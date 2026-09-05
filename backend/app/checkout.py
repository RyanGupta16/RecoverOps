"""Checkout drop-off: the causal frame, with a discount as the treatment.

Abandoned-cart recovery is where uplift ranking earns its keep most obviously,
because the treatment costs real margin rather than ₹0.15. A shopper who was
coming back anyway is a sure thing: send them a 10% code and you have bought
nothing and paid for it. A shopper who left over price is persuadable. The
question is not "will they buy?" but "does the discount change whether they
buy?" — and the answer decides between two arms:

    plain reminder     τ_plain · cart − message cost
    reminder + code    τ_incentive · cart · (1 − d) − margin given up − message cost

Often the free arm wins, which is the whole point: an incentive that converts
someone who would have converted anyway is a pure margin transfer.

Razorpay's Magic Checkout abandoned-cart webhook is the real signal
(``email``, ``phone``, ``line_items``, ``abandoned_checkout_url``,
``cart_token``, ``promotions``). It is India-only and needs Magic Checkout
enabled, so the simulator below produces the same shape for accounts without
it, with segment truth attached so the two-arm choice can be graded.
"""

from __future__ import annotations

import hashlib
import string
from datetime import datetime, timedelta, timezone

import numpy as np

from .leaks import LeakEvent
from .merchant import MerchantConfig
from .sim import _draw_segment, realize_truth
from .taxonomy import FAMILIES

# Where the shopper dropped. Later stages mean higher intent, which is exactly
# the confound a probability model mistakes for persuadability.
STAGES = ("cart", "contact", "address", "payment")
STAGE_WEIGHTS = (0.34, 0.22, 0.24, 0.20)
STAGE_INTENT = {"cart": 0.25, "contact": 0.45, "address": 0.65, "payment": 0.85}

PRODUCTS = [
    ("Everyday tee", 79900),
    ("Everyday tee", 79900),
    ("Linen shirt", 189900),
    ("Chino trousers", 229900),
    ("Weekender bag", 449900),
    ("Leather belt", 129900),
    ("Merino sweater", 349900),
]

# Prior over segments for an abandoned cart, before stage adjusts it.
CART_PRIOR = [0.16, 0.44, 0.30, 0.10]


def _rid(rng: np.random.Generator, prefix: str, n: int = 12) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return prefix + "_" + "".join(alphabet[i] for i in rng.integers(0, len(alphabet), n))


def _stage_shifted_prior(stage: str) -> list[float]:
    """Later stages carry more intent: more sure things, fewer lost causes.
    A probability model reads that as "likely to buy" and discounts them; the
    causal question is whether the discount changed anything."""
    intent = STAGE_INTENT[stage]
    p = list(CART_PRIOR)
    shift = (intent - 0.5) * 0.45
    if shift > 0:
        moved = min(shift, p[2] * 0.7)
        p[2] -= moved
        p[0] += moved
    else:
        moved = min(-shift, p[0] * 0.7)
        p[0] -= moved
        p[2] += moved
    total = sum(p)
    return [x / total for x in p]


def generate_carts(seed: int, count: int, merchant: MerchantConfig) -> list[LeakEvent]:
    """Abandoned carts in the Magic Checkout webhook's shape, with ground truth."""
    rng = np.random.default_rng(seed)
    fam = FAMILIES["CHECKOUT_ABANDONED"]
    now = datetime.now(timezone.utc)
    discount_pct = merchant.discount_cap_pct
    out: list[LeakEvent] = []

    for i in range(count):
        stage = STAGES[int(rng.choice(len(STAGES), p=STAGE_WEIGHTS))]
        n_items = 1 + int(rng.integers(0, 3))
        items = [PRODUCTS[int(rng.integers(0, len(PRODUCTS)))] for _ in range(n_items)]
        cart_paise = sum(p for _, p in items)
        engagement = float(np.clip(rng.beta(2, 2) * 0.5 + STAGE_INTENT[stage] * 0.5, 0.05, 0.98))
        segment = _draw_segment(rng, _stage_shifted_prior(stage), engagement)
        minutes = int(rng.integers(20, 2880))
        tenure = int(rng.integers(0, 900))
        truth = realize_truth(segment, engagement, cart_paise, tenure, "customer")

        token = _rid(rng, "cart")
        # An incentive is only worth *considering* where the cart is big enough
        # for the margin to matter; the estimator decides whether to use it.
        offer_incentive = cart_paise >= 150000
        out.append(
            LeakEvent(
                event_id="evt_" + hashlib.sha256(f"cart|{seed}|{i}|{token}".encode()).hexdigest()[:12],
                kind="checkout_abandonment",
                source="simulator",
                payment_id="",
                subscription_id="",
                order_id=_rid(rng, "order", 14),
                customer_id=_rid(rng, "shopper", 12),
                counterparty_type="consumer",
                failed_at=(now - timedelta(minutes=minutes)).isoformat(),
                amount_paise=cart_paise,
                plan_name=", ".join(name for name, _ in items),
                minutes_since_failure=minutes,
                local_hour_ist=int(rng.integers(0, 24)),
                # The shopper started this checkout themselves — so within
                # thirty minutes a reminder really is transactional (cl. 2(bt)).
                customer_initiated=True,
                has_relationship=bool(rng.random() < 0.42),  # returning shopper?
                method="upi" if rng.random() < 0.55 else "card",
                issuer="",
                reason_code=fam.code,
                reason_label=f"Cart abandoned at {stage}",
                failure_side=fam.side,
                reason_confidence="high",
                ambiguous=False,
                retriable=False,
                hard_decline=False,
                merchant_side=False,
                attempts_this_cycle=1,
                contacts_last_7d=0 if rng.random() < 0.8 else 1,
                retries_30d=0,
                consent_on_file=bool(rng.random() < 0.55),
                consent_granted_days_ago=int(rng.integers(0, 30)) if rng.random() < 0.55 else None,
                dnd_registered=bool(rng.random() < 0.28),
                engagement=round(engagement, 3),
                tenure_days=max(1, tenure),
                segment=segment,
                truth=truth,
                u_recover=float(rng.random()),
                u_churn=float(rng.random()),
                extras={
                    "stage": stage,
                    "cart_token": token,
                    "line_items": [{"name": n, "price": p, "quantity": 1} for n, p in items],
                    "abandoned_checkout_url": f"https://shop.example.in/checkout/{token}",
                    "offer_incentive": offer_incentive,
                    "discount_pct": discount_pct,
                    "intent": STAGE_INTENT[stage],
                },
            )
        )
    return out


