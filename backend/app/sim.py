"""Shared taxonomy + synthetic event generation.

Mirrors scripts/generate-sample-batch.mjs on the frontend so a live batch and
the bundled demo batch describe the same world. The generator here is seeded
per-batch, so every run is a genuinely fresh draw while staying reproducible
given its seed.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

SEGMENTS = ["sure_thing", "persuadable", "lost_cause", "sleeping_dog"]

# Segment-level anchor for the potential outcomes:
# (p_recover_control, p_recover_treat, p_churn_control, p_churn_treat)
SEGMENT_TRUTH = {
    "sure_thing": (0.84, 0.88, 0.02, 0.03),
    "persuadable": (0.11, 0.58, 0.19, 0.07),
    "lost_cause": (0.03, 0.05, 0.55, 0.57),
    "sleeping_dog": (0.62, 0.44, 0.06, 0.38),
}


def realize_truth(segment: str, engagement: float, amount_paise: int, tenure_days: int, failure_side: str) -> tuple[float, float, float, float]:
    """Continuous per-event potential outcomes around the segment anchor.

    Real treatment effects are not four discrete points — they vary with the
    customer. This heterogeneity is what separates the estimators: the
    hand-specified priors only know (reason, engagement) and are therefore
    misspecified against it, while the learners see the full feature vector
    and must recover it from data. Whichever wins the benchmark earns it.

    - Persuadable uplift scales with engagement: an engaged customer acts on
      the nudge; a disengaged one lets the link expire.
    - Sleeping-dog damage scales with DISengagement: the barely-active
      subscriber is the one the reminder pushes into cancelling.
    - Large amounts dampen contact-driven recovery (bigger asks convert worse).
    - Tenure lifts quiet recovery for viable customers (long-standing accounts
      self-heal: balances refill, cards get replaced).
    - Issuer-side failures recover quietly more often regardless of segment.
    """
    p0, p1, c0, c1 = SEGMENT_TRUTH[segment]
    amount_damp = 1.0 - 0.30 * (amount_paise / 300000.0)
    tenure_lift = 0.08 * (tenure_days / 1200.0)

    if segment == "persuadable":
        p1 = p0 + (p1 - p0) * (0.45 + 1.1 * engagement) * amount_damp
        p0 = p0 + tenure_lift * 0.5
    elif segment == "sleeping_dog":
        damage = (0.35 + 0.9 * (1.0 - engagement)) * amount_damp
        p1 = p0 - (p0 - p1) * damage
        c1 = c0 + (c1 - c0) * (0.4 + 1.2 * (1.0 - engagement))
    elif segment == "sure_thing":
        p0 = p0 + tenure_lift
        p1 = p0 + 0.04 * amount_damp
    if failure_side == "issuer":
        p0 += 0.05

    clip = lambda v: float(min(0.97, max(0.01, v)))  # noqa: E731
    return clip(p0), clip(p1), clip(c0), clip(c1)

# code, label, side, weight, prior over segments, ambiguous (needs LLM)
REASONS = [
    ("INSUFFICIENT_FUNDS", "Insufficient balance", "customer", 24, [0.16, 0.44, 0.26, 0.14], False),
    ("CARD_EXPIRED", "Card expired or reissued", "customer", 12, [0.07, 0.55, 0.26, 0.12], False),
    ("DO_NOT_HONOUR", "Declined by issuer (do not honour)", "issuer", 14, [0.29, 0.24, 0.38, 0.09], True),
    ("ISSUER_DOWN", "Issuer or gateway unavailable", "issuer", 11, [0.63, 0.09, 0.22, 0.06], False),
    ("PAYMENT_TIMEOUT", "Authorisation timed out", "issuer", 8, [0.47, 0.16, 0.31, 0.06], False),
    ("INVALID_AUTH_DATA", "Invalid CVV or authentication data", "customer", 7, [0.10, 0.45, 0.35, 0.10], False),
    ("MANDATE_REVOKED", "e-Mandate revoked by customer", "customer", 5, [0.04, 0.13, 0.75, 0.08], False),
    ("AUTH_LIMIT_EXCEEDED", "Per-transaction limit exceeded", "customer", 6, [0.13, 0.47, 0.29, 0.11], False),
    ("SUSPECTED_FRAUD", "Suspected fraud hold", "risk", 3, [0.02, 0.03, 0.93, 0.02], False),
    ("GATEWAY_ERROR", "Gateway-side error", "issuer", 10, [0.56, 0.13, 0.25, 0.06], True),
]
REASON_INDEX = {r[0]: i for i, r in enumerate(REASONS)}

PLANS = [
    ("Standard monthly", 49900),
    ("Standard monthly", 49900),
    ("Pro monthly", 129900),
    ("Pro monthly", 129900),
    ("Team monthly", 299900),
    ("Lite monthly", 19900),
    ("Annual (monthly instalment)", 89900),
]
METHODS = ["card", "card", "card", "upi_autopay", "upi_autopay", "emandate", "netbanking"]
METHOD_KINDS = ["card", "upi_autopay", "emandate", "netbanking"]
ISSUERS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "IndusInd", "Yes Bank", "IDFC First"]

CONFIG = {
    "eventCount": 500,
    "contactBudget": 200,
    "contactCostPaise": 120,
    "churnResidualCycles": 3,
    "baselineProbabilityThreshold": 0.35,
    "upliftThreshold": 0.05,
}


@dataclass
class Event:
    event_id: str
    payment_id: str
    subscription_id: str
    customer_id: str
    failed_at: str
    amount_paise: int
    plan_name: str
    method: str
    issuer: str
    reason_code: str
    reason_label: str
    failure_side: str
    minutes_since_failure: int
    local_hour_ist: int
    attempts_this_cycle: int
    contacts_last_7d: int
    retries_30d: int
    consent_on_file: bool
    dnd_registered: bool
    engagement: float
    tenure_days: int
    segment: str
    truth: tuple[float, float, float, float]
    u_recover: float
    u_churn: float
    ambiguous: bool
    extras: dict = field(default_factory=dict)


def _rid(rng: np.random.Generator, prefix: str, n: int = 14) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return prefix + "_" + "".join(alphabet[i] for i in rng.integers(0, len(alphabet), n))


def _draw_segment(rng: np.random.Generator, prior: list[float], engagement: float) -> str:
    p = list(prior)
    shift = float(np.clip((0.5 - engagement) * 0.34, -0.14, 0.16))
    if shift > 0:
        moved = min(shift, p[1] * 0.6)
        p[1] -= moved
        p[3] += moved
    else:
        moved = min(-shift, p[3] * 0.6)
        p[3] -= moved
        p[1] += moved
    p = np.array(p) / sum(p)
    return SEGMENTS[int(rng.choice(4, p=p))]


def generate_events(seed: int, count: int = CONFIG["eventCount"]) -> list[Event]:
    rng = np.random.default_rng(seed)
    weights = np.array([r[3] for r in REASONS], dtype=float)
    weights /= weights.sum()
    base = datetime.now(timezone.utc) - timedelta(hours=6)

    events: list[Event] = []
    for i in range(count):
        code, label, side, _w, prior, ambiguous = REASONS[int(rng.choice(len(REASONS), p=weights))]
        plan_name, paise = PLANS[int(rng.integers(0, len(PLANS)))]
        engagement = float(np.clip(rng.uniform(0.05, 0.98), 0, 1))
        segment = _draw_segment(rng, prior, engagement)
        method = METHODS[int(rng.integers(0, len(METHODS)))]
        minutes = int(rng.integers(2, 31)) if rng.random() < 0.42 else int(rng.integers(31, 2881))
        roll = rng.random()
        attempts = 0 if roll < 0.18 else 1 if roll < 0.64 else 2 if roll < 0.9 else 3
        tenure_days = int(rng.integers(21, 1181))
        truth = realize_truth(segment, engagement, paise, tenure_days, side)

        events.append(
            Event(
                event_id=_rid(rng, "evt", 12),
                payment_id=_rid(rng, "pay"),
                subscription_id=_rid(rng, "sub"),
                customer_id=_rid(rng, "cust", 12),
                failed_at=(base + timedelta(seconds=i * 41) - timedelta(minutes=minutes)).isoformat(),
                amount_paise=paise,
                plan_name=plan_name,
                method=method,
                issuer="UPI" if method == "upi_autopay" else ISSUERS[int(rng.integers(0, len(ISSUERS)))],
                reason_code=code,
                reason_label=label,
                failure_side=side,
                minutes_since_failure=minutes,
                local_hour_ist=int(rng.integers(0, 24)),
                attempts_this_cycle=attempts,
                contacts_last_7d=0 if rng.random() < 0.72 else int(rng.integers(1, 4)),
                retries_30d=int(rng.integers(0, 15)),
                consent_on_file=bool(rng.random() < 0.63),
                dnd_registered=bool(rng.random() < 0.31),
                engagement=round(engagement, 3),
                tenure_days=tenure_days,
                segment=segment,
                truth=truth,
                u_recover=float(rng.random()),
                u_churn=float(rng.random()),
                ambiguous=ambiguous,
            )
        )
    return events


def true_uplift(ev: Event) -> float:
    return ev.truth[1] - ev.truth[0]


def featurize(ev: Event) -> list[float]:
    """Feature vector shared by every uplift learner."""
    reason_onehot = [0.0] * len(REASONS)
    reason_onehot[REASON_INDEX[ev.reason_code]] = 1.0
    method_onehot = [1.0 if ev.method == m else 0.0 for m in METHOD_KINDS]
    side_onehot = [1.0 if ev.failure_side == s else 0.0 for s in ("customer", "issuer", "risk")]
    return [
        ev.amount_paise / 300000.0,
        ev.engagement,
        ev.tenure_days / 1200.0,
        min(ev.minutes_since_failure, 2880) / 2880.0,
        ev.attempts_this_cycle / 3.0,
        ev.contacts_last_7d / 3.0,
        ev.retries_30d / 14.0,
        1.0 if ev.consent_on_file else 0.0,
        1.0 if ev.dnd_registered else 0.0,
        *method_onehot,
        *reason_onehot,
        *side_onehot,
    ]
