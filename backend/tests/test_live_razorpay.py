"""Integration tests that talk to a real Razorpay account.

Everything else in the suite is hermetic. This file is the opposite: it is only
meaningful when real credentials are present, and it proves the claims that a
mock cannot — that our request shapes are accepted by Razorpay's live API, that
the entities it returns parse, and that the objects we create can be read back.

Run with:      RECOVEROPS_LIVE=1 pytest tests/test_live_razorpay.py -v
Skipped by default, and skipped entirely without test-mode keys, so CI and a
reviewer's clone stay hermetic.

Everything created here is test-mode and tagged `recoverops: conformance` in
its notes, so it is identifiable and disposable.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

LIVE = os.environ.get("RECOVEROPS_LIVE") == "1"


def _load_env() -> dict:
    """Read backend/.env directly — conftest deliberately strips credentials
    from the environment so the rest of the suite stays hermetic."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return {}
    out = {}
    for m in re.finditer(r"^(\w+)=(.*)$", path.read_text(), re.M):
        out[m.group(1)] = m.group(2).strip()
    return out


ENV = _load_env()
HAS_KEYS = bool(ENV.get("RAZORPAY_KEY_ID") and ENV.get("RAZORPAY_KEY_SECRET"))

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not LIVE, reason="set RECOVEROPS_LIVE=1 to run live-API tests"),
    pytest.mark.skipif(not HAS_KEYS, reason="no Razorpay keys in backend/.env"),
]


@pytest.fixture(scope="module")
def client():
    import razorpay

    key_id = ENV["RAZORPAY_KEY_ID"]
    assert key_id.startswith("rzp_test_"), "refusing to run against a live-mode key"
    return razorpay.Client(auth=(key_id, ENV["RAZORPAY_KEY_SECRET"]))


NOTES = {"recoverops": "conformance", "created_by": "test-suite"}


# ------------------------------------------------------------- credentials


def test_the_configured_key_is_test_mode(client):
    """A live-mode key here would create real charges. Fail loudly."""
    assert ENV["RAZORPAY_KEY_ID"].startswith("rzp_test_")


def test_credentials_authenticate(client):
    page = client.payment.all({"count": 1})
    assert isinstance(page, dict) and "items" in page


# ------------------------------------------------- the shapes we depend on


def test_the_live_downtime_feed_matches_the_shape_we_parse(client):
    """This drives DEGRADATION_HOLD, which stops real customer contact. If
    Razorpay changes the payload, holds silently stop working — so the shape is
    asserted against the live endpoint, not a fixture."""
    from app.degradation import DegradationMonitor

    page = client.payment.fetchDownTime()
    assert "items" in page
    for item in page["items"]:
        assert item["entity"] == "payment.downtime"
        assert {"id", "method", "begin", "status", "severity"} <= set(item)
        assert item["status"] in ("scheduled", "started", "updated", "resolved")
        assert item["severity"] in ("high", "medium", "low")
        assert isinstance(item.get("instrument", {}), dict)

    view = DegradationMonitor(client).view()
    assert len(view.cohorts) == len(page["items"]), "every declared downtime must become a cohort"
    for c in view.cohorts:
        assert c.key and c.source == "razorpay" and c.began_at


def test_live_payments_parse_into_leaks(client):
    """Whatever the account holds, every failed payment must normalise."""
    from app.sources import normalize_payment

    now = datetime.now(timezone.utc)
    page = client.payment.all({"count": 100})
    failed = [p for p in page.get("items", []) if str(p.get("status")) == "failed"]
    if not failed:
        pytest.skip("account has no failed payments to parse")
    for p in failed:
        ev = normalize_payment(p, now=now, source="razorpay")
        assert ev is not None and ev.amount_paise > 0 and ev.reason_code


def test_live_invoices_parse_into_receivables(client):
    from app.merchant import MerchantConfig
    from app.receivables import InvoiceSource

    now = datetime.now(timezone.utc)
    page = client.invoice.all({"count": 100})
    parsed = 0
    for inv in page.get("items", []):
        assert inv["entity"] == "invoice"
        assert inv["status"] in ("draft", "issued", "partially_paid", "paid", "cancelled", "expired", "deleted")
        if InvoiceSource.normalize(inv, now=now, merchant=MerchantConfig()):
            parsed += 1
    assert page.get("count") is not None


# --------------------------------------------- the calls the executor makes


def test_the_executor_can_create_a_real_order(client):
    """Proves our request body is accepted, not merely that we can build one."""
    order = client.order.create({
        "amount": 49900, "currency": "INR",
        "receipt": f"recoverops_test_{int(time.time())}"[:40], "notes": NOTES,
    })
    assert order["id"].startswith("order_") and order["amount"] == 49900
    assert client.order.fetch(order["id"])["id"] == order["id"], "created order must be readable back"


def test_the_executor_can_create_a_real_payment_link_with_notify(client):
    """The exact payload the executor sends, including notify and reminders —
    the fields most likely to be rejected if we got the schema wrong.

    Razorpay caps payment links at 30 for the lifetime of a test account, and
    cancelling them does not free the quota. That ceiling is an account state,
    not a conformance failure, so it skips with the reason stated —
    test_the_executor_degrades_when_razorpay_refuses covers what the product
    does when it happens for real."""
    try:
        link = client.payment_link.create({
            "amount": 129900, "currency": "INR",
            "description": "RecoverOps conformance check",
            "notify": {"sms": False, "email": False},
            "reminder_enable": True,
            "notes": NOTES,
        })
    except Exception as exc:  # noqa: BLE001
        if "limit" in str(exc).lower():
            pytest.skip(f"Razorpay test-mode payment-link quota reached: {exc}")
        raise
    assert link["id"].startswith("plink_")
    assert link["status"] in ("created", "issued")
    assert link["short_url"].startswith("https://")
    fetched = client.payment_link.fetch(link["id"])
    assert fetched["amount"] == 129900
    client.payment_link.cancel(link["id"])  # leave the account tidy


