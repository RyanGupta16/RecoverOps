"""Conformance against Razorpay's real published contracts.

Every error_reason string here is taken from Razorpay's own error list, every
entity shape from its API reference, and the downtime payloads are verbatim
records from a live test account. If Razorpay sends it, this file asserts we
handle it — not by guessing a shape, but against the documented one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.checkout import normalize_abandoned_cart
from app.degradation import DegradationMonitor, cohort_key
from app.merchant import MerchantConfig
from app.receivables import InvoiceSource
from app.sources import normalize_payment, normalize_subscription, parse_export
from app.taxonomy import FAMILIES, REASON_TO_FAMILY, classify
from app.webhooks import CHURN_EVENTS, DEGRADATION_EVENTS, RECOVERY_EVENTS

NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)


def payment(**over) -> dict:
    """A Razorpay payment entity in the documented shape."""
    base = {
        "id": "pay_29QQoUBi66xm2f", "entity": "payment", "amount": 100000, "currency": "INR",
        "status": "failed", "order_id": "order_GjCr5oKh4AVC51", "invoice_id": None,
        "international": False, "method": "card", "amount_refunded": 0, "refund_status": None,
        "captured": False, "description": "Payment for Adidas shoes", "card_id": "card_KOdY30ajbuyOYN",
        "card": {"id": "card_KOdY30ajbuyOYN", "entity": "card", "name": "Gaurav Kumar",
                 "last4": "4366", "network": "Visa", "type": "credit", "issuer": "HDFC",
                 "emi": False, "sub_type": "consumer"},
        "bank": None, "wallet": None, "vpa": None, "email": "gaurav.kumar@example.com",
        "contact": "9000090000", "customer_id": "cust_K6fNE0WJZWGqtN", "token_id": "token_KOdY$DBYQOv08n",
        "notes": {}, "fee": 0, "tax": 0,
        "error_code": "BAD_REQUEST_ERROR", "error_description": "Payment failed",
        "error_source": "customer", "error_step": "payment_authorization",
        "error_reason": "payment_failed",
        "acquirer_data": {"auth_code": None}, "created_at": int((NOW - timedelta(minutes=30)).timestamp()),
    }
    base.update(over)
    return base


# ------------------------------------------------- the whole error taxonomy


@pytest.mark.parametrize("reason", sorted(REASON_TO_FAMILY))
def test_every_documented_error_reason_maps_and_normalises(reason):
    """All 109 reason strings Razorpay publishes. Each must map to a family and
    survive normalisation into a LeakEvent without raising."""
    fam = FAMILIES[REASON_TO_FAMILY[reason]]
    c = classify(reason, fam.side)
    assert c.family.code == fam.code and c.confidence == "high"

    ev = normalize_payment(payment(error_reason=reason, error_source=fam.side), now=NOW, source="razorpay")
    assert ev is not None, f"{reason} produced no leak"
    assert ev.reason_code == fam.code
    assert ev.failure_side in ("customer", "issuer", "risk", "merchant")
    assert ev.amount_paise == 100000
    assert isinstance(ev.retriable, bool) and isinstance(ev.hard_decline, bool)


def test_the_taxonomy_covers_the_published_list_at_the_expected_size():
    """A regression guard: if someone deletes a family's reasons, this drops."""
    assert len(REASON_TO_FAMILY) >= 105, f"only {len(REASON_TO_FAMILY)} reasons mapped"
    assert len(FAMILIES) == 15


@pytest.mark.parametrize("code", ["BAD_REQUEST_ERROR", "GATEWAY_ERROR", "SERVER_ERROR"])
def test_every_error_code_class_is_accepted(code):
    ev = normalize_payment(payment(error_code=code), now=NOW, source="razorpay")
    assert ev is not None and ev.extras["errorCode"] == code


