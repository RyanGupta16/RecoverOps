"""Reason families: the bridge between Razorpay's error taxonomy and ours.

A real failed payment arrives with ``error_code`` (``BAD_REQUEST_ERROR`` or
``GATEWAY_ERROR``), ``error_source``, ``error_step`` and ``error_reason`` — about
115 distinct reason strings across methods (razorpay.com/docs/errors/payments/list).
The pipeline reasons about thirteen *families*. Ten were modelled on this
taxonomy from the start; three exist only because real data has them:

- ``CUSTOMER_CANCELLED``  the customer explicitly abandoned the payment flow.
- ``INSTRUMENT_BLOCKED``  a hard decline for the instrument — blocked, inactive,
  closed or malformed. Never retried; only an instrument change can fix it.
- ``MERCHANT_CONFIG``     ``error_source = business``: the merchant caused it.
  Not the customer's problem, so zero contact; a spike is a deploy bug.

Both new hard families are deterministic from the reason string, which is
exactly why a probability-ranked system gets them wrong: it messages a customer
about a decline the merchant caused, or retries a closed account fourteen
times. The gate is correct on them from the first real event.

Every family also carries the simulator's weight and segment prior, so the
generator and the real-data normaliser describe the same world.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SIDES = ("customer", "issuer", "risk", "merchant")


@dataclass(frozen=True)
class Family:
    code: str
    label: str
    side: str  # customer | issuer | risk | merchant
    sim_weight: int  # relative frequency in the simulator
    prior: tuple[float, float, float, float]  # over (sure_thing, persuadable, lost_cause, sleeping_dog)
    ambiguous: bool = False  # opaque code → diagnosis layer / LLM
    retriable: bool = True  # a silent retry can, in principle, succeed
    hard_decline: bool = False  # never retry the same instrument / mandate
    merchant_side: bool = False  # merchant caused it → no customer contact
    razorpay_reasons: tuple[str, ...] = field(default_factory=tuple)


FAMILIES: dict[str, Family] = {
    f.code: f
    for f in (
        Family(
            "INSUFFICIENT_FUNDS", "Insufficient balance", "customer", 24, (0.16, 0.44, 0.26, 0.14),
            razorpay_reasons=("insufficient_funds",),
        ),
        Family(
            "CARD_EXPIRED", "Card expired or reissued", "customer", 12, (0.07, 0.55, 0.26, 0.12),
            retriable=False, hard_decline=True,
            razorpay_reasons=("card_expired",),
        ),
        Family(
            "DO_NOT_HONOUR", "Declined by issuer (do not honour)", "issuer", 14, (0.29, 0.24, 0.38, 0.09),
            ambiguous=True,
            razorpay_reasons=("card_declined", "debit_declined", "payment_declined", "authorisation_declined_by_psp"),
        ),
        Family(
            "ISSUER_DOWN", "Issuer or gateway unavailable", "issuer", 11, (0.63, 0.09, 0.22, 0.06),
            razorpay_reasons=(
                "bank_not_available", "bank_technical_error", "issuer_technical_error", "bank_cutoff_in_progress",
                "psp_app_not_available", "psp_not_available", "upi_app_technical_error", "psp_app_not_supported",
            ),
        ),
        Family(
            "PAYMENT_TIMEOUT", "Authorisation timed out", "issuer", 8, (0.47, 0.16, 0.31, 0.06),
            razorpay_reasons=(
                "payment_timed_out", "request_timed_out", "payment_session_expired", "payment_collect_request_expired",
                "collect_request_pending", "mandate_creation_timeout", "payment_pending",
            ),
        ),
        Family(
            "INVALID_AUTH_DATA", "Invalid CVV or authentication data", "customer", 7, (0.10, 0.45, 0.35, 0.10),
            retriable=False,
            razorpay_reasons=(
                "incorrect_cvv", "incorrect_otp", "incorrect_pin", "incorrect_atm_pin", "incorrect_card_details",
                "incorrect_card_expiry_date", "incorrect_cardholder_name", "authentication_failed", "otp_expired",
                "otp_attempts_exceeded", "pin_attempts_exceeded", "invalid_user_details", "invalid_mobile_number",
                "mobile_number_invalid", "invalid_vpa",
            ),
        ),
        Family(
            "MANDATE_REVOKED", "e-Mandate revoked by customer", "customer", 5, (0.04, 0.13, 0.75, 0.08),
            retriable=False, hard_decline=True,
            razorpay_reasons=(
                "mandate_not_active", "payment_mandate_not_active", "reqauth_mandate_not_acknowledged",
                "funds_blocked_by_mandate",
            ),
        ),
        Family(
            "AUTH_LIMIT_EXCEEDED", "Per-transaction limit exceeded", "customer", 6, (0.13, 0.47, 0.29, 0.11),
            razorpay_reasons=(
                "transaction_limit_exceeded", "transaction_daily_limit_exceeded", "transaction_daily_count_exceeded",
                "transaction_frequency_limit_exceeded", "credit_limit_exceeded", "mcc_amount_limit_exceeded",
                "emi_greater_than_max_amount",
            ),
        ),
        Family(
            "SUSPECTED_FRAUD", "Suspected fraud hold", "risk", 3, (0.02, 0.03, 0.93, 0.02),
            retriable=False, hard_decline=True,
            razorpay_reasons=("payment_risk_check_failed", "compliance_violation"),
        ),
        Family(
            "GATEWAY_ERROR", "Gateway-side error", "issuer", 10, (0.56, 0.13, 0.25, 0.06),
            ambiguous=True,
            razorpay_reasons=(
                "gateway_technical_error", "invalid_response_from_gateway", "payment_failed", "server_error",
                "payment_declined_due_to_high_traffic", "verification_failed", "duplicate_rrn_found",
                "vpa_resolution_failed", "deemed_transaction", "credit_failed", "capture_failed",
            ),
        ),
        Family(
            "CUSTOMER_CANCELLED", "Customer cancelled the payment", "customer", 3, (0.08, 0.30, 0.52, 0.10),
            retriable=False,
            razorpay_reasons=("payment_cancelled",),
        ),
        Family(
            "INSTRUMENT_BLOCKED", "Instrument blocked, inactive or invalid", "customer", 4, (0.02, 0.20, 0.70, 0.08),
            retriable=False, hard_decline=True,
            razorpay_reasons=(
                "debit_instrument_blocked", "debit_instrument_inactive", "bank_account_invalid",
                "bank_account_validation_failed", "card_number_invalid", "card_not_enrolled", "card_type_invalid",
                "transaction_on_vpa_restricted", "psp_not_registered", "user_not_registered_for_netbanking",
                "credit_limit_expired", "credit_limit_inactive", "credit_limit_not_approved", "credit_not_permitted",
                "user_not_eligible", "international_transaction_not_allowed", "pin_not_set",
            ),
        ),
        # Non-payment leak kinds. They carry no Razorpay error_reason — the leak
        # is the absence of a payment, not a failed one — but they flow through
        # the same families machinery so the gate and the value model need no
        # special cases.
        Family(
            "RECEIVABLE_OVERDUE", "Invoice past its due date", "customer", 0, (0.10, 0.45, 0.35, 0.10),
            retriable=False,
        ),
        Family(
            "CHECKOUT_ABANDONED", "Checkout started and abandoned", "customer", 0, (0.12, 0.46, 0.34, 0.08),
            retriable=False,
        ),
        Family(
            "MERCHANT_CONFIG", "Merchant-side configuration error", "merchant", 3, (0.70, 0.05, 0.22, 0.03),
            merchant_side=True,
            razorpay_reasons=(
                "amount_less_than_minimum_amount", "bank_not_enabled", "card_network_not_enabled", "duplicate_request",
                "input_validation_failed", "invalid_amount", "invalid_currency", "invalid_email", "invalid_order_id",
                "invalid_request", "live_mode_not_enabled", "merchant_not_activated", "mismatch_in_transaction_details",
                "order_already_paid", "order_payment_method_mismatch", "order_amount_mismatch",
                "payment_method_not_enabled", "recurring_payment_not_enabled", "upi_collect_not_enabled",
                "upi_intent_not_enabled", "collect_on_mcc_blocked", "upi_autopay_not_supported_on_psp",
                "record_not_found", "payment_amount_tampered", "duplicate_refund_id",
                "beneficiary_account_does_not_exist", "beneficiary_account_dormant", "payment_pending_approval",
                "refund_limit_crossed", "emi_plan_unavailable", "invalid_device",
            ),
        ),
    )
}

FAMILY_CODES: tuple[str, ...] = tuple(FAMILIES)

REASON_TO_FAMILY: dict[str, str] = {
    reason: fam.code for fam in FAMILIES.values() for reason in fam.razorpay_reasons
}

# Razorpay's error_source values → our side, for reasons the table does not know.
SOURCE_TO_SIDE: dict[str, str] = {
    "customer": "customer",
    "business": "merchant",
    "bank": "issuer",
    "issuer_bank": "issuer",
    "issuer": "issuer",
    "beneficiary_bank": "issuer",
    "gateway": "issuer",
    "network": "issuer",
    "customer_psp": "issuer",
    "razorpay": "issuer",
    "internal": "issuer",
}


@dataclass(frozen=True)
class Classification:
    family: Family
    confidence: str  # high | medium | low
    matched_by: str  # error_reason | error_source | description | default
    note: str


def classify(
    error_reason: str | None,
    error_source: str | None = None,
    error_description: str | None = None,
) -> Classification:
    """Map a real failure to a family.

    Exact ``error_reason`` match is high confidence. With no known reason the
    source decides: ``business`` is merchant-side (deterministic — the merchant
    can read its own request), anything bank/gateway/network-side is an
    ambiguous gateway error the diagnosis layer gets to look at, and an unknown
    customer-side reason is treated as an opaque decline rather than guessed.
    """
    reason = (error_reason or "").strip().lower()
    if reason in REASON_TO_FAMILY:
        fam = FAMILIES[REASON_TO_FAMILY[reason]]
        return Classification(fam, "high", "error_reason", f"error_reason `{reason}` → {fam.code}.")

    desc = (error_description or "").lower()
    for needle, code in (
        ("insufficient", "INSUFFICIENT_FUNDS"),
        ("expired card", "CARD_EXPIRED"),
        ("card has expired", "CARD_EXPIRED"),
        ("do not honour", "DO_NOT_HONOUR"),
        ("do not honor", "DO_NOT_HONOUR"),
        ("limit", "AUTH_LIMIT_EXCEEDED"),
        ("mandate", "MANDATE_REVOKED"),
        ("fraud", "SUSPECTED_FRAUD"),
        ("risk", "SUSPECTED_FRAUD"),
        ("timed out", "PAYMENT_TIMEOUT"),
        ("timeout", "PAYMENT_TIMEOUT"),
        ("cancelled", "CUSTOMER_CANCELLED"),
        ("otp", "INVALID_AUTH_DATA"),
        ("cvv", "INVALID_AUTH_DATA"),
        ("blocked", "INSTRUMENT_BLOCKED"),
    ):
        if needle in desc:
            fam = FAMILIES[code]
            return Classification(fam, "medium", "description", f"description matched `{needle}` → {fam.code}.")

    source = (error_source or "").strip().lower()
    if source == "business":
        fam = FAMILIES["MERCHANT_CONFIG"]
        return Classification(fam, "high", "error_source", "error_source `business` — merchant-side by definition.")
    if source == "customer":
        fam = FAMILIES["DO_NOT_HONOUR"]
        return Classification(fam, "low", "error_source", f"unknown customer-side reason `{reason or '∅'}` — treated as an opaque decline.")
    fam = FAMILIES["GATEWAY_ERROR"]
    return Classification(fam, "low", "default", f"unknown reason `{reason or '∅'}` from source `{source or '∅'}` — ambiguous, sent to diagnosis.")


def sim_reasons() -> list[tuple[str, str, str, int, list[float], bool]]:
    """The generator's view: (code, label, side, weight, prior, ambiguous).

    Includes the zero-weight non-payment families so the feature vector's
    one-hot covers every leak kind; the payment-failure generator never draws
    them because their weight is 0."""
    return [(f.code, f.label, f.side, f.sim_weight, list(f.prior), f.ambiguous) for f in FAMILIES.values()]
