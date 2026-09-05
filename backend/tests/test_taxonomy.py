"""Reason families and the Razorpay error_reason mapping."""

from __future__ import annotations

from app.sim import REASON_INDEX, REASONS
from app.taxonomy import FAMILIES, FAMILY_CODES, REASON_TO_FAMILY, classify


def test_every_razorpay_reason_maps_to_exactly_one_family():
    seen: dict[str, str] = {}
    for fam in FAMILIES.values():
        for reason in fam.razorpay_reasons:
            assert reason not in seen, f"{reason} in both {seen[reason]} and {fam.code}"
            seen[reason] = fam.code
    assert len(REASON_TO_FAMILY) == len(seen) >= 100


def test_families_and_simulator_agree():
    assert set(FAMILY_CODES) == {r[0] for r in REASONS}
    assert list(REASON_INDEX) == list(FAMILY_CODES)
    assert "INSTRUMENT_BLOCKED" in FAMILY_CODES and "MERCHANT_CONFIG" in FAMILY_CODES and "CUSTOMER_CANCELLED" in FAMILY_CODES


def test_hard_families_are_deterministic_from_the_reason():
    assert classify("debit_instrument_blocked").family.hard_decline is True
    assert classify("card_expired").family.hard_decline is True
    assert classify("payment_method_not_enabled").family.merchant_side is True
    assert classify("insufficient_funds").family.code == "INSUFFICIENT_FUNDS"
    assert classify("payment_cancelled").family.code == "CUSTOMER_CANCELLED"


def test_unknown_reasons_fall_back_by_source():
    c = classify("something_new", "business")
    assert c.family.code == "MERCHANT_CONFIG" and c.matched_by == "error_source"
    c = classify(None, "gateway")
    assert c.family.code == "GATEWAY_ERROR" and c.confidence == "low"
    c = classify("", "customer", "Your card has expired")
    assert c.family.code == "CARD_EXPIRED" and c.matched_by == "description"


def test_side_vocabulary_is_closed():
    assert {f.side for f in FAMILIES.values()} <= {"customer", "issuer", "risk", "merchant"}


def test_llm_classification_is_advisory_and_never_gates():
    """A model that can move a leak between policy paths is a model that can
    change what the gate does. It must not be able to."""
    from app.diagnosis import Diagnoser
    from app.retrieval import Corpus
    from app.sim import generate_events

    d = Diagnoser(Corpus())
    ev = next(e for e in generate_events(3, count=400) if e.ambiguous)
    gate_side = ev.failure_side

    # Pretend the model came back with a different side than the mapping.
    other = "merchant" if gate_side != "merchant" else "customer"
    d._cache[ev.reason_code] = {"failure_side": other, "latency_ms": 900, "note": "model said so."}

    out = d.diagnose(ev)
    assert out["method"] == "llm_fallback"
    assert out["failureSide"] == gate_side, "the gate's side must be the deterministic one"
    assert out["modelFailureSide"] == other
    assert out["modelAdvisory"] is True and out["disagreesWithGate"] is True
    assert "must not be able" in out["note"]

    # Agreement is not flagged as a disagreement.
    d._cache[ev.reason_code] = {"failure_side": gate_side, "latency_ms": 900, "note": "agrees."}
    agreed = d.diagnose(ev)
    assert agreed["disagreesWithGate"] is False and agreed["failureSide"] == gate_side

    # A deterministic lookup makes no model claim at all.
    plain = next(e for e in generate_events(3, count=400) if not e.ambiguous)
    det = d.diagnose(plain)
    assert det["modelFailureSide"] is None and det["modelAdvisory"] is False


def test_no_model_claim_is_made_when_no_model_answered():
    """Both non-model paths — no API key, and a failed call — must report no
    model opinion. Echoing the deterministic side back would render a
    'model reads … advisory' row in the console for a call that never happened."""
    from app.diagnosis import Diagnoser
    from app.retrieval import Corpus
    from app.sim import generate_events

    ev = next(e for e in generate_events(3, count=400) if e.ambiguous)

    no_key = Diagnoser(Corpus())
    assert no_key.api_key == "", "the suite runs without credentials"
    out = no_key.diagnose(ev)
    assert out["failureSide"] == ev.failure_side
    assert out["modelFailureSide"] is None
    assert out["modelAdvisory"] is False and out["disagreesWithGate"] is False

    failed = Diagnoser(Corpus())
    failed.api_key = "present-but-the-call-fails"
    failed._cache[ev.reason_code] = {"failure_side": None, "latency_ms": 120, "note": "call failed."}
    out = failed.diagnose(ev)
    assert out["modelFailureSide"] is None and out["modelAdvisory"] is False