@pytest.mark.parametrize(
    "source", ["customer", "business", "bank", "issuer_bank", "gateway", "network",
               "customer_psp", "beneficiary_bank", "razorpay", "internal", "issuer"],
)
def test_every_documented_error_source_is_handled(source):
    """Razorpay's error_source vocabulary differs per payment method. An
    unrecognised one must not crash or silently become customer-side."""
    ev = normalize_payment(payment(error_reason="brand_new_reason", error_source=source),
                           now=NOW, source="razorpay")
    assert ev is not None
    if source == "business":
        assert ev.merchant_side is True, "business-sourced failures are ours, not the customer's"
    else:
        assert ev.merchant_side is False


@pytest.mark.parametrize(
    "step", ["payment_initiation", "card_enrollment_check", "payment_authentication",
             "payment_authorization", "payment_capture", "mandate_creation",
             "payment_debit_request", "payment_status_response", "refund_request"],
)
def test_every_documented_error_step_is_carried_through(step):
    ev = normalize_payment(payment(error_step=step), now=NOW, source="razorpay")
    assert ev.extras["errorStep"] == step


def test_an_unknown_future_reason_degrades_instead_of_crashing():
    """Razorpay adds reason strings. An unmapped one must land somewhere
    sensible and be flagged low-confidence, not raise."""
    c = classify("some_reason_invented_next_year", "gateway")
    assert c.family.code == "GATEWAY_ERROR" and c.confidence == "low"
    ev = normalize_payment(payment(error_reason="some_reason_invented_next_year", error_source="gateway"),
                           now=NOW, source="razorpay")
    assert ev.reason_confidence == "low" and ev.ambiguous is True


# ---------------------------------------------------------- payment methods


@pytest.mark.parametrize("method", ["card", "netbanking", "wallet", "upi", "emi"])
def test_every_payment_method_normalises(method):
    p = payment(method=method)
    if method != "card":
        p["card"] = None
        p["card_id"] = None
    if method == "netbanking":
        p["bank"] = "HDFC"
    if method == "wallet":
        p["wallet"] = "payzapp"
    if method == "upi":
        p["vpa"] = "gaurav.kumar@okhdfcbank"
    ev = normalize_payment(p, now=NOW, source="razorpay")
    assert ev is not None and ev.method


def test_upi_handles_resolve_to_the_right_psp():
    for handle, psp in [("okhdfcbank", "google_pay"), ("ybl", "phonepe"), ("paytm", "paytm")]:
        ev = normalize_payment(payment(method="upi", card=None, vpa=f"x@{handle}"), now=NOW, source="razorpay")
        assert ev.psp == psp, handle


def test_a_recurring_token_makes_it_a_subscription_leak_not_a_checkout_one():
    """A token_id means merchant-initiated, which changes the message class."""
    with_token = normalize_payment(payment(token_id="token_x"), now=NOW, source="razorpay")
    assert with_token.kind == "subscription_failure" and with_token.customer_initiated is False
    one_off = normalize_payment(payment(token_id=None, notes={}, customer_id=None), now=NOW, source="razorpay")
    assert one_off.kind == "checkout_abandonment" and one_off.customer_initiated is True


def test_non_failed_statuses_are_ignored():
    for status in ("created", "authorized", "captured", "refunded"):
        assert normalize_payment(payment(status=status), now=NOW, source="razorpay") is None


# ------------------------------------------------------- subscriptions, invoices


@pytest.mark.parametrize("status,expect", [("pending", True), ("halted", True),
                                           ("active", False), ("completed", False), ("cancelled", False)])
def test_subscription_states_that_signal_a_leak(status, expect):
    s = {"id": "sub_00000000000001", "entity": "subscription", "plan_id": "plan_00000000000001",
         "status": status, "customer_id": "cust_1", "payment_method": "card",
         "paid_count": 3, "remaining_count": 9, "charge_at": int(NOW.timestamp())}
    plans = {"plan_00000000000001": {"item": {"amount": 60000, "name": "Test plan"}}}
    ev = normalize_subscription(s, now=NOW, source="razorpay", plan_lookup=plans)
    assert (ev is not None) is expect
    if ev:
        assert ev.amount_paise == 60000 and ev.kind == "subscription_failure"


