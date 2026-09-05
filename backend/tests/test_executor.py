"""Executor without keys: every record must be honest about what happened."""

from __future__ import annotations


import pytest

from app.executor import DEFAULT_MAX_LIVE_CALLS, Executor
from app.sim import generate_events


def _no_keys(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)


def test_silent_retry_mock_does_not_cite_a_nonexistent_endpoint(monkeypatch):
    _no_keys(monkeypatch)
    ex = Executor()
    ev = generate_events(3, count=1)[0]
    rec = ex.execute(ev, "silent_retry")
    assert rec["mocked"] is True
    # Razorpay's Subscriptions API has no retry endpoint; the trace must not invent one.
    assert "/retry" not in rec["detail"]
    assert "pending" in rec["detail"] and "T+1" in rec["detail"]


def test_outreach_mock_labels_delivery_as_mocked(monkeypatch):
    _no_keys(monkeypatch)
    ex = Executor()
    ev = generate_events(4, count=1)[0]
    rec = ex.execute(ev, "payment_link_whatsapp")
    assert rec["mocked"] is True
    assert "WhatsApp delivery mocked" in rec["detail"]


def test_escalate_makes_no_call(monkeypatch):
    _no_keys(monkeypatch)
    ex = Executor()
    ev = generate_events(5, count=1)[0]
    assert ex.execute(ev, "escalate")["mode"] == "none"


def test_live_call_cap_is_per_batch(monkeypatch):
    _no_keys(monkeypatch)
    monkeypatch.setenv("EXECUTOR_MAX_LIVE_CALLS", "2")
    ex = Executor()
    assert ex.max_live_calls == 2
    # No client → never takes a slot, regardless of cap.
    assert ex._take_live_slot() is False


@pytest.mark.parametrize("value", ["", "   ", "not-a-number"])
def test_blank_or_bad_numeric_env_falls_back_instead_of_crashing(monkeypatch, value):
    """A copied .env template leaves `EXECUTOR_MAX_LIVE_CALLS=` blank. That must
    not stop the backend booting — an empty variable is an absent one."""
    _no_keys(monkeypatch)
    monkeypatch.setenv("EXECUTOR_MAX_LIVE_CALLS", value)
    assert Executor().max_live_calls == DEFAULT_MAX_LIVE_CALLS


def test_absent_numeric_env_uses_the_default(monkeypatch):
    _no_keys(monkeypatch)
    monkeypatch.delenv("EXECUTOR_MAX_LIVE_CALLS", raising=False)
    assert Executor().max_live_calls == DEFAULT_MAX_LIVE_CALLS
