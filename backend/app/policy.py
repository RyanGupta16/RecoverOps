"""Policy gate v2: ordered, named, cited.

Same semantics as v1 — rules evaluate in order, the first BLOCK stops the
action, and every rule after it is recorded N/A rather than skipped, so a trace
always shows why a rule did not fire. Two structural changes:

1. Three message classes, not two. TRAI's TCCCPR Second Amendment (12 Feb 2025)
   defines *transactional* (cl. 2(bt): in response to a customer-initiated
   transaction, within thirty minutes, no consent needed), *service*
   (cl. 2(bh): information about a product the customer holds — no explicit
   consent, no time band) and *promotional* (cl. 2(au): anything with an offer
   in it, and "if promotional content is mixed with any type of Transactional
   or Service Message, such Message shall be treated as a Promotional
   Message"). A failed recurring charge is merchant-initiated, so a plain
   reminder about it is a service message — the v1 gate over-blocked these
   after 30 minutes. An incentive makes the whole message promotional.

2. Rules carry the regulation they enforce, and product policy is labelled as
   product policy. The catalogue is data (RULES) so the API can serve it and
   the console can show the citation next to the verdict.

Order is deliberate: hard safety stops first (fraud, hard declines, disputes,
merchant-side faults), then classification, then class-dependent compliance,
then instrument-specific ceilings, then economics, then the human ladder.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .leaks import LeakEvent
from .merchant import MerchantConfig

PASS, BLOCK, NA = "PASS", "BLOCK", "N/A"

CONTACT_ACTIONS = {"payment_link_sms", "payment_link_whatsapp", "card_update_request", "incentive_link"}
RETRY_ACTIONS = {"silent_retry", "retry_scheduled"}
INCENTIVE_ACTIONS = {"incentive_link"}

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
NO_MESSAGE = "No outbound message in this action."
NO_CHARGE = "No charge attempt in this action."


@dataclass(frozen=True)
class Rule:
    id: str
    category: str  # compliance | frequency | risk | economics
    description: str
    citation: str | None = None  # regulation; None means product policy
    basis: str = "product policy"


# The catalogue, in evaluation order. ESCALATE_UNRESOLVED is appended by the engine.
RULES: list[Rule] = [
    Rule("NO_RETRY_ON_FRAUD", "risk", "Hard block on suspected-fraud and risk-hold reason codes. Never retried, never contacted.", None, "card-scheme rules; product policy"),
    Rule("HARD_DECLINE_NO_RETRY", "risk", "A hard decline for the instrument — expired, blocked, closed, revoked — is never retried on the same instrument. Only an instrument change can fix it.", "Visa reattempt rules", "Visa Category-1 declines are never reattempted; Mastercard equivalent"),
    Rule("STOP_ON_DISPUTE", "risk", "No action of any kind while a dispute or chargeback is open on the counterparty.", None, "fairness; product policy"),
    Rule("MERCHANT_SIDE_NO_CONTACT", "risk", "Merchant-side failures (error_source = business) are not the customer's problem: zero customer contact, routed to merchant operations.", None, "Razorpay error taxonomy; product policy"),
    Rule("MSG_CLASS_TCCCPR_2025", "compliance", "Classify the message: transactional only if the customer initiated the transaction and it is within 30 minutes; service if it informs about a product the customer holds; promotional otherwise.", "TCCCPR 2025 cl. 2(bt), 2(bh), 2(au)", "TRAI TCCCPR Second Amendment, 12 Feb 2025"),
    Rule("MIXED_CONTENT_IS_PROMOTIONAL", "compliance", "Any incentive, discount or win-back offer reclassifies the whole message as promotional and re-gates it.", "TCCCPR 2025 cl. 2(au) proviso", "TRAI TCCCPR Second Amendment, 12 Feb 2025"),
    Rule("QUIET_HOURS_2100_0900_IST", "compliance", "No promotional-class outreach outside 09:00–21:00 IST.", "TCCCPR preference bands", "TRAI TCCCPR 2018 default time band"),
    Rule("DUES_CONTACT_WINDOW_0800_1900", "compliance", "Anything that reads as dues collection — overdue receivables, broken-promise follow-ups — is contacted only 08:00–19:00 IST, on any channel.", "RBI recovery-agent norms", "RBI circular on recovery agents, Aug 2022; reinforced 2024–25"),
    Rule("DND_SCRUB_PROMOTIONAL", "compliance", "Promotional-class messages are scrubbed against the preference register and need a consent record; blocked otherwise.", "TCCCPR", "TRAI TCCCPR Preference and Consent Registers"),
    Rule("CONSENT_PURPOSE_MATCH", "compliance", "Promotional outreach needs a consent record whose purpose covers it; explicit consent given to complete a purchase expires after seven days.", "DPDP Rules 2025 · TCCCPR 2025 cl. 2(bh)", "DPDP Act 2023 (Rules notified 14 Nov 2025); TCCCPR seven-day consent"),
    Rule("MANDATE_ATTEMPT_CAP_4", "frequency", "A UPI Autopay or e-mandate cycle allows one execution plus three retries. Four attempts used means no more this cycle.", "NPCI UPI Autopay", "NPCI mandate execution rules"),
    Rule("AFA_THRESHOLD", "compliance", "A recurring debit above the AFA-free ceiling (₹15,000; ₹1,00,000 for mutual funds, insurance and card bills) cannot be retried silently — it needs the customer's authentication.", "RBI E-Mandate Framework 2026", "RBI Digital Payments — E-Mandate Framework, 2026"),
    Rule("MAX_RETRY_3_PER_CYCLE", "frequency", "Max 3 charge attempts per billing cycle on cards, mirroring Razorpay's own T+3 behaviour.", None, "mirrors Razorpay subscription retries; product policy"),
    Rule("NETWORK_RETRY_CAP_30D", "frequency", "Respect the card network's rolling 30-day reattempt ceiling: 15 on Visa, 10 on Mastercard.", "Visa / Mastercard", "Visa excessive-reattempt rule; Mastercard reattempt limits"),
    Rule("SILENT_FIRST", "frequency", "Attempt at least one silent retry before any customer contact — unless the decline is hard and a silent retry cannot succeed.", None, "product policy"),
    Rule("MAX_CONTACTS_2_PER_7D", "frequency", "No more than 2 customer contacts in any rolling 7 days.", None, "product policy"),
    Rule("BACKOFF_ON_ISSUER_DOWN", "risk", "For bank, gateway or network-side failures: exponential backoff, zero customer contact.", None, "product policy"),
    Rule("DISCOUNT_CAP_5PCT", "economics", "Any incentive capped at 5% of order value, with a cumulative batch budget ceiling.", None, "product policy"),
    Rule("STOP_ON_NEGATIVE_UPLIFT", "economics", "If estimated uplift is at or below the threshold, or the contact's expected net value is negative, take no action. Sleeping-dog protection.", None, "product policy"),
    Rule("APPROVAL_ABOVE_THRESHOLD", "economics", "Outreach on a leak above the merchant's approval threshold waits for a human; silent retries proceed.", None, "human-in-the-loop; product policy"),
    Rule("ESCALATE_UNRESOLVED", "economics", "After exhausting the ladder, route to the human queue with a structured reason.", None, "product policy"),
]

RULES_BY_ID = {r.id: r for r in RULES}


def rules_public() -> list[dict]:
    return [
        {"id": r.id, "category": r.category, "description": r.description, "citation": r.citation, "basis": r.basis}
        for r in RULES
    ]


@dataclass
class GateOutcome:
    gate: list[dict] = field(default_factory=list)
    blocked: bool = False
    message_class: str | None = "service"
    blocked_by: str | None = None
    denied_action: str | None = None
    denied_by: str | None = None
    escalated: bool = False


def preferred_contact_action(ev: LeakEvent) -> str:
    """The outreach the engine would like to make for this leak, before the gate."""
    if ev.reason_code in ("CARD_EXPIRED", "INVALID_AUTH_DATA"):
        return "card_update_request"
    if ev.reason_code == "INSTRUMENT_BLOCKED":
        return "card_update_request" if ev.method == "card" else "payment_link_sms"
    if ev.reason_code == "MANDATE_REVOKED":
        return "incentive_link"
    if ev.method in ("upi_autopay", "upi") or ev.reason_code == "CUSTOMER_CANCELLED":
        return "payment_link_whatsapp"
    return "payment_link_sms"


def classify_message(ev: LeakEvent, action: str) -> tuple[str | None, str]:
    """TCCCPR 2025 three-class model. Returns (class, note); class is None when
    the action carries no outbound message."""
    if action not in CONTACT_ACTIONS:
        return None, NO_MESSAGE
    if action in INCENTIVE_ACTIONS:
        return "promotional", "Message carries an incentive — promotional-class by content, regardless of timing (cl. 2(au))."
    if ev.customer_initiated and ev.minutes_since_failure <= 30:
        return "transactional", f"{ev.minutes_since_failure} min since a customer-initiated transaction — transactional (cl. 2(bt)); no consent needed."
    if ev.has_relationship:
        if ev.customer_initiated:
            return "service", f"{ev.minutes_since_failure} min since the transaction — outside the 30-minute transactional window; informational message about a product the customer holds, so service-class (cl. 2(bh)(i))."
        return "service", "Recurring charge is merchant-initiated, so never transactional; an informational notice about a product the customer holds is service-class (cl. 2(bh)(i)) — no explicit consent, no time band."
    return "promotional", "No existing relationship to inform about — promotional-class; needs consent and a time band."


def evaluate_gate(
    ev: LeakEvent,
    intended_action: str,
    agent: str,
    uplift_hat: float,
    merchant: MerchantConfig,
    net_value_paise: float | None = None,
) -> GateOutcome:
    out = GateOutcome()

    def push(rule: str, verdict: str, note: str) -> None:
        r = RULES_BY_ID[rule]
        out.gate.append({"ruleId": rule, "verdict": verdict, "note": note, "citation": r.citation})

    def block_once(rule: str, note: str) -> None:
        if not out.blocked:
            push(rule, BLOCK, note)
            out.blocked = True
        else:
            push(rule, NA, NOT_EVALUATED)

    is_contact = intended_action in CONTACT_ACTIONS
    is_retry = intended_action in RETRY_ACTIONS
    is_incentive = intended_action in INCENTIVE_ACTIONS
    raw = f" ({ev.raw_reason})" if ev.raw_reason else ""

    # 1. Fraud is a hard stop, ahead of everything else.
    if ev.reason_code == "SUSPECTED_FRAUD":
        block_once("NO_RETRY_ON_FRAUD", f"Reason code is a suspected-fraud or risk hold{raw}. Never retried, never contacted.")
    else:
        push("NO_RETRY_ON_FRAUD", PASS, "Reason code is not a fraud hold.")

    # 2. Hard declines: the instrument itself is the problem.
    if out.blocked:
        push("HARD_DECLINE_NO_RETRY", NA, NOT_EVALUATED)
    elif not is_retry:
        push("HARD_DECLINE_NO_RETRY", NA, NO_CHARGE if not ev.hard_decline else "Hard decline — this action changes the instrument rather than retrying it.")
    elif ev.hard_decline:
        block_once("HARD_DECLINE_NO_RETRY", f"{ev.reason_label}{raw} is a hard decline for this instrument. Reattempting it cannot succeed and counts against the network's excessive-reattempt rule.")
    else:
        push("HARD_DECLINE_NO_RETRY", PASS, "Soft decline — a retry can succeed.")

    # 3. Open disputes freeze everything.
    if out.blocked:
        push("STOP_ON_DISPUTE", NA, NOT_EVALUATED)
    elif ev.dispute_open:
        block_once("STOP_ON_DISPUTE", "A dispute or chargeback is open on this counterparty. No retry, no contact until it closes.")
    else:
        push("STOP_ON_DISPUTE", PASS, "No open dispute on this counterparty.")

    # 4. Merchant-side faults are ours to fix, not theirs to hear about.
    if out.blocked:
        push("MERCHANT_SIDE_NO_CONTACT", NA, NOT_EVALUATED)
    elif not ev.merchant_side:
        push("MERCHANT_SIDE_NO_CONTACT", PASS, "Failure is not merchant-side.")
    elif is_contact:
        block_once("MERCHANT_SIDE_NO_CONTACT", f"error_source is the merchant{raw}. The customer cannot fix a merchant configuration error; contacting them blames them for it. Routed to merchant operations.")
    else:
        push("MERCHANT_SIDE_NO_CONTACT", PASS, f"Merchant-side failure{raw} — silent retry allowed once the configuration is fixed; flagged for merchant operations.")

    # 5. Message classification decides which compliance rules apply.
    msg_class, class_note = classify_message(ev, intended_action)
    out.message_class = msg_class
    if out.blocked:
        push("MSG_CLASS_TCCCPR_2025", NA, NOT_EVALUATED)
    elif msg_class is None:
        push("MSG_CLASS_TCCCPR_2025", NA, NO_MESSAGE)
    else:
        push("MSG_CLASS_TCCCPR_2025", PASS, f"Classified {msg_class}. {class_note}")

    # 6. Mixed content → promotional (a reclassification, recorded as PASS with the consequence).
    if out.blocked:
        push("MIXED_CONTENT_IS_PROMOTIONAL", NA, NOT_EVALUATED)
    elif not is_contact:
        push("MIXED_CONTENT_IS_PROMOTIONAL", NA, NO_MESSAGE)
    elif is_incentive:
        push("MIXED_CONTENT_IS_PROMOTIONAL", PASS, "Incentive present — the whole message is promotional-class and the promotional rules below apply.")
    else:
        push("MIXED_CONTENT_IS_PROMOTIONAL", NA, "No promotional content in this message.")

    promotional = is_contact and msg_class == "promotional"

    # 7. Quiet hours, promotional class only.
    if out.blocked:
        push("QUIET_HOURS_2100_0900_IST", NA, NOT_EVALUATED)
    elif not promotional:
        push("QUIET_HOURS_2100_0900_IST", NA, f"{msg_class.capitalize()} class — the promotional time band does not apply." if is_contact else NO_MESSAGE)
    elif not merchant.promotional_window.contains(ev.local_hour_ist):
        block_once("QUIET_HOURS_2100_0900_IST", f"Local time {ev.local_hour_ist:02d}:00 IST is outside {merchant.promotional_window.label()} for promotional-class outreach.")
    else:
        push("QUIET_HOURS_2100_0900_IST", PASS, f"Local time {ev.local_hour_ist:02d}:00 IST is inside {merchant.promotional_window.label()}.")

    # 8. Dues-type leaks get the stricter RBI window on every channel and class.
    if out.blocked:
        push("DUES_CONTACT_WINDOW_0800_1900", NA, NOT_EVALUATED)
    elif not is_contact:
        push("DUES_CONTACT_WINDOW_0800_1900", NA, NO_MESSAGE)
    elif not ev.is_dues:
        push("DUES_CONTACT_WINDOW_0800_1900", NA, "Not a dues-type leak — the recovery-agent window does not apply.")
    elif not merchant.dues_window.contains(ev.local_hour_ist):
        block_once("DUES_CONTACT_WINDOW_0800_1900", f"Local time {ev.local_hour_ist:02d}:00 IST is outside {merchant.dues_window.label()} for dues collection contact.")
    else:
        push("DUES_CONTACT_WINDOW_0800_1900", PASS, f"Local time {ev.local_hour_ist:02d}:00 IST is inside {merchant.dues_window.label()}.")

    # 9. Preference register + consent presence, promotional class only.
    if out.blocked:
        push("DND_SCRUB_PROMOTIONAL", NA, NOT_EVALUATED)
    elif not promotional:
        push("DND_SCRUB_PROMOTIONAL", NA, f"{msg_class.capitalize()} class — preference scrub does not apply." if is_contact else NO_MESSAGE)
    elif not ev.consent_on_file or ev.dnd_registered:
        block_once(
            "DND_SCRUB_PROMOTIONAL",
            "No consent record on file for promotional-class messaging." if not ev.consent_on_file else "Number is on the preference (DND) register and the message is promotional-class.",
        )
    else:
        push("DND_SCRUB_PROMOTIONAL", PASS, "Consent record present, not DND-registered.")

    # 10. Consent validity and purpose.
    if out.blocked:
        push("CONSENT_PURPOSE_MATCH", NA, NOT_EVALUATED)
    elif not promotional:
        push("CONSENT_PURPOSE_MATCH", NA, "Service or transactional class — consent is inferred for the duration of the relationship." if is_contact else NO_MESSAGE)
    elif ev.consent_granted_days_ago is not None and ev.consent_granted_days_ago > 7 and ev.extras.get("consent_purpose", "marketing") == "transaction_completion":
        block_once("CONSENT_PURPOSE_MATCH", f"Consent was given {ev.consent_granted_days_ago} days ago to complete a transaction; that purpose expires after seven days.")
    else:
        push("CONSENT_PURPOSE_MATCH", PASS, "Consent purpose covers promotional outreach" + (f", granted {ev.consent_granted_days_ago} days ago." if ev.consent_granted_days_ago is not None else "."))

    # 11. NPCI mandate attempt cap.
    if out.blocked:
        push("MANDATE_ATTEMPT_CAP_4", NA, NOT_EVALUATED)
    elif not is_retry:
        push("MANDATE_ATTEMPT_CAP_4", NA, NO_CHARGE)
    elif not ev.is_mandate:
        push("MANDATE_ATTEMPT_CAP_4", NA, "Not a UPI Autopay or e-mandate debit.")
    elif ev.attempts_this_cycle >= merchant.max_mandate_attempts:
        block_once("MANDATE_ATTEMPT_CAP_4", f"{ev.attempts_this_cycle} of {merchant.max_mandate_attempts} mandate executions used this cycle (one execution plus three retries).")
    else:
        push("MANDATE_ATTEMPT_CAP_4", PASS, f"{ev.attempts_this_cycle} of {merchant.max_mandate_attempts} mandate executions used this cycle.")

    # 12. AFA ceiling on silent recurring debits.
    if out.blocked:
        push("AFA_THRESHOLD", NA, NOT_EVALUATED)
    elif not is_retry or not ev.is_recurring:
        push("AFA_THRESHOLD", NA, NO_CHARGE if not is_retry else "Not a recurring debit.")
    elif ev.amount_paise > merchant.afa_limit_paise:
        block_once("AFA_THRESHOLD", f"₹{ev.amount_paise / 100:,.2f} exceeds the ₹{merchant.afa_limit_paise / 100:,.0f} AFA-free ceiling for this category — a silent debit is not permitted; the customer must authenticate.")
    else:
        push("AFA_THRESHOLD", PASS, f"₹{ev.amount_paise / 100:,.2f} is within the ₹{merchant.afa_limit_paise / 100:,.0f} AFA-free ceiling.")

    # 13. Card charge-attempt ceiling per cycle.
    if out.blocked:
        push("MAX_RETRY_3_PER_CYCLE", NA, NOT_EVALUATED)
    elif not is_retry:
        push("MAX_RETRY_3_PER_CYCLE", NA, NO_CHARGE)
    elif ev.is_mandate:
        push("MAX_RETRY_3_PER_CYCLE", NA, "Mandate debit — the NPCI attempt cap applies instead.")
    elif ev.attempts_this_cycle >= merchant.max_retries_per_cycle_cards:
        block_once("MAX_RETRY_3_PER_CYCLE", f"{ev.attempts_this_cycle} attempts already made this billing cycle.")
    else:
        push("MAX_RETRY_3_PER_CYCLE", PASS, f"{ev.attempts_this_cycle} of {merchant.max_retries_per_cycle_cards} attempts used this cycle.")

    # 14. Network ceiling, network-aware.
    if out.blocked:
        push("NETWORK_RETRY_CAP_30D", NA, NOT_EVALUATED)
    elif not is_retry:
        push("NETWORK_RETRY_CAP_30D", NA, NO_CHARGE)
    elif ev.method != "card":
        push("NETWORK_RETRY_CAP_30D", NA, "No card network on this instrument.")
    else:
        cap = merchant.network_cap(ev.network)
        if ev.retries_30d >= cap:
            block_once("NETWORK_RETRY_CAP_30D", f"{ev.retries_30d} attempts in the rolling 30-day window — {ev.network or 'network'} ceiling of {cap} reached; further reattempts incur excessive-reattempt fees.")
        else:
            push("NETWORK_RETRY_CAP_30D", PASS, f"{ev.retries_30d} of {cap} attempts in the rolling 30-day window ({ev.network or 'network'}).")

    # 15. Silent-first ladder, with the hard-decline exception.
    if out.blocked:
        push("SILENT_FIRST", NA, NOT_EVALUATED)
    elif not is_contact:
        push("SILENT_FIRST", NA, "Action is a retry — this rule gates outreach, not retries.")
    elif ev.attempts_this_cycle == 0 and not ev.hard_decline:
        block_once("SILENT_FIRST", "No silent retry attempted yet this cycle. Outreach deferred until one has run.")
    elif ev.attempts_this_cycle == 0:
        push("SILENT_FIRST", PASS, "No silent retry yet, but the decline is hard — a retry cannot succeed, so outreach is allowed first.")
    else:
        push("SILENT_FIRST", PASS, f"{ev.attempts_this_cycle} silent attempt(s) already made this cycle.")

    # 16. Contact frequency ceiling.
    if out.blocked:
        push("MAX_CONTACTS_2_PER_7D", NA, NOT_EVALUATED)
    elif not is_contact:
        push("MAX_CONTACTS_2_PER_7D", NA, NO_MESSAGE)
    elif ev.contacts_last_7d >= merchant.max_contacts_7d:
        block_once("MAX_CONTACTS_2_PER_7D", f"{ev.contacts_last_7d} contacts already made in the rolling 7-day window.")
    else:
        push("MAX_CONTACTS_2_PER_7D", PASS, f"{ev.contacts_last_7d} of {merchant.max_contacts_7d} contacts used in the rolling 7-day window.")

    # 17. Issuer-side failures are not the customer's problem.
    if out.blocked:
        push("BACKOFF_ON_ISSUER_DOWN", NA, NOT_EVALUATED)
    elif ev.failure_side != "issuer":
        push("BACKOFF_ON_ISSUER_DOWN", PASS, f"Failure originates {ev.failure_side}-side, not issuer-side.")
    elif is_contact:
        block_once("BACKOFF_ON_ISSUER_DOWN", "Bank, gateway or network-side failure. Exponential backoff only, zero customer contact.")
    else:
        push("BACKOFF_ON_ISSUER_DOWN", PASS, "Issuer-side failure — backoff schedule applied to the retry.")

    # 18. Incentive ceiling.
    if not is_incentive:
        push("DISCOUNT_CAP_5PCT", NA, "No incentive attached to this action.")
    elif out.blocked:
        push("DISCOUNT_CAP_5PCT", NA, NOT_EVALUATED)
    else:
        push("DISCOUNT_CAP_5PCT", PASS, f"Incentive held at {merchant.discount_cap_pct}% of ₹{ev.amount_paise / 100:.2f}, inside the batch budget ceiling.")

    # 19. Sleeping-dog protection. Agent A has no uplift estimate — unevaluable.
    if agent != "B":
        push("STOP_ON_NEGATIVE_UPLIFT", NA, "Baseline policy has no uplift estimate. Rule is unevaluable.")
    elif out.blocked:
        push("STOP_ON_NEGATIVE_UPLIFT", NA, NOT_EVALUATED)
    elif not is_contact:
        push("STOP_ON_NEGATIVE_UPLIFT", NA, "No outreach in this action.")
    elif uplift_hat <= merchant.uplift_threshold:
        block_once("STOP_ON_NEGATIVE_UPLIFT", f"Estimated uplift {'+' if uplift_hat >= 0 else ''}{uplift_hat:.3f} is at or below the {merchant.uplift_threshold} threshold.")
    elif net_value_paise is not None and net_value_paise <= 0:
        block_once("STOP_ON_NEGATIVE_UPLIFT", f"Uplift +{uplift_hat:.3f} clears the threshold but the contact's expected net value is ₹{net_value_paise / 100:,.2f} after churn risk and channel cost.")
    else:
        push("STOP_ON_NEGATIVE_UPLIFT", PASS, f"Estimated uplift +{uplift_hat:.3f} clears the {merchant.uplift_threshold} threshold" + (f"; expected net value ₹{net_value_paise / 100:,.2f}." if net_value_paise is not None else "."))

    # 20. Human approval above the merchant's threshold.
    if out.blocked:
        push("APPROVAL_ABOVE_THRESHOLD", NA, NOT_EVALUATED)
    elif not is_contact:
        push("APPROVAL_ABOVE_THRESHOLD", NA, NO_MESSAGE)
    elif ev.amount_paise > merchant.approval_threshold_paise:
        block_once("APPROVAL_ABOVE_THRESHOLD", f"₹{ev.amount_paise / 100:,.2f} exceeds the ₹{merchant.approval_threshold_paise / 100:,.0f} approval threshold. Outreach waits for a human; the silent path proceeds.")
    else:
        push("APPROVAL_ABOVE_THRESHOLD", PASS, f"₹{ev.amount_paise / 100:,.2f} is within the ₹{merchant.approval_threshold_paise / 100:,.0f} no-approval ceiling.")

    out.blocked_by = next((g["ruleId"] for g in out.gate if g["verdict"] == BLOCK), None)
    return out
