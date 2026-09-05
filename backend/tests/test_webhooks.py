"""Webhook receiver: signature, idempotence, and attribution."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.runtime import Runtime
from app.webhooks import WebhookReceiver

SECRET = "whsec_test"


@pytest.fixture(scope="module")
def rt(tmp_path_factory):
    return Runtime.build(store_path=tmp_path_factory.mktemp("wh") / "ledger.db")


@pytest.fixture
def receiver(rt):
    return WebhookReceiver(rt.store, rt.outcomes, rt.promises, secret=SECRET)


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def payload(event: str, **entities) -> dict:
    body: dict = {"event": event, "payload": {}}
    for name, entity in entities.items():
        body["payload"][name] = {"entity": entity}
    return body


def test_unsigned_and_wrong_signature_are_refused(rt):
    unconfigured = WebhookReceiver(rt.store, rt.outcomes, rt.promises, secret="")
    assert unconfigured.configured is False
    assert unconfigured.verify(b"{}", sign(b"{}")) is False, "no secret means no delivery is ever trusted"

    r = WebhookReceiver(rt.store, rt.outcomes, rt.promises, secret=SECRET)
    body = json.dumps(payload("payment_link.paid")).encode()
    assert r.verify(body, sign(body)) is True
    assert r.verify(body, "deadbeef") is False
    assert r.verify(body, None) is False
    assert r.verify(b'{"event":"tampered"}', sign(body)) is False


def test_recovery_webhook_attributes_and_settles_the_promise(rt, receiver):
    """A real batch, then Razorpay reports the money. Without live keys the
    executor creates no payment link, so the Razorpay object is attached
    directly — the matching path is what is under test, not the executor."""
    import time

    now = int(time.time())
    rows = [{
        "id": "pay_wh1", "amount": 249900, "status": "failed", "method": "card",
        "card": {"network": "Visa", "issuer": "HDFC"}, "token_id": "tok",
        "customer_id": "cust_wh", "contact": "+919900000123",
        "error_reason": "insufficient_funds", "error_source": "customer",
        "created_at": now - 3600,
    }]
    meta = rt.sources["file"].save(json.dumps({"items": rows}).encode(), "wh.json")
    summary = rt.run_and_store("file", file_id=meta["fileId"])
    leak = rt.store.leaks_for_batch(summary["batchId"])[0]
    with rt.store.transaction() as c:
        c.execute(
            "UPDATE leaks SET external_kind = 'payment_link', external_id = ? WHERE batch_id = ? AND event_id = ?",
            ("plink_WH123", summary["batchId"], leak["event_id"]),
        )

    # A live promise on the same counterparty must be settled by the payment.
    rt.promises.record(leak["counterparty_id"], leak["amount_paise"], "2099-01-01T00:00:00+00:00", "voice", "kal", leak["event_id"])
    assert rt.promises.open_for(leak["counterparty_id"]) is not None

    res = receiver.handle(payload("payment_link.paid", payment_link={"id": "plink_WH123"}), "evt_delivery_1")
    assert res.status == "applied" and res.leak == leak["event_id"], res.detail

    after = rt.store.latest_leak(leak["event_id"])
    assert after["outcome_state"] == "resolved" and after["outcome_recovered"] == 1
    assert after["outcome_source"] == "webhook:payment_link.paid"
    assert rt.promises.open_for(leak["counterparty_id"]) is None, "a payment settles the live promise"
    assert rt.store.audit_tail(kind="outcome.attributed", ref=leak["event_id"])

    # A second, different delivery about the same leak leaves it unchanged.
    again = receiver.handle(payload("subscription.charged", subscription={"id": "sub_other"}), "evt_delivery_2")
    assert again.status in ("unmatched", "ignored")


def test_churn_webhook_marks_the_leak_churned(rt, receiver):
    import time

    now = int(time.time())
    rows = [{
        "id": "pay_wh2", "amount": 129900, "status": "failed", "method": "card",
        "card": {"issuer": "ICIC"}, "token_id": "tok", "customer_id": "cust_wh2",
        "error_reason": "card_expired", "error_source": "customer", "created_at": now - 7200,
    }]
    meta = rt.sources["file"].save(json.dumps({"items": rows}).encode(), "wh2.json")
    summary = rt.run_and_store("file", file_id=meta["fileId"])
    leak = rt.store.leaks_for_batch(summary["batchId"])[0]
    with rt.store.transaction() as c:
        c.execute("UPDATE leaks SET subscription_id = ? WHERE batch_id = ? AND event_id = ?",
                  ("sub_WH9", summary["batchId"], leak["event_id"]))

    res = receiver.handle(payload("subscription.halted", subscription={"id": "sub_WH9"}), "evt_delivery_halt")
    assert res.status == "applied"
    after = rt.store.latest_leak(leak["event_id"])
    assert after["outcome_recovered"] == 0 and after["outcome_churned"] == 1


def test_replayed_delivery_is_acknowledged_not_reprocessed(rt, receiver):
    body = payload("payment_link.paid", payment_link={"id": "plink_never_seen"})
    first = receiver.handle(body, "evt_delivery_dup")
    assert first.status in ("unmatched", "applied")
    again = receiver.handle(body, "evt_delivery_dup")
    assert again.status == "replayed"
    assert "already processed" in again.detail


def test_irrelevant_and_unmatched_events(rt, receiver):
    assert receiver.handle(payload("payment.authorized"), "evt_x1").status == "ignored"
    assert receiver.handle(payload("payment_link.paid", payment_link={"id": "plink_nobody"}), "evt_x2").status == "unmatched"
    downtime = receiver.handle(payload("payment.downtime.started", payment_downtime={"id": "down_1"}), "evt_x3")
    assert downtime.status == "applied" and "cohort" in downtime.detail


def test_describe_says_plainly_when_unconfigured(rt):
    d = WebhookReceiver(rt.store, rt.outcomes, rt.promises, secret="").describe()
    assert d["configured"] is False
    assert "will not accept unsigned payloads" in d["note"]
    assert "subscription.charged" in d["events"]
