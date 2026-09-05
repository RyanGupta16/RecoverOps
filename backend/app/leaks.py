"""The LeakEvent: every rupee that is owed but not yet collected, as one object.

A failed subscription charge, a mandate that did not execute, an abandoned
checkout, an invoice past its due date — each is a leak with a counterparty,
an amount, a clock, an instrument, a reason and a channel history. Kind-specific
detail lives under ``extras``; everything the gate and the value model read is
at the top level so no rule ever reaches into kind-specific fields.

The synthetic ground truth (``segment``, ``truth``, ``u_recover``, ``u_churn``)
is optional and ``None`` on real data. That is the single most important
property of this type: the same pipeline runs on both, and every layer that
touches truth has to say what it does without it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

LEAK_KINDS = (
    "subscription_failure",
    "mandate_failure",
    "checkout_abandonment",
    "receivable_overdue",
    "degradation_cohort",
)

SOURCES = ("simulator", "razorpay", "file")

RECURRING_METHODS = ("card", "upi_autopay", "emandate")


@dataclass
class LeakEvent:
    # --- identity -----------------------------------------------------------
    event_id: str
    kind: str = "subscription_failure"
    source: str = "simulator"

    # --- Razorpay references ------------------------------------------------
    payment_id: str = ""
    subscription_id: str = ""
    invoice_id: str | None = None
    order_id: str | None = None
    customer_id: str = ""
    counterparty_type: str = "consumer"  # consumer | business
    # Raw contact is what the executor hands Razorpay for a payment link. It is
    # never written to the audit log — see contact_hash().
    contact: str | None = None
    email: str | None = None

    # --- money and clock ----------------------------------------------------
    failed_at: str = ""
    amount_paise: int = 0
    plan_name: str = ""
    minutes_since_failure: int = 0
    local_hour_ist: int = 12
    # TCCCPR 2025 cl. 2(bt): transactional only if the customer initiated the
    # transaction. A recurring auto-debit is merchant-initiated, so a failed
    # subscription charge is never transactional — it is a *service* message
    # about a product the customer holds (cl. 2(bh)(i)).
    customer_initiated: bool = False
    has_relationship: bool = True

    # --- instrument ---------------------------------------------------------
    method: str = "card"  # card | upi_autopay | emandate | netbanking | upi | wallet
    issuer: str = ""
    network: str | None = None  # Visa | MasterCard | RuPay | ...
    psp: str | None = None  # google_pay | phonepe | paytm | ...

    # --- reason -------------------------------------------------------------
    reason_code: str = "GATEWAY_ERROR"  # family code (taxonomy.FAMILIES)
    reason_label: str = ""
    failure_side: str = "issuer"  # customer | issuer | risk | merchant
    raw_reason: str | None = None  # Razorpay error_reason as received
    raw_source: str | None = None
    raw_description: str | None = None
    reason_confidence: str = "high"  # high | medium | low
    ambiguous: bool = False
    retriable: bool = True
    hard_decline: bool = False
    merchant_side: bool = False
    dispute_open: bool = False

    # --- history ------------------------------------------------------------
    attempts_this_cycle: int = 0
    contacts_last_7d: int = 0
    retries_30d: int = 0
    consent_on_file: bool = False
    consent_granted_days_ago: int | None = None
    dnd_registered: bool = False
    engagement: float = 0.5
    tenure_days: int = 90
    # How this counterparty responded the last time it was chased — the signal a
    # merchant's own dunning history carries and the learning loop builds from
    # real outcomes. "none" for a customer never contacted before.
    prior_nudge_response: str = "none"  # none | paid_after_nudge | paid_without_nudge | ignored | complained
    features_are_proxies: bool = False  # real data: engagement/tenure are estimates
    promise_until: str | None = None
    holdout: bool = False
    # Set by the engine before gating: the live degradation cohort holding this
    # leak's instrument, and the live promise holding its counterparty.
    degradation_hold: dict | None = None
    promise_hold: dict | None = None

    # --- synthetic ground truth (None on real data) -------------------------
    segment: str | None = None
    truth: tuple[float, float, float, float] | None = None
    u_recover: float | None = None
    u_churn: float | None = None

    extras: dict = field(default_factory=dict)

    # ------------------------------------------------------------------------

    @property
    def is_synthetic(self) -> bool:
        return self.truth is not None

    @property
    def is_recurring(self) -> bool:
        return self.method in RECURRING_METHODS and self.kind in ("subscription_failure", "mandate_failure")

    @property
    def is_mandate(self) -> bool:
        return self.method in ("upi_autopay", "emandate")

    @property
    def is_dues(self) -> bool:
        """Leak types that read as debt collection to the counterparty — the
        RBI recovery-agent contact window applies on top of TCCCPR."""
        return self.kind == "receivable_overdue" or bool(self.extras.get("broken_promise"))

    @property
    def days_overdue(self) -> int:
        """Days past the due date, for receivables. 0 when not yet due."""
        return int(self.extras.get("days_overdue", 0) or 0)

    @property
    def is_mse_supplier(self) -> bool:
        """Whether the MSMED statutory clock applies to this receivable."""
        return bool(self.extras.get("mse_supplier", False))

    def contact_hash(self) -> str | None:
        if not self.contact:
            return None
        return hashlib.sha256(self.contact.encode("utf-8")).hexdigest()[:16]

    def public_row(self) -> dict:
        """What may be persisted or shown: no raw contact details."""
        return {
            "eventId": self.event_id,
            "kind": self.kind,
            "source": self.source,
            "paymentId": self.payment_id,
            "subscriptionId": self.subscription_id,
            "invoiceId": self.invoice_id,
            "customerId": self.customer_id,
            "counterpartyType": self.counterparty_type,
            "contactHash": self.contact_hash(),
            "failedAt": self.failed_at,
            "amountPaise": self.amount_paise,
            "planName": self.plan_name,
            "customerName": self.extras.get("customer_name"),
            "method": self.method,
            "issuer": self.issuer,
            "network": self.network,
            "psp": self.psp,
            "reasonCode": self.reason_code,
            "reasonLabel": self.reason_label,
            "failureSide": self.failure_side,
            "rawReason": self.raw_reason,
            "reasonConfidence": self.reason_confidence,
            "hardDecline": self.hard_decline,
            "merchantSide": self.merchant_side,
            "minutesSinceFailure": self.minutes_since_failure,
            "attemptsThisCycle": self.attempts_this_cycle,
            "contactsLast7d": self.contacts_last_7d,
            "retries30d": self.retries_30d,
            "priorNudgeResponse": self.prior_nudge_response,
            "featuresAreProxies": self.features_are_proxies,
            "holdout": self.holdout,
            "synthetic": self.is_synthetic,
        }
