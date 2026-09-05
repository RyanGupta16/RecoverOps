"""Adversarial tests: deliberate attempts to make the gate permit something it must not.

Every test here is written from the attacker's side. The question is not "does
the rule work on a clean case" — test_policy.py covers that — but "can I
construct an input that slips a prohibited action past the gate?"

Each one names the regulation it is protecting and the harm if it failed. If a
test in this file ever goes red, the system has become able to do something a
regulator, a card network, or a customer would object to.
"""

from __future__ import annotations

import itertools

import pytest

from app.leaks import LeakEvent
from app.merchant import MerchantConfig
from app.policy import ACTION_LABELS, CONTACT_ACTIONS, RULES, evaluate_gate
from app.taxonomy import FAMILIES

ALL_ACTIONS = sorted(set(ACTION_LABELS) - {"escalate", "no_action"})


@pytest.fixture
def merchant():
    return MerchantConfig()


def leak(**over) -> LeakEvent:
    """A leak that passes every rule, so any block in a test comes from the
    single field that test changed."""
    base = dict(
        event_id="evt_adv",
        kind="subscription_failure",
        amount_paise=49900,
        method="card",
        network="Visa",
        issuer="HDFC",
        minutes_since_failure=120,
        local_hour_ist=14,
        attempts_this_cycle=1,
        contacts_last_7d=0,
        retries_30d=2,
        consent_on_file=True,
        consent_granted_days_ago=2,
        dnd_registered=False,
        has_relationship=True,
        customer_initiated=False,
        reason_code="INSUFFICIENT_FUNDS",
        reason_label="Insufficient balance",
        failure_side="customer",
        extras={},
    )
    base.update(over)
    return LeakEvent(**base)


def blocked_by(ev, action, merchant, agent="B", tau=0.5, net=100000):
    return evaluate_gate(ev, action, agent, tau, merchant, net_value_paise=net).blocked_by


# ------------------------------------------------------- hard safety stops


def test_no_action_whatsoever_survives_a_fraud_hold(merchant):
    """Card-scheme rules prohibit merchant-initiated retries on fraud-coded
    declines; contacting the customer is worse. Nothing may get through."""
    ev = leak(reason_code="SUSPECTED_FRAUD", reason_label="Suspected fraud hold", failure_side="risk")
    for action in ALL_ACTIONS:
        assert blocked_by(ev, action, merchant) == "NO_RETRY_ON_FRAUD", f"{action} escaped the fraud stop"


def test_a_dispute_freezes_every_channel(merchant):
    """Chasing a disputed charge is how a supplier relationship ends and how a
    complaint gets filed."""
    ev = leak(dispute_open=True)
    for action in ALL_ACTIONS:
        assert blocked_by(ev, action, merchant) == "STOP_ON_DISPUTE", f"{action} escaped the dispute freeze"


def test_a_live_promise_stops_even_silent_machinery(merchant):
    """RBI fair-practice: once a date is agreed, contact before it is
    harassment. A silent retry is still an action on that account."""
    ev = leak(promise_hold={"amountPaise": 49900, "dueAt": "2099-01-01", "capturedVia": "voice"})
    for action in ALL_ACTIONS:
        assert blocked_by(ev, action, merchant) == "PTP_ACTIVE_HOLD", f"{action} escaped the promise hold"


@pytest.mark.parametrize("family", [c for c, f in FAMILIES.items() if f.hard_decline])
def test_hard_declines_are_never_retried_on_the_same_instrument(family, merchant):
    """Visa treats a reattempt on a Category-1 decline as excessive regardless
    of outcome, and charges for it."""
    fam = FAMILIES[family]
    ev = leak(reason_code=family, reason_label=fam.label, failure_side=fam.side,
              hard_decline=True, retriable=False, attempts_this_cycle=0)
    assert blocked_by(ev, "silent_retry", merchant) in ("NO_RETRY_ON_FRAUD", "HARD_DECLINE_NO_RETRY")