def normalize_abandoned_cart(payload: dict, *, now: datetime, merchant: MerchantConfig) -> LeakEvent | None:
    """Razorpay Magic Checkout abandoned-cart webhook payload → LeakEvent."""
    token = str(payload.get("cart_token") or payload.get("token") or "")
    if not token:
        return None
    items = payload.get("line_items") or []
    total = int(payload.get("line_items_total") or sum(int(i.get("price", 0)) * int(i.get("quantity", 1)) for i in items))
    if total <= 0:
        return None
    created = payload.get("created_at") or payload.get("updated_at")
    try:
        started = datetime.fromtimestamp(float(created), tz=timezone.utc) if created else now
    except (TypeError, ValueError):
        started = now
    minutes = max(0, int((now - started).total_seconds() // 60))
    stage = str(payload.get("stage") or ("payment" if payload.get("shipping_address") else "cart"))
    fam = FAMILIES["CHECKOUT_ABANDONED"]
    cust = payload.get("customer") or {}
    return LeakEvent(
        event_id="evt_" + hashlib.sha256(f"cart|{token}".encode()).hexdigest()[:12],
        kind="checkout_abandonment",
        source="razorpay",
        customer_id=str(payload.get("customer_id") or f"shopper_{hashlib.sha256(token.encode()).hexdigest()[:10]}"),
        counterparty_type="consumer",
        contact=payload.get("phone") or cust.get("contact"),
        email=payload.get("email") or cust.get("email"),
        failed_at=started.isoformat(),
        amount_paise=total,
        plan_name=", ".join(str(i.get("name", "item")) for i in items[:3]) or "Abandoned cart",
        minutes_since_failure=minutes,
        local_hour_ist=started.astimezone(timezone(timedelta(hours=5, minutes=30))).hour,
        customer_initiated=True,
        has_relationship=bool(payload.get("customer_id")),
        method="upi",
        issuer="",
        reason_code=fam.code,
        reason_label=f"Cart abandoned at {stage}",
        failure_side=fam.side,
        reason_confidence="high",
        features_are_proxies=True,
        extras={
            "stage": stage,
            "cart_token": token,
            "line_items": items,
            "abandoned_checkout_url": payload.get("abandoned_checkout_url"),
            "promotions": payload.get("promotions"),
            "utm": payload.get("utm_parameters"),
            "offer_incentive": total >= 150000,
            "discount_pct": merchant.discount_cap_pct,
        },
    )


def incentive_arm_value(
    ev: LeakEvent,
    tau_plain: float,
    tau_incentive: float,
    merchant: MerchantConfig,
) -> dict:
    """Choose between the free arm and the discounted one, in rupees.

    The discount is charged on every conversion it appears on — including the
    ones that would have happened anyway — which is what makes "send the code
    to everyone" expensive and invisible in a conversion-rate dashboard.
    """
    cart = ev.amount_paise
    d = merchant.discount_cap_pct / 100.0
    margin = merchant.gross_margin_pct / 100.0
    plain_cost = merchant.cost_for("cart_reminder", "service")
    inc_cost = merchant.cost_for("cart_incentive", "promotional")

    plain_value = tau_plain * cart * margin - plain_cost
    # Baseline conversion still gets the discount, so the margin given up is on
    # the full converting population, not just the incremental one.
    p_convert_with_incentive = max(0.0, min(1.0, ev.extras.get("p_base", 0.25) + tau_incentive))
    incentive_value = tau_incentive * cart * margin - p_convert_with_incentive * cart * d - inc_cost

    best = "cart_incentive" if incentive_value > plain_value and ev.extras.get("offer_incentive") else "cart_reminder"
    return {
        "arm": best,
        "plainValuePaise": int(round(plain_value)),
        "incentiveValuePaise": int(round(incentive_value)),
        "marginGivenUpPaise": int(round(p_convert_with_incentive * cart * d)),
        "discountPct": merchant.discount_cap_pct,
        "note": (
            "The free reminder wins: the discount would mostly be spent on shoppers who were coming back anyway."
            if best == "cart_reminder"
            else "The incentive wins even after charging the margin given up on every conversion it touches."
        ),
    }
