"""Promise-to-pay: the hold, the clock, and what verifies a promise."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.leaks import LeakEvent
from app.merchant import MerchantConfig
from app.policy import evaluate_gate
from app.promises import BROKEN_AFTER_DAYS, PromiseBook
from app.store import Store

NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def book(tmp_path):
    s = Store(tmp_path / "ledger.db")
    yield PromiseBook(s)
    s.close()


def iso(dt: datetime) -> str:
    return dt.isoformat()


def test_recording_a_promise_holds_the_counterparty(book):
    p = book.record("cust_1", 49900, iso(NOW + timedelta(days=4)), "voice", "Salary aate hi pay kar dunga", "evt_1")
    assert p.state == "open" and p.public()["open"] is True
    assert book.open_for("cust_1").promise_id == p.promise_id
    assert book.open_for("cust_other") is None
    audit = book.store.audit_tail(kind="promise.recorded")
    assert audit and audit[0]["payload"]["capturedVia"] == "voice"


def test_a_live_promise_blocks_even_the_silent_retry(book):
    p = book.record("cust_1", 49900, iso(NOW + timedelta(days=4)), "whatsapp", "Friday tak", "evt_1")
    ev = LeakEvent(event_id="evt_1", customer_id="cust_1", amount_paise=49900, method="card", network="Visa",
                   reason_code="INSUFFICIENT_FUNDS", reason_label="Insufficient balance", failure_side="customer",
                   attempts_this_cycle=1, consent_on_file=True, promise_hold=p.public())
    merchant = MerchantConfig()
    for action in ("payment_link_sms", "silent_retry"):
        out = evaluate_gate(ev, action, "B", 0.4, merchant)
        assert out.blocked_by == "PTP_ACTIVE_HOLD", action
    note = next(g["note"] for g in evaluate_gate(ev, "silent_retry", "B", 0.4, merchant).gate if g["ruleId"] == "PTP_ACTIVE_HOLD")
    assert "499.00" in note and "whatsapp" in note


def test_renegotiation_replaces_rather_than_stacks(book):
    first = book.record("cust_1", 49900, iso(NOW + timedelta(days=2)), "voice", "Kal", "evt_1")
    second = book.record("cust_1", 49900, iso(NOW + timedelta(days=9)), "voice", "Agle hafte", "evt_1")
    assert book.get(first.promise_id).state == "cancelled"
    assert book.open_for("cust_1").promise_id == second.promise_id


def test_reminder_then_broken_then_escalation(book):
    due = NOW + timedelta(days=1)
    p = book.record("cust_1", 49900, iso(due), "voice", "Kal", "evt_1")

    book.tick(now=NOW)  # T−24h → reminded
    assert book.get(p.promise_id).state == "reminded"

    book.tick(now=due + timedelta(days=BROKEN_AFTER_DAYS, hours=1))
    assert book.get(p.promise_id).state == "broken"
    assert book.get(p.promise_id).broken_count == 1
    assert book.open_for("cust_1") is None, "a broken promise no longer holds anything"

    # A second promise that also breaks escalates to a risk decision.
    p2 = book.record("cust_1", 49900, iso(due + timedelta(days=5)), "voice", "Pakka", "evt_1")
    assert p2.broken_count == 1
    book.tick(now=due + timedelta(days=5 + BROKEN_AFTER_DAYS, hours=1))
    assert book.get(p2.promise_id).state == "risk_escalated"
    assert book.store.audit_tail(kind="promise.escalated")


def test_kept_only_when_money_actually_arrives(book):
    p = book.record("cust_1", 49900, iso(NOW + timedelta(days=3)), "link_click", "", "evt_1")
    assert book.settle_from_outcome("cust_nobody", 49900, "poll:payment_link") is False
    assert book.settle_from_outcome("cust_1", 49900, "poll:payment_link") is True
    got = book.get(p.promise_id)
    assert got.state == "kept" and got.verified_by == "poll:payment_link"
    assert book.open_for("cust_1") is None


def test_partial_payment_is_partially_kept(book):
    p = book.record("cust_1", 100000, iso(NOW + timedelta(days=3)), "voice", "", "evt_1")
    book.settle_from_outcome("cust_1", 40000, "poll:invoice")
    assert book.get(p.promise_id).state == "partially_kept"


def test_stats_report_kept_rate_by_channel(book):
    book.record("cust_a", 10000, iso(NOW + timedelta(days=2)), "voice", "", "evt_a")
    book.settle_from_outcome("cust_a", 10000, "poll:payment_link")
    book.record("cust_b", 10000, iso(NOW - timedelta(days=10)), "whatsapp", "", "evt_b")
    book.tick(now=NOW)
    s = book.stats()
    assert s["total"] == 2
    assert s["keptRate"] == 0.5
    assert s["byChannel"]["voice"]["kept"] == 1
    assert s["byChannel"]["whatsapp"]["broken"] == 1
