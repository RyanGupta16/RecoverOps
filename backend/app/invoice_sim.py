"""A synthetic invoice book, for demonstrating the receivables ladder.

Used when the merchant's Razorpay account has no overdue invoices — which is
the normal state of a fresh test account. The shape is identical to what
``receivables.InvoiceSource`` produces from real invoices, so the ladder, the
gate and the value model cannot tell the difference.

Payer archetypes are the point. A large buyer with a slow AP cycle is not a
lost cause; it is a sure thing that pays on day 52 whatever you send, and
chasing it on day 20 spends goodwill for nothing. That is the same causal
distinction as the subscription world, in a different costume.
"""

from __future__ import annotations

import hashlib
import string
from datetime import datetime, timedelta, timezone

import numpy as np

from .leaks import LeakEvent
from .merchant import MerchantConfig
from .receivables import ageing_bucket, ladder_step, msmed_deadline_days, msmed_interest_paise
from .sim import realize_truth
from .taxonomy import FAMILIES

IST = timezone(timedelta(hours=5, minutes=30))

# (archetype, weight, segment, typical days-to-pay, dispute rate)
ARCHETYPES = [
    ("enterprise_slow_ap", 0.26, "sure_thing", 55, 0.04),
    ("prompt_payer", 0.18, "sure_thing", 12, 0.02),
    ("cash_tight_sme", 0.28, "persuadable", 48, 0.06),
    ("chronic_late", 0.16, "persuadable", 72, 0.10),
    ("disputing", 0.07, "lost_cause", 95, 0.75),
    ("insolvent", 0.05, "lost_cause", 130, 0.15),
]

BUYERS = [
    "Meridian Retail Pvt Ltd", "Coastal Foods LLP", "Anand Traders", "Nimbus Hospitality",
    "Kalyani Logistics", "Sunrise Cafes Pvt Ltd", "Deccan Textiles", "Harbour Wholesale",
    "Vardhman Distributors", "Lotus Hotels Group",
]
LINE_ITEMS = [
    ("Wholesale order — 40 units", 250000),
    ("Wholesale order — 120 units", 780000),
    ("Monthly supply contract", 1450000),
    ("Bulk consignment", 3200000),
    ("Sample order", 68000),
    ("Quarterly retainer", 2100000),
]


def _rid(rng: np.random.Generator, prefix: str, n: int = 12) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return prefix + "_" + "".join(alphabet[i] for i in rng.integers(0, len(alphabet), n))


def generate_invoices(seed: int, count: int, merchant: MerchantConfig) -> list[LeakEvent]:
    rng = np.random.default_rng(seed)
    fam = FAMILIES["RECEIVABLE_OVERDUE"]
    now = datetime.now(timezone.utc)
    weights = np.array([a[1] for a in ARCHETYPES], dtype=float)
    weights /= weights.sum()
    out: list[LeakEvent] = []

    for i in range(count):
        archetype, _w, segment, typical_days, dispute_rate = ARCHETYPES[int(rng.choice(len(ARCHETYPES), p=weights))]
        buyer = BUYERS[int(rng.integers(0, len(BUYERS)))]
        item, base = LINE_ITEMS[int(rng.integers(0, len(LINE_ITEMS)))]
        amount = int(base * float(rng.uniform(0.7, 1.6)))
        has_agreement = bool(rng.random() < 0.6)
        # The seller is the MSE here — the statutory clock protects the supplier.
        is_mse = merchant.is_registered_mse
        deadline = msmed_deadline_days(has_agreement)
        days_overdue = max(0, int(rng.normal(typical_days, 18)) - deadline)
        if days_overdue == 0 and rng.random() < 0.5:
            days_overdue = int(rng.integers(1, 12))
        dispute_open = bool(rng.random() < dispute_rate)

        due = now - timedelta(days=days_overdue)
        # Engagement stands in for payment reliability: how often this buyer has
        # paid without being chased.
        engagement = float(np.clip(1.0 - (typical_days / 150.0) + rng.normal(0, 0.08), 0.05, 0.98))
        tenure = int(rng.integers(90, 1800))
        truth = realize_truth(segment, engagement, min(amount, 300000), tenure, "customer")
        inv_id = _rid(rng, "inv", 14)
        partial = int(amount * float(rng.uniform(0.1, 0.5))) if rng.random() < 0.18 else 0

        out.append(
            LeakEvent(
                event_id="evt_" + hashlib.sha256(f"invsim|{seed}|{i}|{inv_id}".encode()).hexdigest()[:12],
                kind="receivable_overdue",
                source="simulator",
                payment_id="",
                subscription_id="",
                invoice_id=inv_id,
                customer_id=_rid(rng, "biz", 10),
                counterparty_type="business",
                failed_at=due.isoformat(),
                amount_paise=amount - partial,
                plan_name=item,
                minutes_since_failure=max(0, days_overdue * 24 * 60),
                local_hour_ist=int(rng.integers(8, 20)),
                customer_initiated=False,
                has_relationship=True,
                method="netbanking",
                issuer="",
                reason_code=fam.code,
                reason_label=f"Invoice {days_overdue} days past due" if days_overdue else "Invoice due",
                failure_side=fam.side,
                reason_confidence="high",
                ambiguous=False,
                retriable=False,
                hard_decline=False,
                merchant_side=False,
                dispute_open=dispute_open,
                attempts_this_cycle=1,
                contacts_last_7d=0 if rng.random() < 0.7 else int(rng.integers(1, 3)),
                retries_30d=0,
                consent_on_file=True,
                engagement=round(engagement, 3),
                tenure_days=tenure,
                segment=segment,
                truth=truth,
                u_recover=float(rng.random()),
                u_churn=float(rng.random()),
                extras={
                    "days_overdue": days_overdue,
                    "ageing": ageing_bucket(days_overdue),
                    "mse_supplier": is_mse,
                    "written_agreement": has_agreement,
                    "statutory_deadline_days": deadline,
                    "statutory_interest_paise": msmed_interest_paise(amount - partial, days_overdue, deadline) if is_mse else 0,
                    "invoice_status": "partially_paid" if partial else "issued",
                    "amount_paid_paise": partial,
                    "archetype": archetype,
                    "typical_days_to_pay": typical_days,
                    "customer_name": buyer,
                    "ladder": ladder_step(days_overdue, is_mse, dispute_open),
                },
            )
        )
    return out
