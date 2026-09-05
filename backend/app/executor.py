"""Layer 05 — Executor.

Real Razorpay test-mode API calls when RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET
are set: payment links for outreach actions, orders for retry scheduling.
Outbound SMS/WhatsApp delivery is always mocked and labelled mocked — a
buildathon demo must never message a real number.

Live calls are capped per batch (EXECUTOR_MAX_LIVE_CALLS, default 8): the
point is to prove the integration is real, not to hammer the sandbox 500
times per run. Every execution record says exactly which of the three modes
it took — real call (with the returned id), capped, or mocked-no-keys.
"""

from __future__ import annotations

import os
import threading

from .sim import Event

try:
    import razorpay
except ImportError:  # pragma: no cover
    razorpay = None


class Executor:
    def __init__(self) -> None:
        key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
        self.max_live_calls = int(os.environ.get("EXECUTOR_MAX_LIVE_CALLS", "8"))
        self._lock = threading.Lock()
        self._live_calls_made = 0
        self.client = None
        if razorpay and key_id and key_secret:
            self.client = razorpay.Client(auth=(key_id, key_secret))

    def start_batch(self) -> None:
        with self._lock:
            self._live_calls_made = 0

    def _take_live_slot(self) -> bool:
        with self._lock:
            if self.client is None or self._live_calls_made >= self.max_live_calls:
                return False
            self._live_calls_made += 1
            return True

    def execute(self, ev: Event, action: str) -> dict:
        """Returns the execution record. `externalKind`/`externalId` name the
        Razorpay object created, when one was, so outcome attribution can poll it."""
        if action == "escalate":
            return {"mode": "none", "detail": "No call made. Case routed to the exception queue.", "mocked": False, "externalKind": None, "externalId": None}

        amount_str = f"₹{ev.amount_paise / 100:.2f}"
        if action == "silent_retry":
            # Razorpay exposes no merchant-initiated retry for a subscription:
            # a failed charge moves it to `pending` and Razorpay itself retries
            # at T+1, T+2, T+3 before `halted`. What we can do in test mode is
            # create the order the next charge will run against, which proves
            # the integration without inventing an endpoint.
            if self._take_live_slot():
                try:
                    order = self.client.order.create(
                        {
                            "amount": ev.amount_paise,
                            "currency": "INR",
                            "receipt": ev.payment_id[:40],
                            "notes": {"recoverops": "silent_retry", "subscription": ev.subscription_id},
                        }
                    )
                    return {
                        "mode": "razorpay_test_mode",
                        "detail": (
                            f"POST /v1/orders → {order['id']} — retry order created, test mode. "
                            f"Subscription {ev.subscription_id} left in `pending` for Razorpay's T+1 auto-retry."
                        ),
                        "mocked": False,
                        "externalKind": "order",
                        "externalId": order["id"],
                    }
                except Exception as exc:  # noqa: BLE001 — an executor failure must not sink the batch
                    return {
                        "mode": "razorpay_test_mode",
                        "detail": f"POST /v1/orders failed ({type(exc).__name__}) — retry recorded locally, test mode.",
                        "mocked": True,
                        "externalKind": None,
                        "externalId": None,
                    }
            note = "call budget reached for this batch" if self.client else "no API keys configured"
            return {
                "mode": "razorpay_test_mode",
                "detail": (
                    f"Subscription {ev.subscription_id} left in `pending` for Razorpay's scheduled T+1 auto-retry "
                    f"(no merchant retry endpoint exists). Retry order not created — {note}."
                ),
                "mocked": True,
                "externalKind": None,
                "externalId": None,
            }

        # Outreach actions: payment link via the real API where allowed.
        channel = "WhatsApp" if action == "payment_link_whatsapp" else "SMS"
        kind = "card update" if action == "card_update_request" else "payment link"
        if self._take_live_slot():
            try:
                payload: dict = {
                    "amount": ev.amount_paise,
                    "currency": "INR",
                    "description": f"RecoverOps {kind} · {ev.plan_name}",
                    "notes": {"recoverops": action, "event": ev.event_id},
                    # Razorpay is the registered sender: with a real contact on the
                    # leak and notify enabled, Razorpay delivers the SMS/email in live
                    # mode. In test mode nothing is sent. Nothing else ever sends.
                    "notify": {"sms": bool(ev.contact), "email": bool(ev.email)},
                    "reminder_enable": True,
                }
                if ev.contact or ev.email:
                    payload["customer"] = {k: v for k, v in (("contact", ev.contact), ("email", ev.email)) if v}
                link = self.client.payment_link.create(payload)
                return {
                    "mode": "razorpay_test_mode",
                    "detail": f"POST /v1/payment_links → {link['id']} — {kind}, {amount_str}, test mode. Razorpay notify {'requested' if (ev.contact or ev.email) else 'not requested (no contact on leak)'}; {channel} delivery mocked.",
                    "mocked": True,  # delivery is mocked even when the link is real
                    "externalKind": "payment_link",
                    "externalId": link["id"],
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "mode": "razorpay_test_mode",
                    "detail": f"POST /v1/payment_links failed ({type(exc).__name__}) — {kind} recorded locally, {amount_str}. {channel} delivery mocked.",
                    "mocked": True,
                    "externalKind": None,
                    "externalId": None,
                }
        note = "call budget reached for this batch" if self.client else "no API keys configured"
        return {
            "mode": "razorpay_test_mode",
            "detail": f"POST /v1/payment_links — {kind}, {amount_str}, test mode ({note}). {channel} delivery mocked.",
            "mocked": True,
            "externalKind": None,
            "externalId": None,
        }