def test_merchant_side_failures_never_reach_the_customer(merchant):
    """error_source = business means we broke it. Messaging the customer bills
    them for our mistake in attention."""
    ev = leak(reason_code="MERCHANT_CONFIG", failure_side="merchant", merchant_side=True,
              raw_reason="payment_method_not_enabled")
    for action in sorted(CONTACT_ACTIONS):
        assert blocked_by(ev, action, merchant) == "MERCHANT_SIDE_NO_CONTACT", f"{action} reached the customer"


# --------------------------------------------------------- TCCCPR pressure


def test_an_incentive_cannot_smuggle_itself_in_under_the_service_class(merchant):
    """TCCCPR 2025 cl. 2(au): promotional content mixed into a service message
    makes the whole message promotional. The classic evasion is to call a
    discount a 'service update' and skip consent and the time band."""
    ev = leak(reason_code="MANDATE_REVOKED", reason_label="Mandate revoked",
              consent_on_file=False, local_hour_ist=23)
    out = evaluate_gate(ev, "incentive_link", "B", 0.5, merchant, net_value_paise=100000)
    assert out.message_class == "promotional", "an incentive must reclassify the message"
    assert out.blocked_by in ("QUIET_HOURS_2100_0900_IST", "DND_SCRUB_PROMOTIONAL")


@pytest.mark.parametrize("hour", [21, 22, 23, 0, 3, 6, 8])
def test_promotional_outreach_cannot_run_outside_the_time_band(hour, merchant):
    ev = leak(reason_code="MANDATE_REVOKED", local_hour_ist=hour, has_relationship=False)
    assert blocked_by(ev, "incentive_link", merchant) is not None, f"{hour}:00 let promotional through"


def test_a_recurring_charge_is_never_transactional_however_recent(merchant):
    """cl. 2(bt) requires the customer to have initiated the transaction. A
    merchant-initiated auto-debit never qualifies, even one minute after it
    fails — claiming otherwise is how consent gets bypassed at scale."""
    for minutes in (0, 1, 5, 29):
        ev = leak(minutes_since_failure=minutes, customer_initiated=False)
        out = evaluate_gate(ev, "payment_link_sms", "B", 0.5, merchant, net_value_paise=100000)
        assert out.message_class == "service", f"{minutes} min claimed transactional"


def test_stale_purchase_consent_expires_at_seven_days(merchant):
    """TCCCPR caps explicit consent taken to complete a transaction at 7 days."""
    ev = leak(reason_code="MANDATE_REVOKED", has_relationship=False,
              consent_granted_days_ago=8, extras={"consent_purpose": "transaction_completion"})
    assert blocked_by(ev, "incentive_link", merchant) == "CONSENT_PURPOSE_MATCH"


def test_dues_collection_respects_the_stricter_rbi_window(merchant):
    """The RBI recovery-agent window (08:00-19:00) is tighter than TCCCPR's and
    covers SMS and WhatsApp, not just calls."""
    for hour in (7, 19, 20, 22):
        ev = leak(kind="receivable_overdue", counterparty_type="business",
                  local_hour_ist=hour, extras={"days_overdue": 30})
        assert blocked_by(ev, "invoice_reminder", merchant) is not None, f"{hour}:00 let dues contact through"


# ---------------------------------------------------------- RBI / NPCI caps


@pytest.mark.parametrize("amount", [15_000_01, 20_000_00, 99_999_00])
def test_large_recurring_debits_cannot_be_taken_silently(amount, merchant):
    """RBI e-mandate framework: above the AFA-free ceiling the customer must
    authenticate. Silently debiting is the exact harm the rule exists for."""
    ev = leak(method="emandate", network=None, amount_paise=amount,
              extras={"scheduled_hour_ist": 9, "pre_debit_notice_hours": 24})
    assert blocked_by(ev, "silent_retry", merchant) == "AFA_THRESHOLD"


