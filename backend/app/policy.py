"""Policy gate: Python port of the gate in scripts/generate-sample-batch.mjs.

Same twelve rule ids, same evaluation order, same first-BLOCK-stops semantics.
Rules after a block are recorded N/A rather than skipped, so every trace shows
why a rule did not fire. The two implementations are kept behaviourally
identical so a live batch and the bundled demo batch are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sim import CONFIG, Event

PASS, BLOCK, NA = "PASS", "BLOCK", "N/A"

CONTACT_ACTIONS = {"payment_link_sms", "payment_link_whatsapp", "card_update_request", "incentive_link"}
RETRY_ACTIONS = {"silent_retry", "retry_scheduled"}

ACTION_LABELS = {
    "silent_retry": "Silent retry",
    "retry_scheduled": "Retry scheduled",
    "payment_link_sms": "Payment link · SMS",
    "payment_link_whatsapp": "Payment link · WhatsApp",
    "card_update_request": "Card update request",
    "incentive_link": "Incentive link",
    "escalate": "Escalated to human queue",
    "no_action": "No action",
}

NOT_EVALUATED = "Not evaluated — an earlier rule already blocked this action."


@dataclass
class GateOutcome:
    gate: list[dict] = field(default_factory=list)
    blocked: bool = False
    message_class: str = "transactional"
    blocked_by: str | None = None
    denied_action: str | None = None
    denied_by: str | None = None
    escalated: bool = False


def preferred_contact_action(ev: Event) -> str:
    if ev.reason_code in ("CARD_EXPIRED", "INVALID_AUTH_DATA"):
        return "card_update_request"
    if ev.reason_code == "MANDATE_REVOKED":
        return "incentive_link"
    if ev.method == "upi_autopay":
        return "payment_link_whatsapp"
    return "payment_link_sms"


def evaluate_gate(ev: Event, intended_action: str, agent: str, uplift_hat: float) -> GateOutcome:
    out = GateOutcome()
    push = lambda rule, verdict, note: out.gate.append({"ruleId": rule, "verdict": verdict, "note": note})  # noqa: E731

    def block_once(rule: str, note: str) -> None:
        if not out.blocked:
            push(rule, BLOCK, note)
            out.blocked = True
        else:
            push(rule, NA, NOT_EVALUATED)

    is_contact = intended_action in CONTACT_ACTIONS
    is_retry = intended_action in RETRY_ACTIONS

    # 1. Fraud is a hard stop, ahead of everything else.
    if ev.reason_code == "SUSPECTED_FRAUD":
        block_once("NO_RETRY_ON_FRAUD", "Reason code is a suspected-fraud hold. Never retried, never contacted.")
    else:
        push("NO_RETRY_ON_FRAUD", PASS, "Reason code is not a fraud hold.")

    # 2. Message classification decides which of the next two rules apply.
    transactional = ev.minutes_since_failure <= 30
    out.message_class = "transactional" if transactional else "promotional"
    if out.blocked:
        push("MSG_CLASS_TRANSACTIONAL_30MIN", NA, NOT_EVALUATED)
    elif not is_contact:
        push("MSG_CLASS_TRANSACTIONAL_30MIN", NA, "No outbound message in this action.")
    else:
        push(
            "MSG_CLASS_TRANSACTIONAL_30MIN",
            PASS,
            f"{ev.minutes_since_failure} min since failure — inside the 30-minute window, classified transactional."
            if transactional
            else f"{ev.minutes_since_failure} min since failure — outside the 30-minute window, reclassified promotional and re-gated.",
        )

    # 3. Quiet hours, promotional class only.
    if out.blocked:
        push("QUIET_HOURS_2100_0900_IST", NA, NOT_EVALUATED)
    elif not is_contact or transactional:
        push(
            "QUIET_HOURS_2100_0900_IST",
            NA,
            "Transactional class — quiet hours do not apply." if is_contact else "No outbound message in this action.",
        )
    elif ev.local_hour_ist >= 21 or ev.local_hour_ist < 9:
        block_once(
            "QUIET_HOURS_2100_0900_IST",
            f"Local time {ev.local_hour_ist:02d}:00 IST is outside 09:00–21:00 for promotional-class outreach.",
        )
    else:
        push("QUIET_HOURS_2100_0900_IST", PASS, f"Local time {ev.local_hour_ist:02d}:00 IST is inside 09:00–21:00.")

    # 4. Consent + DND, promotional class only.
    if out.blocked:
        push("DND_SCRUB_PROMOTIONAL", NA, NOT_EVALUATED)
    elif not is_contact or transactional:
        push(
            "DND_SCRUB_PROMOTIONAL",
            NA,
            "Transactional class — consent scrub does not apply." if is_contact else "No outbound message in this action.",
        )
    elif not ev.consent_on_file or ev.dnd_registered:
        block_once(
            "DND_SCRUB_PROMOTIONAL",
            "No consent record on file for promotional-class messaging."
            if not ev.consent_on_file
            else "Number is on the DND register and the message is promotional-class.",
        )
    else:
        push("DND_SCRUB_PROMOTIONAL", PASS, "Consent record present, not DND-registered.")

    # 5. Charge-attempt ceilings.
    if out.blocked:
        push("MAX_RETRY_3_PER_CYCLE", NA, NOT_EVALUATED)
    elif not is_retry:
        push("MAX_RETRY_3_PER_CYCLE", NA, "No charge attempt in this action.")
    elif ev.attempts_this_cycle >= 3:
        block_once("MAX_RETRY_3_PER_CYCLE", f"{ev.attempts_this_cycle} attempts already made this billing cycle.")
    else:
        push("MAX_RETRY_3_PER_CYCLE", PASS, f"{ev.attempts_this_cycle} of 3 attempts used this cycle.")

    if out.blocked:
        push("NETWORK_RETRY_CAP_30D", NA, NOT_EVALUATED)
    elif not is_retry:
        push("NETWORK_RETRY_CAP_30D", NA, "No charge attempt in this action.")
    elif ev.retries_30d >= 14:
        block_once("NETWORK_RETRY_CAP_30D", f"{ev.retries_30d} attempts in the rolling 30-day window — network ceiling reached.")
    else:
        push("NETWORK_RETRY_CAP_30D", PASS, f"{ev.retries_30d} attempts in the rolling 30-day window.")

    # 6. Silent-first ladder.
    if out.blocked:
        push("SILENT_FIRST", NA, NOT_EVALUATED)
    elif not is_contact:
        push("SILENT_FIRST", NA, "Action is a silent retry — this rule gates outreach, not retries.")
    elif ev.attempts_this_cycle == 0:
        block_once("SILENT_FIRST", "No silent retry attempted yet this cycle. Outreach deferred until one has run.")
    else:
        push("SILENT_FIRST", PASS, f"{ev.attempts_this_cycle} silent attempt(s) already made this cycle.")

    # 7. Contact frequency ceiling.
    if out.blocked:
        push("MAX_CONTACTS_2_PER_7D", NA, NOT_EVALUATED)
    elif not is_contact:
        push("MAX_CONTACTS_2_PER_7D", NA, "No outbound message in this action.")
    elif ev.contacts_last_7d >= 2:
        block_once("MAX_CONTACTS_2_PER_7D", f"{ev.contacts_last_7d} contacts already made in the rolling 7-day window.")
    else:
        push("MAX_CONTACTS_2_PER_7D", PASS, f"{ev.contacts_last_7d} of 2 contacts used in the rolling 7-day window.")

    # 8. Issuer-side failures are not the customer's problem.
    if out.blocked:
        push("BACKOFF_ON_ISSUER_DOWN", NA, NOT_EVALUATED)
    elif ev.failure_side != "issuer":
        push("BACKOFF_ON_ISSUER_DOWN", PASS, "Failure originates customer-side, not issuer-side.")
    elif is_contact:
        block_once(
            "BACKOFF_ON_ISSUER_DOWN",
            "Bank, gateway or network-side failure. Exponential backoff only, zero customer contact.",
        )
    else:
        push("BACKOFF_ON_ISSUER_DOWN", PASS, "Issuer-side failure — backoff schedule applied to the retry.")

    # 9. Incentive ceiling.
    if intended_action != "incentive_link":
        push("DISCOUNT_CAP_5PCT", NA, "No incentive attached to this action.")
    elif out.blocked:
        push("DISCOUNT_CAP_5PCT", NA, NOT_EVALUATED)
    else:
        push(
            "DISCOUNT_CAP_5PCT",
            PASS,
            f"Incentive held at 5% of ₹{ev.amount_paise / 100:.2f}, inside the batch budget ceiling.",
        )

    # 10. Sleeping-dog protection. Agent A has no uplift estimate — unevaluable.
    if agent != "B":
        push("STOP_ON_NEGATIVE_UPLIFT", NA, "Baseline policy has no uplift estimate. Rule is unevaluable.")
    elif out.blocked:
        push("STOP_ON_NEGATIVE_UPLIFT", NA, NOT_EVALUATED)
    elif not is_contact:
        push("STOP_ON_NEGATIVE_UPLIFT", NA, "No outreach in this action.")
    elif uplift_hat <= CONFIG["upliftThreshold"]:
        block_once(
            "STOP_ON_NEGATIVE_UPLIFT",
            f"Estimated uplift {'+' if uplift_hat >= 0 else ''}{uplift_hat:.3f} is at or below the {CONFIG['upliftThreshold']} threshold.",
        )
    else:
        push(
            "STOP_ON_NEGATIVE_UPLIFT",
            PASS,
            f"Estimated uplift +{uplift_hat:.3f} clears the {CONFIG['upliftThreshold']} threshold.",
        )

    out.blocked_by = next((g["ruleId"] for g in out.gate if g["verdict"] == BLOCK), None)
    return out