@pytest.mark.parametrize("status,expect", [("issued", True), ("partially_paid", True),
                                           ("paid", False), ("cancelled", False), ("expired", False),
                                           ("draft", False)])
def test_invoice_states_that_signal_a_receivable(status, expect):
    inv = {"id": "inv_1", "entity": "invoice", "status": status, "amount": 250000,
           "amount_due": 250000, "amount_paid": 0,
           "expire_by": int((NOW - timedelta(days=30)).timestamp()),
           "customer_details": {"name": "A", "contact": "9000090000", "email": "a@b.co"},
           "line_items": [{"name": "Order"}], "notes": {}}
    ev = InvoiceSource.normalize(inv, now=NOW, merchant=MerchantConfig())
    assert (ev is not None) is expect, status


def test_a_partially_paid_invoice_chases_only_the_remaining_balance():
    inv = {"id": "inv_2", "status": "partially_paid", "amount": 250000, "amount_due": 100000,
           "amount_paid": 150000, "expire_by": int((NOW - timedelta(days=20)).timestamp()),
           "customer_details": {}, "line_items": [{"name": "Order"}], "notes": {}}
    ev = InvoiceSource.normalize(inv, now=NOW, merchant=MerchantConfig())
    assert ev.amount_paise == 100000, "we must chase the balance, not the original total"


# ------------------------------------------------------------- downtime feed


REAL_DOWNTIMES = [
    {"id": "down_SixfwG8hES0hCJ", "entity": "payment.downtime", "method": "netbanking",
     "begin": 1777389900, "end": None, "status": "started", "scheduled": False,
     "severity": "high", "instrument": {"bank": "DLXB"}, "created_at": 1777389945, "updated_at": 1777389945},
    {"id": "down_T8xxbch1uvYaeg", "entity": "payment.downtime", "method": "card",
     "begin": 1783067791, "end": None, "status": "started", "scheduled": False,
     "severity": "high", "instrument": {"issuer": "BKID"}, "created_at": 1783067791, "updated_at": 1783067791},
    {"id": "down_TLffRyzbmbOFb5", "entity": "payment.downtime", "method": "upi",
     "begin": 1785841792, "end": None, "status": "started", "scheduled": False,
     "severity": "high", "instrument": {"vpa_handle": "kotak811"}, "created_at": 1785841792, "updated_at": 1785841792},
    {"id": "down_TYIYgpDiVBLtm5", "entity": "payment.downtime", "method": "fpx",
     "begin": 1788598832, "end": None, "status": "started", "scheduled": False,
     "severity": "high", "instrument": {"bank": "BNPA_C"}, "created_at": 1788598832, "updated_at": 1788598832},
]


class _Feed:
    def __init__(self, items): self._items = items
    class _P:
        def __init__(self, o): self.o = o
        def fetchDownTime(self): return {"entity": "collection", "count": len(self.o._items), "items": self.o._items}
    @property
    def payment(self): return _Feed._P(self)


def test_the_real_downtime_payloads_parse_verbatim():
    """These are records captured from a live Razorpay test account."""
    view = DegradationMonitor(_Feed(REAL_DOWNTIMES)).view()
    assert view.public()["live"] == 4
    keys = {c.key for c in view.cohorts}
    assert keys == {"netbanking:bank=DLXB", "card:issuer=BKID",
                    "upi:vpa_handle=kotak811", "fpx:bank=BNPA_C"}
    assert all(c.source == "razorpay" and c.severity == "high" for c in view.cohorts)


@pytest.mark.parametrize("field", ["issuer", "bank", "vpa_handle", "psp", "network", "wallet", "card_type", "flow"])
def test_every_documented_instrument_field_produces_a_cohort_key(field):
    key = cohort_key("card", {field: "XYZ"})
    assert key.startswith("card:") and key != "card:*" or field in ("card_type", "flow")