def test_the_raised_afa_ceiling_applies_only_to_the_named_categories(merchant):
    """₹1,00,000 is permitted for mutual funds, insurance and card bills — and
    for nothing else. A merchant must not inherit it by mislabelling."""
    ev = leak(method="emandate", network=None, amount_paise=50_000_00,
              extras={"scheduled_hour_ist": 9, "pre_debit_notice_hours": 24})
    assert blocked_by(ev, "silent_retry", MerchantConfig(category="subscription")) == "AFA_THRESHOLD"
    assert blocked_by(ev, "silent_retry", MerchantConfig(category="insurance")) is None


@pytest.mark.parametrize("hours", [0, 1, 12, 23, 23.9])
def test_a_debit_cannot_outrun_its_pre_debit_notice(hours, merchant):
    ev = leak(method="emandate", network=None,
              extras={"scheduled_hour_ist": 9, "pre_debit_notice_hours": hours})
    assert blocked_by(ev, "silent_retry", merchant) == "PRE_DEBIT_NOTICE_24H"


@pytest.mark.parametrize("hour", [10, 11, 12, 17, 18, 20])
def test_mandate_executions_stay_out_of_npci_peak_windows(hour, merchant):
    ev = leak(method="upi_autopay", network=None,
              extras={"scheduled_hour_ist": hour, "pre_debit_notice_hours": 24})
    assert blocked_by(ev, "silent_retry", merchant) == "MANDATE_EXECUTION_WINDOW"


def test_network_reattempt_ceilings_are_per_network(merchant):
    """Mastercard's ceiling is 10, Visa's 15. Applying the looser one to both
    is a fee-generating bug."""
    assert blocked_by(leak(network="MasterCard", retries_30d=10), "silent_retry", merchant) == "NETWORK_RETRY_CAP_30D"
    assert blocked_by(leak(network="Visa", retries_30d=10), "silent_retry", merchant) is None
    assert blocked_by(leak(network="Visa", retries_30d=15), "silent_retry", merchant) == "NETWORK_RETRY_CAP_30D"


# ------------------------------------------------------------ voice-specific


def test_a_promotional_call_cannot_go_out_on_the_service_series(merchant):
    """TRAI: promotional auto-dialled calls must originate on 140, service and
    transactional on 1600. A merchant holding only 1600 must not place a
    promotional call at all."""
    ev = leak(reason_code="MANDATE_REVOKED", amount_paise=299900, has_relationship=False,
              contacts_last_7d=1)
    out = evaluate_gate(ev, "voice_call", "B", 0.5, merchant, net_value_paise=100000)
    assert out.message_class == "promotional"
    assert out.blocked_by == "VOICE_ELIGIBILITY"


def test_voice_is_never_the_first_touch(merchant):
    ev = leak(amount_paise=299900, contacts_last_7d=0)
    assert blocked_by(ev, "voice_call", merchant) == "VOICE_ELIGIBILITY"


def test_recording_disclosure_is_mandatory(merchant):
    ev = leak(amount_paise=299900, contacts_last_7d=1)
    silent = MerchantConfig(voice_recording_disclosure=False)
    assert blocked_by(ev, "voice_call", silent) == "VOICE_ELIGIBILITY"


# -------------------------------------------------- statutory claims (MSMED)


def test_a_statutory_interest_notice_cannot_be_sent_without_the_registration(merchant):
    """Asserting MSMED interest against a buyer when the supplier is not a
    registered MSE is a false statement made for commercial advantage."""
    ev = leak(kind="receivable_overdue", counterparty_type="business", local_hour_ist=11,
              extras={"days_overdue": 200, "mse_supplier": False, "statutory_deadline_days": 45})
    assert blocked_by(ev, "msmed_notice", merchant) == "MSMED_LEVER_AFTER_STATUTORY_WINDOW"


@pytest.mark.parametrize("days,deadline", [(1, 15), (14, 15), (44, 45), (45, 45)])
def test_the_statutory_clock_cannot_be_started_early(days, deadline, merchant):
    ev = leak(kind="receivable_overdue", counterparty_type="business", local_hour_ist=11,
              extras={"days_overdue": days, "mse_supplier": True, "statutory_deadline_days": deadline})
    assert blocked_by(ev, "msmed_notice", merchant) == "MSMED_LEVER_AFTER_STATUTORY_WINDOW"


