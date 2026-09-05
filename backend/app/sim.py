"""Synthetic world: the generator that knows both branches.

To grade a decision against the four causal segments you need the outcome of
the branch not taken, which no live system observes. The simulator produces
LeakEvents with both potential outcomes attached, so the comparison in the
console is exact rather than estimated. It is a test fixture for the pipeline
that real data flows through — the same LeakEvent, minus the truth.

The reason taxonomy comes from taxonomy.py so the generator and the real-data
normaliser describe the same world, including the three families real data
has and a toy simulator would never have thought of.
"""

from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone

import numpy as np

from .leaks import LeakEvent
from .taxonomy import FAMILIES, SIDES, sim_reasons

# Backwards-compatible alias: the layers were written against `Event`.
Event = LeakEvent

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
    - Issuer-side failures recover quietly more often regardless of segment;
      merchant-side ones even more so — once the merchant fixes its config the
      next scheduled charge simply goes through.
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
    elif failure_side == "merchant":
        p0 += 0.08

    clip = lambda v: float(min(0.97, max(0.01, v)))  # noqa: E731
    return clip(p0), clip(p1), clip(c0), clip(c1)


# (code, label, side, weight, prior over segments, ambiguous) — from the taxonomy.
REASONS = sim_reasons()
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
METHOD_KINDS = ["card", "upi_autopay", "emandate", "netbanking", "upi", "wallet"]
ISSUERS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "IndusInd", "Yes Bank", "IDFC First"]
NETWORKS = ["Visa", "Visa", "MasterCard", "RuPay"]
PSPS = ["google_pay", "phonepe", "paytm", "bhim"]

# Simulator defaults. The engine reads the merchant config; these are the
# numbers merchant.toml falls back to, kept here so a bare generator still runs.
CONFIG = {
    "eventCount": 500,
    "contactBudget": 200,
    "contactCostPaise": 120,
    "churnResidualCycles": 3,
    "baselineProbabilityThreshold": 0.35,
    "upliftThreshold": 0.05,
}


def _rid(rng: np.random.Generator, prefix: str, n: int = 14) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return prefix + "_" + "".join(alphabet[i] for i in rng.integers(0, len(alphabet), n))


NUDGE_RESPONSES = ["none", "paid_after_nudge", "paid_without_nudge", "ignored", "complained"]

# P(prior_nudge_response | segment). Informative but noisy: a third of every
# segment has never been chased, and each history reads the "wrong" way some of
# the time. This is the one place the simulated world is kinder than the bare
# reason code — and it is the signal a merchant's own history provides, which
# is why the learning loop exists. Disclosed in the batch's honesty block.
NUDGE_RESPONSE_GIVEN_SEGMENT = {
    #                 none   paid_after  paid_without  ignored  complained
    "sure_thing":   (0.34,   0.06,       0.44,         0.12,    0.04),
    "persuadable":  (0.34,   0.42,       0.08,         0.13,    0.03),
    "lost_cause":   (0.34,   0.06,       0.05,         0.47,    0.08),
    "sleeping_dog": (0.34,   0.05,       0.22,         0.13,    0.26),
}


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


