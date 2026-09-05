"""Policy gate v2: classification, hard stops, instrument-specific ceilings."""

from __future__ import annotations

import pytest

from app.leaks import LeakEvent
from app.merchant import MerchantConfig
from app.policy import RULES, RULES_BY_ID, classify_message, evaluate_gate
from app.taxonomy import FAMILIES


@pytest.fixture
def merchant():
    return MerchantConfig()  # defaults, no file


def leak(**over) -> LeakEvent:
    fam = FAMILIES[over.pop("reason_code", "INSUFFICIENT_FUNDS")]
    base = dict(
        event_id="evt_test",
        amount_paise=49900,
        method="card",
        network="Visa",
        minutes_since_failure=120,
        local_hour_ist=14,
        attempts_this_cycle=1,
        contacts_last_7d=0,
        retries_30d=2,
        consent_on_file=True,
        consent_granted_days_ago=3,
        dnd_registered=False,
        reason_code=fam.code,
        reason_label=fam.label,
        failure_side=fam.side,
        hard_decline=fam.hard_decline,
        merchant_side=fam.merchant_side,
        retriable=fam.retriable,
    )
    base.update(over)
    return LeakEvent(**base)


def verdicts(out) -> dict[str, str]:
    return {g["ruleId"]: g["verdict"] for g in out.gate}


def test_every_row_names_a_catalogued_rule_and_carries_its_citation_key(merchant):
    out = evaluate_gate(leak(), "payment_link_sms", "B", 0.2, merchant)
    ids = [g["ruleId"] for g in out.gate]
    assert ids == [r.id for r in RULES if r.id != "ESCALATE_UNRESOLVED"]
    for g in out.gate:
        assert "citation" in g
        assert g["citation"] == RULES_BY_ID[g["ruleId"]].citation


def test_recurring_reminder_is_service_class_not_transactional(merchant):
    # Merchant-initiated charge → never transactional; informational → service (cl. 2(bh)).
    ev = leak(minutes_since_failure=5, customer_initiated=False, consent_on_file=False, dnd_registered=True, local_hour_ist=23)
    out = evaluate_gate(ev, "payment_link_sms", "B", 0.2, merchant)
    assert out.message_class == "service"
    assert not out.blocked, out.blocked_by
    v = verdicts(out)
    # Consent, DND and quiet hours do not apply to a service message.
    assert v["QUIET_HOURS_2100_0900_IST"] == "N/A"
    assert v["DND_SCRUB_PROMOTIONAL"] == "N/A"
    assert v["CONSENT_PURPOSE_MATCH"] == "N/A"


def test_customer_initiated_within_30_minutes_is_transactional(merchant):
    ev = leak(customer_initiated=True, minutes_since_failure=12, kind="checkout_abandonment")
    assert classify_message(ev, "payment_link_whatsapp")[0] == "transactional"
    ev = leak(customer_initiated=True, minutes_since_failure=45, has_relationship=True)
    assert classify_message(ev, "payment_link_whatsapp")[0] == "service"
    ev = leak(customer_initiated=True, minutes_since_failure=45, has_relationship=False)
    assert classify_message(ev, "payment_link_whatsapp")[0] == "promotional"


def test_incentive_reclassifies_as_promotional_and_hits_dnd(merchant):
    ev = leak(reason_code="MANDATE_REVOKED", consent_on_file=False)
    out = evaluate_gate(ev, "incentive_link", "B", 0.2, merchant)
    assert out.message_class == "promotional"
    assert out.blocked_by == "DND_SCRUB_PROMOTIONAL"
    assert verdicts(out)["MIXED_CONTENT_IS_PROMOTIONAL"] == "PASS"


def test_promotional_quiet_hours_and_seven_day_consent(merchant):
    late = leak(reason_code="MANDATE_REVOKED", local_hour_ist=22)
    assert evaluate_gate(late, "incentive_link", "B", 0.2, merchant).blocked_by == "QUIET_HOURS_2100_0900_IST"
    stale = leak(reason_code="MANDATE_REVOKED", consent_granted_days_ago=9, extras={"consent_purpose": "transaction_completion"})
    assert evaluate_gate(stale, "incentive_link", "B", 0.2, merchant).blocked_by == "CONSENT_PURPOSE_MATCH"


def test_hard_decline_blocks_retry_but_allows_instrument_change_first(merchant):
    ev = leak(reason_code="INSTRUMENT_BLOCKED", attempts_this_cycle=0, raw_reason="debit_instrument_blocked")
    retry = evaluate_gate(ev, "silent_retry", "B", 0.1, merchant)
    assert retry.blocked_by == "HARD_DECLINE_NO_RETRY"
    assert "debit_instrument_blocked" in next(g["note"] for g in retry.gate if g["ruleId"] == "HARD_DECLINE_NO_RETRY")
    contact = evaluate_gate(ev, "card_update_request", "B", 0.2, merchant)
    assert not contact.blocked
    # SILENT_FIRST would normally block at zero attempts; a hard decline is the exception.
    assert verdicts(contact)["SILENT_FIRST"] == "PASS"