@pytest.mark.parametrize("status", ["scheduled", "started", "updated", "resolved"])
def test_every_documented_downtime_status_is_handled(status):
    item = dict(REAL_DOWNTIMES[0], status=status, end=None if status != "resolved" else 1785845000)
    view = DegradationMonitor(_Feed([item])).view()
    cohort = view.cohorts[0]
    assert cohort.live is (status in ("started", "scheduled"))


# ------------------------------------------------------------------ webhooks


def test_the_webhook_event_vocabulary_matches_razorpays():
    """Every event we act on must be one Razorpay actually emits."""
    published = {
        "order.paid", "payment.authorized", "payment.captured", "payment.failed",
        "payment.downtime.started", "payment.downtime.resolved", "payment.downtime.updated",
        "invoice.partially_paid", "invoice.paid", "invoice.expired",
        "subscription.authenticated", "subscription.activated", "subscription.charged",
        "subscription.completed", "subscription.updated", "subscription.pending",
        "subscription.halted", "subscription.cancelled", "subscription.paused", "subscription.resumed",
        "payment_link.paid", "payment_link.partially_paid", "payment_link.cancelled", "payment_link.expired",
        "virtual_account.created", "virtual_account.credited", "virtual_account.closed",
        "refund.created", "refund.processed", "settlement.processed",
    }
    ours = set(RECOVERY_EVENTS) | set(CHURN_EVENTS) | set(DEGRADATION_EVENTS)
    assert ours <= published, f"we act on events Razorpay does not emit: {ours - published}"


# ----------------------------------------------------------------- exports


def test_a_dashboard_csv_export_parses():
    csv = (
        "Payment Id,Amount,Status,Method,Error Code,Error Description,Error Reason,Error Source,Error Step,Created At,Contact,Email\n"
        "pay_1,1299.00,failed,card,BAD_REQUEST_ERROR,Card expired,card_expired,customer,payment_authorization,2026-09-05 12:00:00,+919999999999,a@b.c\n"
        "pay_2,499.50,failed,upi,GATEWAY_ERROR,Bank offline,bank_not_available,gateway,payment_authorization,2026-09-05 12:05:00,+919999999998,c@d.e\n"
    )
    rows, warnings = parse_export(csv.encode(), "payments.csv")
    assert len(rows) == 2 and not warnings
    a = normalize_payment(rows[0], now=NOW, source="file")
    assert a.amount_paise == 129900 and a.reason_code == "CARD_EXPIRED" and a.hard_decline
    b = normalize_payment(rows[1], now=NOW, source="file")
    assert b.amount_paise == 49950, "rupee decimals must convert exactly to paise"
    assert b.reason_code == "ISSUER_DOWN"


def test_an_api_json_export_parses_in_both_shapes():
    items = [payment(), payment(id="pay_2")]
    rows, _ = parse_export(json.dumps({"entity": "collection", "count": 2, "items": items}).encode(), "x.json")
    assert len(rows) == 2
    rows, _ = parse_export(json.dumps(items).encode(), "x.json")
    assert len(rows) == 2, "a bare list must parse too"


def test_magic_checkout_abandoned_cart_payload_normalises():
    payload = {
        "cart_token": "tok_1", "email": "a@b.c", "phone": "+919999999999",
        "line_items": [{"name": "Linen shirt", "price": 189900, "quantity": 1,
                        "product_id": "p1", "variant_id": "v1", "sku": "SKU1"}],
        "line_items_total": 189900, "currency": "INR",
        "abandoned_checkout_url": "https://shop.example.in/checkout/tok_1",
        "promotions": [], "utm_parameters": {"utm_source": "instagram"},
        "created_at": int((NOW - timedelta(minutes=25)).timestamp()),
    }
    ev = normalize_abandoned_cart(payload, now=NOW, merchant=MerchantConfig())
    assert ev.amount_paise == 189900 and ev.customer_initiated is True
    assert ev.extras["abandoned_checkout_url"].endswith("tok_1")