def test_the_executor_can_create_an_invoice_with_partial_payment(client):
    """Receivables depend on partial_payment and expire_by being accepted."""
    inv = client.invoice.create({
        "type": "invoice",
        "description": "RecoverOps conformance check",
        "customer": {"name": "Conformance Buyer", "contact": "+919900000001", "email": "conformance@example.com"},
        "line_items": [{"name": "Test line", "amount": 250000, "currency": "INR", "quantity": 1}],
        "expire_by": int(time.time()) + 7 * 86400,
        "sms_notify": 0, "email_notify": 0,
        "partial_payment": True,
        "notes": NOTES,
    })
    assert inv["id"].startswith("inv_") and inv["status"] == "issued"
    assert inv["amount"] == 250000
    client.invoice.cancel(inv["id"])


def test_a_bad_request_is_rejected_by_razorpay_not_by_us(client):
    """Confirms we surface Razorpay's own validation rather than pre-empting it
    with assumptions that may drift from their rules."""
    import razorpay

    with pytest.raises(razorpay.errors.BadRequestError):
        client.order.create({"amount": -100, "currency": "INR"})


# ------------------------------------------------- the pipeline, end to end


def test_a_full_batch_runs_against_the_live_account(tmp_path):
    """The whole pipeline with real credentials: real downtime cohorts, real
    executor calls, a persisted ledger and an intact audit chain."""
    for k, v in ENV.items():
        os.environ[k] = v
    try:
        from app.runtime import Runtime

        rt = Runtime.build(store_path=tmp_path / "live.db")
        assert rt.executor.client is not None, "executor should be live with keys present"
        assert rt.degradation.feed.available is True

        summary = rt.run_and_store("simulator", seed=555, count=120)
        batch = rt.store.get_batch(summary["batchId"])

        assert batch["eventCount"] == 120
        assert rt.store.verify_audit()["ok"] is True

        # Real cohorts from the account should be attached to the batch.
        deg = batch.get("degradation") or {}
        assert deg.get("feedAvailable") is True

        # Some executions should be genuine API calls, capped per batch.
        traces = [rt.store.get_trace(e["eventId"], summary["batchId"]) for e in batch["events"]]
        real_calls = [t for t in traces if t["agentB"]["execution"].get("externalId")]
        assert len(real_calls) <= rt.executor.max_live_calls, "the per-batch call cap was exceeded"
        for t in real_calls:
            ext = t["agentB"]["execution"]
            assert ext["externalKind"] in ("order", "payment_link")
            assert ext["externalId"].startswith(("order_", "plink_"))
    finally:
        for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "ANTHROPIC_API_KEY", "SARVAM_API_KEY"):
            os.environ.pop(k, None)


@pytest.mark.skipif(not ENV.get("ANTHROPIC_API_KEY"), reason="no Anthropic key")
def test_the_diagnosis_model_answers_and_stays_advisory():
    """The model must return a valid side and must not be able to change the
    side the gate ran on."""
    os.environ["ANTHROPIC_API_KEY"] = ENV["ANTHROPIC_API_KEY"]
    try:
        from app.diagnosis import Diagnoser
        from app.retrieval import Corpus
        from app.sim import generate_events

        d = Diagnoser(Corpus())
        ev = next(e for e in generate_events(3, count=400) if e.ambiguous)
        out = d.diagnose(ev)
        assert out["method"] == "llm_fallback"
        assert out["latencyMs"] > 0, "a real call takes measurable time"
        assert out["modelFailureSide"] in ("customer", "issuer", "risk", "merchant")
        assert out["failureSide"] == ev.failure_side, "the gate must use the deterministic side"
        assert out["modelAdvisory"] is True
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


@pytest.mark.skipif(not ENV.get("SARVAM_API_KEY"), reason="no Sarvam key")
def test_sarvam_returns_real_hinglish_audio_and_transcribes_it_back():
    """A round trip: synthesise a Hinglish sentence, transcribe it, and confirm
    the transcript still classifies as a promise — the property the voice agent
    actually depends on."""
    os.environ["SARVAM_API_KEY"] = ENV["SARVAM_API_KEY"]
    try:
        import base64

        from app.voice import SarvamVoice, classify_intent

        v = SarvamVoice()
        assert v.live is True
        b64, mocked, latency, note = v.speak("Main kal tak payment kar dunga")
        assert not mocked and b64, note
        wav = base64.b64decode(b64)
        assert wav[:4] == b"RIFF" and len(wav) > 10_000, "expected real WAV audio"
        assert latency > 0

        transcript, stt_mocked, _ = v.transcribe(wav)
        assert not stt_mocked and transcript, "speech-to-text returned nothing"
        intent, _conf = classify_intent(transcript)
        assert intent == "promise", f"a transcribed promise classified as {intent}: {transcript!r}"
    finally:
        os.environ.pop("SARVAM_API_KEY", None)