def generate_events(seed: int, count: int = CONFIG["eventCount"]) -> list[LeakEvent]:
    rng = np.random.default_rng(seed)
    weights = np.array([r[3] for r in REASONS], dtype=float)
    weights /= weights.sum()
    base = datetime.now(timezone.utc) - timedelta(hours=6)

    events: list[LeakEvent] = []
    for i in range(count):
        code, label, side, _w, prior, ambiguous = REASONS[int(rng.choice(len(REASONS), p=weights))]
        fam = FAMILIES[code]
        plan_name, paise = PLANS[int(rng.integers(0, len(PLANS)))]
        engagement = float(np.clip(rng.uniform(0.05, 0.98), 0, 1))
        segment = _draw_segment(rng, prior, engagement)
        method = METHODS[int(rng.integers(0, len(METHODS)))]
        minutes = int(rng.integers(2, 31)) if rng.random() < 0.42 else int(rng.integers(31, 2881))
        roll = rng.random()
        attempts = 0 if roll < 0.18 else 1 if roll < 0.64 else 2 if roll < 0.9 else 3
        tenure_days = int(rng.integers(21, 1181))
        truth = realize_truth(segment, engagement, paise, tenure_days, side)
        consent = bool(rng.random() < 0.63)
        nudge_response = NUDGE_RESPONSES[int(rng.choice(5, p=NUDGE_RESPONSE_GIVEN_SEGMENT[segment]))]

        events.append(
            LeakEvent(
                event_id=_rid(rng, "evt", 12),
                kind="subscription_failure",
                source="simulator",
                payment_id=_rid(rng, "pay"),
                subscription_id=_rid(rng, "sub"),
                customer_id=_rid(rng, "cust", 12),
                counterparty_type="consumer",
                failed_at=(base + timedelta(seconds=i * 41) - timedelta(minutes=minutes)).isoformat(),
                amount_paise=paise,
                plan_name=plan_name,
                minutes_since_failure=minutes,
                local_hour_ist=int(rng.integers(0, 24)),
                customer_initiated=False,
                has_relationship=True,
                method=method,
                issuer="UPI" if method == "upi_autopay" else ISSUERS[int(rng.integers(0, len(ISSUERS)))],
                network=NETWORKS[int(rng.integers(0, len(NETWORKS)))] if method == "card" else None,
                psp=PSPS[int(rng.integers(0, len(PSPS)))] if method == "upi_autopay" else None,
                reason_code=code,
                reason_label=label,
                failure_side=side,
                raw_reason=fam.razorpay_reasons[int(rng.integers(0, len(fam.razorpay_reasons)))] if fam.razorpay_reasons else None,
                reason_confidence="high",
                ambiguous=ambiguous,
                retriable=fam.retriable,
                hard_decline=fam.hard_decline,
                merchant_side=fam.merchant_side,
                attempts_this_cycle=attempts,
                contacts_last_7d=0 if rng.random() < 0.72 else int(rng.integers(1, 4)),
                retries_30d=int(rng.integers(0, 15)),
                consent_on_file=consent,
                consent_granted_days_ago=int(rng.integers(0, 61)) if consent else None,
                dnd_registered=bool(rng.random() < 0.31),
                engagement=round(engagement, 3),
                tenure_days=tenure_days,
                prior_nudge_response=nudge_response,
                segment=segment,
                truth=truth,
                u_recover=float(rng.random()),
                u_churn=float(rng.random()),
            )
        )
    return events


def true_uplift(ev: LeakEvent) -> float:
    assert ev.truth is not None, "true_uplift needs synthetic ground truth"
    return ev.truth[1] - ev.truth[0]


# Bump when the feature vector changes shape; the model cache is keyed on it.
FEATURE_VERSION = 3


def featurize(ev: LeakEvent) -> list[float]:
    """Feature vector shared by every uplift learner.

    On real data engagement and tenure are proxies (see sources.py) and the
    nudge history is "none" until this ledger has seen outcomes; the vector is
    the same so the same models can score both, and the trace says when the
    inputs were estimated.
    """
    reason_onehot = [0.0] * len(REASONS)
    reason_onehot[REASON_INDEX[ev.reason_code]] = 1.0
    method_onehot = [1.0 if ev.method == m else 0.0 for m in METHOD_KINDS]
    side_onehot = [1.0 if ev.failure_side == s else 0.0 for s in SIDES]
    nudge_onehot = [1.0 if ev.prior_nudge_response == r else 0.0 for r in NUDGE_RESPONSES]
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
        1.0 if ev.hard_decline else 0.0,
        *method_onehot,
        *reason_onehot,
        *side_onehot,
        *nudge_onehot,
    ]
