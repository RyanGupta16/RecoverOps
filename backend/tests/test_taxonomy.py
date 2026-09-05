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
