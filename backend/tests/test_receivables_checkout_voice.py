"""Receivables ladder, cart arms, mandate scheduling, and the voice call."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.checkout import generate_carts, incentive_arm_value, normalize_abandoned_cart
from app.invoice_sim import generate_invoices
from app.leaks import LeakEvent
from app.merchant import MerchantConfig
from app.policy import evaluate_gate, in_mandate_window, preferred_contact_action
from app.receivables import (
    MSMED_DAYS_NO_AGREEMENT,
    MSMED_DAYS_WITH_AGREEMENT,
    InvoiceSource,
    ageing_bucket,
    ladder_step,
    msmed_interest_paise,
)
from app.scheduling import choose_slot, sequence
from app.voice import SarvamVoice, classify_intent, extract_promise_date, run_call

NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def merchant():
    return MerchantConfig()


# ------------------------------------------------------------- receivables


def test_ageing_buckets_and_statutory_deadlines():
    assert ageing_bucket(3) == "0-15" and ageing_bucket(30) == "16-45"
    assert ageing_bucket(60) == "46-90" and ageing_bucket(200) == "90+"
    assert MSMED_DAYS_NO_AGREEMENT == 15 and MSMED_DAYS_WITH_AGREEMENT == 45


def test_msmed_interest_only_accrues_past_the_deadline():
    assert msmed_interest_paise(1_000_000, 30, 45) == 0
    late = msmed_interest_paise(1_000_000, 105, 45)  # 60 days late ≈ 2 months
    assert late > 0
    # 3× a 5.5% bank rate is 16.5% a year; two months is roughly 2.8% of principal.
    assert 20_000 < late < 35_000


def test_ladder_advances_with_age_and_skips_statutory_for_non_mse():
    assert ladder_step(2, False, False)["action"] == "invoice_reminder"
    assert ladder_step(10, False, False)["action"] == "statement_of_account"
    assert ladder_step(25, False, False)["action"] == "payment_link_sms"
    assert ladder_step(40, False, False)["action"] == "virtual_account"
    assert ladder_step(50, True, False)["action"] == "msmed_notice"
    # No MSE registration → the statutory rung does not exist for this supplier.
    assert ladder_step(50, False, False)["action"] == "virtual_account"
    assert ladder_step(80, False, False)["action"] == "escalate"
    # A dispute stops the ladder wherever it is.
    assert ladder_step(80, True, True)["action"] == "no_action"


def test_statutory_notice_is_gated_on_registration_and_the_window(merchant):
    def receivable(days, mse, deadline=45):
        return LeakEvent(
            event_id="evt_r", kind="receivable_overdue", counterparty_type="business",
            customer_id="biz_1", amount_paise=2_500_00, method="netbanking",
            reason_code="RECEIVABLE_OVERDUE", reason_label="overdue", failure_side="customer",
            local_hour_ist=11, attempts_this_cycle=1, consent_on_file=True,
            extras={"days_overdue": days, "mse_supplier": mse, "statutory_deadline_days": deadline,
                    "statutory_interest_paise": 1234, "ladder": ladder_step(days, mse, False)},
        )

    too_early = evaluate_gate(receivable(30, True), "msmed_notice", "B", 0.3, merchant)
    assert too_early.blocked_by == "MSMED_LEVER_AFTER_STATUTORY_WINDOW"
    not_mse = evaluate_gate(receivable(90, False), "msmed_notice", "B", 0.3, merchant)
    assert not_mse.blocked_by == "MSMED_LEVER_AFTER_STATUTORY_WINDOW"
    assert "false statement" in next(g["note"] for g in not_mse.gate if g["ruleId"] == "MSMED_LEVER_AFTER_STATUTORY_WINDOW")
    ok = evaluate_gate(receivable(90, True), "msmed_notice", "B", 0.3, merchant)
    assert ok.blocked_by is None


def test_receivables_use_the_dues_contact_window(merchant):
    ev = LeakEvent(event_id="evt_r", kind="receivable_overdue", counterparty_type="business", customer_id="biz_1",
                   amount_paise=250000, method="netbanking", reason_code="RECEIVABLE_OVERDUE",
                   reason_label="overdue", failure_side="customer", local_hour_ist=20,
                   attempts_this_cycle=1, consent_on_file=True, extras={"days_overdue": 20})
    assert evaluate_gate(ev, "invoice_reminder", "B", 0.3, merchant).blocked_by == "DUES_CONTACT_WINDOW_0800_1900"


def test_real_invoice_normalises_to_a_receivable_leak(merchant):
    inv = {
        "id": "inv_ABC", "entity": "invoice", "status": "issued", "amount": 250000, "amount_due": 250000,
        "amount_paid": 0, "expire_by": int((NOW - timedelta(days=60)).timestamp()),
        "customer_details": {"name": "Coastal Foods", "contact": "+919900000001", "email": "ap@coastal.example"},
        "line_items": [{"name": "Wholesale order"}],
        "notes": {"mse_supplier": "true", "written_agreement": "true"},
    }
    ev = InvoiceSource.normalize(inv, now=NOW, merchant=merchant)
    assert ev is not None
    assert ev.kind == "receivable_overdue" and ev.counterparty_type == "business"
    assert ev.days_overdue == 60 and ev.is_mse_supplier
    assert ev.extras["ageing"] == "46-90"
    assert ev.extras["statutory_interest_paise"] > 0
    assert ev.extras["ladder"]["action"] == "msmed_notice"
    assert preferred_contact_action(ev) == "msmed_notice"
    assert ev.truth is None  # real data never knows the branch not taken

    paid = dict(inv, status="paid")
    assert InvoiceSource.normalize(paid, now=NOW, merchant=merchant) is None
    not_due = dict(inv, expire_by=int((NOW + timedelta(days=5)).timestamp()))
    assert InvoiceSource.normalize(not_due, now=NOW, merchant=merchant) is None


def test_invoice_simulator_produces_the_same_shape(merchant):
    leaks = generate_invoices(7, 120, merchant)
    assert len(leaks) == 120
    assert all(e.kind == "receivable_overdue" and e.counterparty_type == "business" for e in leaks)
    assert all(e.truth is not None for e in leaks), "the simulated book is gradeable"
    assert {e.extras["archetype"] for e in leaks} - {"enterprise_slow_ap", "prompt_payer", "cash_tight_sme", "chronic_late", "disputing", "insolvent"} == set()
    assert any(e.dispute_open for e in leaks)
    assert generate_invoices(7, 20, merchant)[0].event_id == generate_invoices(7, 20, merchant)[0].event_id


# ---------------------------------------------------------------- checkout


def test_carts_carry_stage_truth_and_are_customer_initiated(merchant):
    carts = generate_carts(11, 200, merchant)
    assert len(carts) == 200
    assert all(c.kind == "checkout_abandonment" and c.customer_initiated for c in carts)
    assert all(c.truth is not None for c in carts)
    stages = {c.extras["stage"] for c in carts}
    assert stages == {"cart", "contact", "address", "payment"}
    # Later stages carry more intent, so they hold more sure things.
    payment_sure = sum(1 for c in carts if c.extras["stage"] == "payment" and c.segment == "sure_thing")
    cart_sure = sum(1 for c in carts if c.extras["stage"] == "cart" and c.segment == "sure_thing")
    payment_n = sum(1 for c in carts if c.extras["stage"] == "payment")
    cart_n = sum(1 for c in carts if c.extras["stage"] == "cart")
    assert (payment_sure / payment_n) > (cart_sure / cart_n)


def test_free_arm_wins_when_the_discount_buys_nothing(merchant):
    ev = LeakEvent(event_id="evt_c", kind="checkout_abandonment", amount_paise=200000,
                   extras={"offer_incentive": True, "p_base": 0.60})
    # The incentive adds almost nothing, but the discount is paid on every conversion.
    arm = incentive_arm_value(ev, tau_plain=0.10, tau_incentive=0.11, merchant=merchant)
    assert arm["arm"] == "cart_reminder"
    assert arm["marginGivenUpPaise"] > 0
    assert "coming back anyway" in arm["note"]


def test_incentive_arm_wins_when_it_genuinely_converts(merchant):
    ev = LeakEvent(event_id="evt_c", kind="checkout_abandonment", amount_paise=200000,
                   extras={"offer_incentive": True, "p_base": 0.05})
    arm = incentive_arm_value(ev, tau_plain=0.02, tau_incentive=0.35, merchant=merchant)
    assert arm["arm"] == "cart_incentive"
    assert arm["incentiveValuePaise"] > arm["plainValuePaise"]


def test_magic_checkout_webhook_normalises(merchant):
    payload = {
        "cart_token": "tok_1", "email": "a@b.c", "phone": "+919999999999",
        "line_items": [{"name": "Linen shirt", "price": 189900, "quantity": 1}],
        "line_items_total": 189900,
        "abandoned_checkout_url": "https://shop.example.in/checkout/tok_1",
        "created_at": int((NOW - timedelta(minutes=20)).timestamp()),
    }
    ev = normalize_abandoned_cart(payload, now=NOW, merchant=merchant)
    assert ev is not None and ev.amount_paise == 189900
    assert ev.customer_initiated and ev.minutes_since_failure == 20
    assert ev.truth is None and ev.features_are_proxies
    assert normalize_abandoned_cart({}, now=NOW, merchant=merchant) is None


def test_a_cart_reminder_within_thirty_minutes_is_transactional(merchant):
    ev = LeakEvent(event_id="evt_c", kind="checkout_abandonment", amount_paise=189900,
                   customer_initiated=True, minutes_since_failure=12, has_relationship=False,
                   local_hour_ist=23, consent_on_file=False, dnd_registered=True,
                   reason_code="CHECKOUT_ABANDONED", reason_label="abandoned", failure_side="customer",
                   attempts_this_cycle=1, extras={"stage": "payment"})
    out = evaluate_gate(ev, "cart_reminder", "B", 0.3, merchant)
    assert out.message_class == "transactional"
    assert not out.blocked, out.blocked_by
    # The same nudge with a discount is promotional, and now the time band bites.
    inc = evaluate_gate(ev, "cart_incentive", "B", 0.3, merchant)
    assert inc.message_class == "promotional"
    assert inc.blocked_by == "QUIET_HOURS_2100_0900_IST"


# ---------------------------------------------------------------- mandates


def test_scheduler_picks_a_liquid_day_inside_an_npci_window(merchant):
    ev = LeakEvent(event_id="evt_m", method="upi_autopay", amount_paise=49900, attempts_this_cycle=1,
                   reason_code="INSUFFICIENT_FUNDS", reason_label="Insufficient balance", failure_side="customer")
    # Late in the month, when balances are thinnest.
    now = datetime(2026, 9, 24, 6, 0, tzinfo=timezone.utc)
    slot = choose_slot(ev, merchant, now)
    assert in_mandate_window(slot.at.astimezone(timezone(timedelta(hours=5, minutes=30))).hour)
    assert slot.notice_at <= slot.at - timedelta(hours=24)
    plan = sequence(ev, merchant, now)
    assert plan["pSufficientLift"] > 0, "waiting for the salary window must beat the fixed T+1 clock here"
    assert plan["expectedRecoveryPaise"] > plan["fixedClockRecoveryPaise"]
    assert plan["attemptsLeft"] == merchant.max_mandate_attempts - 1


def test_gate_blocks_a_peak_window_execution(merchant):
    ev = LeakEvent(event_id="evt_m", method="upi_autopay", amount_paise=49900, attempts_this_cycle=1,
                   reason_code="INSUFFICIENT_FUNDS", reason_label="Insufficient balance", failure_side="customer",
                   extras={"scheduled_hour_ist": 11, "pre_debit_notice_hours": 24})
    assert evaluate_gate(ev, "silent_retry", "B", 0.2, merchant).blocked_by == "MANDATE_EXECUTION_WINDOW"
    ev.extras["scheduled_hour_ist"] = 9
    assert evaluate_gate(ev, "silent_retry", "B", 0.2, merchant).blocked_by is None


def test_gate_blocks_a_debit_without_24h_notice(merchant):
    ev = LeakEvent(event_id="evt_m", method="emandate", amount_paise=49900, attempts_this_cycle=1,
                   reason_code="INSUFFICIENT_FUNDS", reason_label="Insufficient balance", failure_side="customer",
                   extras={"scheduled_hour_ist": 9, "pre_debit_notice_hours": 4})
    assert evaluate_gate(ev, "silent_retry", "B", 0.2, merchant).blocked_by == "PRE_DEBIT_NOTICE_24H"


# ------------------------------------------------------------------- voice


def test_intent_classification_prioritises_objections():
    assert classify_intent("Haan 12 tarikh tak kar dunga")[0] == "promise"
    assert classify_intent("Link bhej dijiye")[0] == "send_link"
    assert classify_intent("Nahi chahiye, band kar dijiye")[0] == "decline"
    assert classify_intent("Maine cancel kar diya tha, yeh galat hai")[0] == "dispute"
    assert classify_intent("")[0] == "silence"
    assert classify_intent("hmm")[0] == "unclear"
    # A reply that both promises and objects is read as the objection.
    assert classify_intent("yeh galat hai lekin kal kar dunga")[0] == "dispute"


def test_promise_date_extraction_is_conservative():
    assert extract_promise_date("kal kar dunga", NOW).date() == (NOW + timedelta(days=1)).date()
    assert extract_promise_date("12 tarikh tak", NOW).astimezone(timezone(timedelta(hours=5, minutes=30))).day == 12
    assert extract_promise_date("salary aate hi", NOW).astimezone(timezone(timedelta(hours=5, minutes=30))).day == 1
    assert extract_promise_date("jaldi kar dunga", NOW) is None, "an unparseable promise must be no promise"


def test_call_without_a_sarvam_key_produces_a_script_and_says_so(merchant):
    ev = LeakEvent(event_id="evt_v", amount_paise=299900, plan_name="Team monthly",
                   reason_code="INSUFFICIENT_FUNDS", reason_label="Insufficient balance",
                   failure_side="customer", segment="persuadable", method="upi_autopay",
                   extras={"customer_name": "Ravi"})
    voice = SarvamVoice(api_key="")
    assert voice.live is False
    res = run_call(ev, "Meridian", voice, np.random.default_rng(3))
    assert res.turns and res.turns[0].speaker == "agent"
    assert all(t.audio_b64 is None and t.audio_mocked for t in res.turns if t.speaker == "agent")
    assert res.audio_live is False
    assert "SARVAM_API_KEY is not set" in res.note
    assert "Namaste Ravi ji" in res.turns[0].text
    assert res.outcome in ("promise", "link_sent", "decline", "dispute", "callback", "no_answer", "unclear")


def test_voice_gate_requires_series_value_and_a_prior_attempt(merchant):
    def caller(**over):
        base = dict(event_id="evt_v", amount_paise=299900, reason_code="INSUFFICIENT_FUNDS",
                    reason_label="Insufficient balance", failure_side="customer", method="card", network="Visa",
                    attempts_this_cycle=1, contacts_last_7d=1, consent_on_file=True, local_hour_ist=11,
                    has_relationship=True, extras={})
        base.update(over)
        return LeakEvent(**base)

    ok = evaluate_gate(caller(), "voice_call", "B", 0.3, merchant)
    assert ok.blocked_by is None, ok.blocked_by

    cheap = evaluate_gate(caller(amount_paise=19900), "voice_call", "B", 0.3, merchant)
    assert cheap.blocked_by == "VOICE_ELIGIBILITY"

    untouched = evaluate_gate(caller(contacts_last_7d=0), "voice_call", "B", 0.3, merchant)
    assert untouched.blocked_by == "VOICE_ELIGIBILITY"
    assert "last rung" in next(g["note"] for g in untouched.gate if g["ruleId"] == "VOICE_ELIGIBILITY")

    over_called = evaluate_gate(caller(extras={"voice_calls_today": 3}), "voice_call", "B", 0.3, merchant)
    assert over_called.blocked_by == "VOICE_FREQ_3D_8W"


def test_promotional_voice_needs_the_140_series(merchant):
    ev = LeakEvent(event_id="evt_v", amount_paise=299900, reason_code="MANDATE_REVOKED",
                   reason_label="Mandate revoked", failure_side="customer", method="upi_autopay",
                   attempts_this_cycle=1, contacts_last_7d=1, consent_on_file=True, local_hour_ist=11,
                   has_relationship=False, extras={})
    # No relationship → promotional class → 140-series required; merchant holds 1600.
    out = evaluate_gate(ev, "voice_call", "B", 0.3, merchant)
    assert out.message_class == "promotional"
    assert out.blocked_by == "VOICE_ELIGIBILITY"
    assert "140-series" in next(g["note"] for g in out.gate if g["ruleId"] == "VOICE_ELIGIBILITY")
