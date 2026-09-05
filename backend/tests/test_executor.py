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


class _RefusingClient:
    """A Razorpay client that fails the way a real one does when a test-mode
    quota is exhausted — the exact condition this account is now in."""

    class _Fails:
        def __init__(self, msg): self.msg = msg
        def create(self, *_a, **_k): raise RuntimeError(self.msg)

    def __init__(self, msg="test mode limit of 30 reached for payment_link"):
        self.payment_link = _RefusingClient._Fails(msg)
        self.order = _RefusingClient._Fails(msg)


def test_the_executor_degrades_when_razorpay_refuses(monkeypatch):
    """When Razorpay rejects a call — quota exhausted, product not enabled, an
    outage — the batch must still complete, and the execution record must say
    the call failed rather than implying it succeeded."""
    _no_keys(monkeypatch)
    ex = Executor()
    ex.client = _RefusingClient()
    ev = generate_events(11, count=1)[0]

    for action in ("silent_retry", "payment_link_sms", "payment_link_whatsapp"):
        rec = ex.execute(ev, action)
        assert rec["mocked"] is True, f"{action} claimed a real call after a refusal"
        assert rec["externalId"] is None, "no external id may be reported for a failed call"
        assert "failed" in rec["detail"].lower() or "not created" in rec["detail"].lower()


def test_a_refusing_razorpay_does_not_sink_the_batch(monkeypatch, tmp_path):
    """The whole point of catching executor errors: one refused API call must
    not lose the other 199 decisions in the batch."""
    _no_keys(monkeypatch)
    from app.runtime import Runtime

    rt = Runtime.build(store_path=tmp_path / "refuse.db")
    rt.executor.client = _RefusingClient()
    summary = rt.run_and_store("simulator", seed=17, count=200)
    assert summary["eventCount"] == 200
    assert rt.store.verify_audit()["ok"] is True
    batch = rt.store.get_batch(summary["batchId"])
    traces = [rt.store.get_trace(e["eventId"], summary["batchId"]) for e in batch["events"]]
    assert all(t["agentB"]["execution"]["externalId"] is None for t in traces)