# ------------------------------------------------------------ the gate itself


def test_economics_can_never_outvote_compliance(merchant):
    """The whole architecture: the gate runs after the engine. However large
    the expected value, a compliance block must still win."""
    ev = leak(reason_code="SUSPECTED_FRAUD", failure_side="risk", amount_paise=100_000_00)
    out = evaluate_gate(ev, "payment_link_sms", "B", 0.99, merchant, net_value_paise=10_000_000_00)
    assert out.blocked_by == "NO_RETRY_ON_FRAUD"


def test_agent_a_is_held_to_the_same_compliance_rules_as_agent_b(merchant):
    """The baseline must not look better by being allowed to break rules — that
    would make the whole comparison dishonest."""
    compliance = [r.id for r in RULES if r.category in ("compliance", "risk", "frequency")]
    for ev, action in [
        (leak(reason_code="SUSPECTED_FRAUD", failure_side="risk"), "payment_link_sms"),
        (leak(dispute_open=True), "silent_retry"),
        (leak(reason_code="MANDATE_REVOKED", local_hour_ist=23, has_relationship=False), "incentive_link"),
        (leak(network="MasterCard", retries_30d=12), "silent_retry"),
    ]:
        a = evaluate_gate(ev, action, "A", 0.5, merchant)
        b = evaluate_gate(ev, action, "B", 0.5, merchant, net_value_paise=100000)
        assert a.blocked_by == b.blocked_by, f"agents diverge on {action}: A={a.blocked_by} B={b.blocked_by}"
        assert a.blocked_by in compliance


def test_every_rule_is_reported_on_every_evaluation(merchant):
    """A rule silently skipped is a rule nobody can audit. Every catalogued
    rule must appear with a verdict on every decision, in a stable order."""
    catalogue = [r.id for r in RULES if r.id != "ESCALATE_UNRESOLVED"]
    for ev, action in itertools.product(
        [leak(), leak(reason_code="SUSPECTED_FRAUD", failure_side="risk"), leak(dispute_open=True),
         leak(kind="receivable_overdue", counterparty_type="business", extras={"days_overdue": 60})],
        ["silent_retry", "payment_link_sms", "incentive_link", "voice_call"],
    ):
        gate = evaluate_gate(ev, action, "B", 0.4, merchant, net_value_paise=50000).gate
        assert [g["ruleId"] for g in gate] == catalogue, f"rule set drifted for {action}"
        assert sum(1 for g in gate if g["verdict"] == "BLOCK") <= 1, "only the first block may stand"


def test_every_block_states_a_reason_a_human_can_act_on(merchant):
    """An audit trail whose reasons are empty is not an audit trail."""
    cases = [
        (leak(reason_code="SUSPECTED_FRAUD", failure_side="risk"), "payment_link_sms"),
        (leak(dispute_open=True), "silent_retry"),
        (leak(network="MasterCard", retries_30d=12), "silent_retry"),
        (leak(contacts_last_7d=5), "payment_link_sms"),
        (leak(amount_paise=100_000_00), "payment_link_sms"),
    ]
    for ev, action in cases:
        out = evaluate_gate(ev, action, "B", 0.4, merchant, net_value_paise=50000)
        note = next(g["note"] for g in out.gate if g["verdict"] == "BLOCK")
        assert len(note) > 25, f"thin reason for {out.blocked_by}: {note!r}"
        assert note.strip().endswith((".", "%")), "reasons are sentences"


def test_no_rule_can_be_bypassed_by_an_unknown_action_name(merchant):
    """A typo or an injected action name must not fall through as permitted."""
    ev = leak(reason_code="SUSPECTED_FRAUD", failure_side="risk")
    out = evaluate_gate(ev, "definitely_not_an_action", "B", 0.9, merchant, net_value_paise=999999)
    assert out.blocked_by == "NO_RETRY_ON_FRAUD"
