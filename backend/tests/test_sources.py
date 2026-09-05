"""Real-data normalisation: Razorpay payment entities and exports → LeakEvents."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.sources import FileSource, SimulatorSource, _History, normalize_payment, normalize_subscription, parse_export

NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)


def _payment(**over) -> dict:
    base = {
        "id": "pay_ABC123",
        "entity": "payment",
        "amount": 129900,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "card": {"network": "Visa", "type": "credit", "issuer": "HDFC", "last4": "4242"},
        "token_id": "token_XYZ",
        "email": "a@example.com",
        "contact": "+919999999999",
        "customer_id": "cust_1",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Your payment didn't go through as it was declined by the bank.",
        "error_source": "customer",
        "error_step": "payment_authorization",
        "error_reason": "insufficient_funds",
        "created_at": int((NOW - timedelta(minutes=50)).timestamp()),
        "notes": {"subscription_id": "sub_77"},
    }
    base.update(over)
    return base


def test_recurring_card_failure_normalises_to_subscription_leak():
    ev = normalize_payment(_payment(), now=NOW, source="razorpay")
    assert ev is not None
    assert ev.kind == "subscription_failure"
    assert ev.reason_code == "INSUFFICIENT_FUNDS" and ev.reason_confidence == "high"
    assert ev.method == "card" and ev.network == "Visa" and ev.issuer == "HDFC"
    assert ev.customer_initiated is False and ev.has_relationship is True
    assert ev.minutes_since_failure == 50
    assert ev.subscription_id == "sub_77"
    assert ev.features_are_proxies is True
    assert ev.contact_hash() and ev.contact_hash() != ev.contact
    assert ev.truth is None and not ev.is_synthetic
    # IST hour from the UTC timestamp: 09:10 UTC → 14:40 IST.
    assert ev.local_hour_ist == 14


def test_one_time_upi_cancellation_is_customer_initiated_checkout_leak():
    p = _payment(method="upi", vpa="ravi@ybl", card=None, token_id=None, notes={}, customer_id=None, error_reason="payment_cancelled", error_source="customer")
    ev = normalize_payment(p, now=NOW, source="file")
    assert ev.kind == "checkout_abandonment"
    assert ev.customer_initiated is True
    assert ev.method == "upi" and ev.psp == "phonepe" and ev.issuer == "YBL"
    assert ev.reason_code == "CUSTOMER_CANCELLED"
    assert ev.has_relationship is False  # no customer id, no subscription


def test_business_source_unknown_reason_is_merchant_side():
    ev = normalize_payment(_payment(error_reason="brand_new_code", error_source="business"), now=NOW, source="razorpay")
    assert ev.reason_code == "MERCHANT_CONFIG" and ev.merchant_side is True and ev.failure_side == "merchant"


def test_non_failed_rows_are_ignored():
    assert normalize_payment(_payment(status="captured"), now=NOW, source="razorpay") is None


def test_history_proxies_come_from_the_window():
    rows = [
        _payment(id="pay_1", status="captured", created_at=int((NOW - timedelta(days=200)).timestamp())),
        _payment(id="pay_2", status="captured", created_at=int((NOW - timedelta(days=100)).timestamp())),
        _payment(id="pay_3", status="failed", created_at=int((NOW - timedelta(days=2)).timestamp())),
        _payment(id="pay_4", status="failed", created_at=int((NOW - timedelta(minutes=50)).timestamp())),
    ]
    h = _History(rows)
    ev = normalize_payment(rows[-1], now=NOW, source="razorpay", history=h)
    assert ev.attempts_this_cycle == 2  # two failures inside 7 days
    assert ev.retries_30d == 2
    assert ev.tenure_days >= 199
    assert 0.4 < ev.engagement < 0.8  # 2 successes of 4, smoothed


def test_pending_subscription_becomes_a_leak_when_payment_is_out_of_window():
    s = {"id": "sub_9", "status": "halted", "plan_id": "plan_1", "customer_id": "cust_2", "payment_method": "upi", "paid_count": 5, "charge_at": int((NOW - timedelta(days=3)).timestamp())}
    ev = normalize_subscription(s, now=NOW, source="razorpay", plan_lookup={"plan_1": {"item": {"amount": 49900, "name": "Pro monthly"}}})
    assert ev.amount_paise == 49900 and ev.plan_name == "Pro monthly"
    assert ev.method == "upi_autopay" and ev.attempts_this_cycle == 4 and ev.retriable is False


def test_parse_export_accepts_api_json_and_dashboard_csv():
    rows, warnings = parse_export(json.dumps({"entity": "collection", "count": 1, "items": [_payment()]}).encode(), "payments.json")
    assert len(rows) == 1 and not warnings

    csv_text = (
        "Payment Id,Amount,Status,Method,Error Code,Error Description,Error Reason,Error Source,Created At,Contact,Email\n"
        "pay_1,1299.00,failed,card,BAD_REQUEST_ERROR,Card expired,card_expired,customer,2026-09-05 12:00:00,+919999999999,a@b.c\n"
        "pay_2,499.00,captured,upi,,,,,2026-09-05 12:05:00,+919999999999,a@b.c\n"
    )
    rows, warnings = parse_export(csv_text.encode(), "payments.csv")
    assert len(rows) == 2 and rows[0]["id"] == "pay_1"
    ev = normalize_payment(rows[0], now=NOW, source="file")
    assert ev.amount_paise == 129900  # rupees with decimals → paise
    assert ev.reason_code == "CARD_EXPIRED" and ev.hard_decline
    assert normalize_payment(rows[1], now=NOW, source="file") is None


def test_file_source_round_trips_through_disk(tmp_path):
    src = FileSource(upload_dir=tmp_path)
    meta = src.save(json.dumps([_payment(), _payment(id="pay_2", status="captured")]).encode(), "export.json")
    assert meta["rows"] == 2 and meta["failedRows"] == 1 and meta["byFamily"] == {"INSUFFICIENT_FUNDS": 1}
    assert src.get_meta(meta["fileId"])["filename"] == "export.json"
    pulled = src.pull(file_id=meta["fileId"])
    assert len(pulled.leaks) == 1 and pulled.meta["rowsScanned"] == 2
    assert src.pull(file_id="file_nope").leaks == []
    assert [f["fileId"] for f in src.list_files()] == [meta["fileId"]]


def test_simulator_source_is_deterministic():
    a = SimulatorSource().pull(seed=11, count=20)
    b = SimulatorSource().pull(seed=11, count=20)
    assert [e.event_id for e in a.leaks] == [e.event_id for e in b.leaks]
    assert all(e.is_synthetic for e in a.leaks) and a.meta["seed"] == 11