def test_merchant_side_failure_never_contacts_the_customer(merchant):
    ev = leak(reason_code="MERCHANT_CONFIG", raw_reason="payment_method_not_enabled")
    out = evaluate_gate(ev, "payment_link_sms", "B", 0.5, merchant)
    assert out.blocked_by == "MERCHANT_SIDE_NO_CONTACT"
    assert not evaluate_gate(ev, "silent_retry", "B", 0.5, merchant).blocked


def test_dispute_and_fraud_stop_everything(merchant):
    assert evaluate_gate(leak(dispute_open=True), "silent_retry", "B", 0.5, merchant).blocked_by == "STOP_ON_DISPUTE"
    assert evaluate_gate(leak(reason_code="SUSPECTED_FRAUD"), "payment_link_sms", "B", 0.5, merchant).blocked_by == "NO_RETRY_ON_FRAUD"


def test_afa_ceiling_blocks_large_silent_recurring_debit(merchant):
    ev = leak(method="emandate", network=None, amount_paise=20_000_00)
    assert evaluate_gate(ev, "silent_retry", "B", 0.1, merchant).blocked_by == "AFA_THRESHOLD"
    small = leak(method="emandate", network=None, amount_paise=9_000_00)
    assert verdicts(evaluate_gate(small, "silent_retry", "B", 0.1, merchant))["AFA_THRESHOLD"] == "PASS"
    # Category raises the ceiling to ₹1,00,000.
    mf = MerchantConfig(category="mutual_fund")
    assert verdicts(evaluate_gate(ev, "silent_retry", "B", 0.1, mf))["AFA_THRESHOLD"] == "PASS"


def test_mandate_cap_replaces_card_cycle_cap_for_upi_autopay(merchant):
    ev = leak(method="upi_autopay", network=None, attempts_this_cycle=4)
    out = evaluate_gate(ev, "silent_retry", "B", 0.1, merchant)
    assert out.blocked_by == "MANDATE_ATTEMPT_CAP_4"
    assert verdicts(out)["MAX_RETRY_3_PER_CYCLE"] == "N/A"
    three = leak(method="upi_autopay", network=None, attempts_this_cycle=3)
    assert not evaluate_gate(three, "silent_retry", "B", 0.1, merchant).blocked


def test_network_cap_is_network_aware(merchant):
    visa = leak(network="Visa", retries_30d=12)
    mc = leak(network="MasterCard", retries_30d=12)
    assert not evaluate_gate(visa, "silent_retry", "B", 0.1, merchant).blocked
    assert evaluate_gate(mc, "silent_retry", "B", 0.1, merchant).blocked_by == "NETWORK_RETRY_CAP_30D"


def test_dues_window_applies_only_to_dues_leaks(merchant):
    sub = leak(local_hour_ist=20)
    assert verdicts(evaluate_gate(sub, "payment_link_sms", "B", 0.2, merchant))["DUES_CONTACT_WINDOW_0800_1900"] == "N/A"
    dues = leak(kind="receivable_overdue", local_hour_ist=20)
    assert evaluate_gate(dues, "payment_link_sms", "B", 0.2, merchant).blocked_by == "DUES_CONTACT_WINDOW_0800_1900"


def test_negative_net_value_blocks_even_with_positive_uplift(merchant):
    ev = leak()
    out = evaluate_gate(ev, "payment_link_sms", "B", 0.2, merchant, net_value_paise=-500)
    assert out.blocked_by == "STOP_ON_NEGATIVE_UPLIFT"
    assert verdicts(evaluate_gate(ev, "payment_link_sms", "A", 0.2, merchant))["STOP_ON_NEGATIVE_UPLIFT"] == "N/A"


def test_approval_threshold_holds_outreach_on_large_leaks(merchant):
    big = leak(amount_paise=15_000_00)
    assert evaluate_gate(big, "payment_link_sms", "B", 0.3, merchant).blocked_by == "APPROVAL_ABOVE_THRESHOLD"
    assert not evaluate_gate(big, "silent_retry", "B", 0.3, merchant).blocked


def test_rules_after_a_block_are_recorded_not_skipped(merchant):
    out = evaluate_gate(leak(reason_code="SUSPECTED_FRAUD"), "payment_link_sms", "B", 0.5, merchant)
    rows = out.gate
    assert rows[0]["verdict"] == "BLOCK"
    assert all(r["verdict"] == "N/A" for r in rows[1:])
    assert len(rows) == len(RULES) - 1
