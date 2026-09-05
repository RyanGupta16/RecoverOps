"""Razorpay webhook receiver: outcomes in real time rather than on a poll.

Polling closes the loop, but it closes it minutes late and it costs an API call
per pending leak. A webhook is the same attribution with none of that — so this
takes the identical path through ``OutcomeTracker.attribute`` and writes the
identical audit row, with ``source: webhook:<event>``.

Two things are non-negotiable:

- **Signature first.** Every payload is verified against
  ``RAZORPAY_WEBHOOK_SECRET`` with ``hmac.compare_digest`` before it is parsed
  for meaning. An unverified webhook is an anonymous stranger asserting that a
  customer paid, and acting on it would let anyone mark any leak recovered.
- **Idempotence.** Razorpay retries on non-2xx, and a retried delivery must not
  produce a second attribution or a second audit row. Deliveries are keyed on
  ``x-razorpay-event-id`` and replayed ones are acknowledged, not reprocessed.

Without a secret configured the endpoint refuses everything and says so. That
is deliberate: a receiver that accepts unsigned payloads "for the demo" is a
receiver that accepts them in production.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

from .outcomes import OutcomeTracker
from .promises import PromiseBook
from .store import Store

# Which events say something about a leak's outcome, and what they say.
RECOVERY_EVENTS = {
    "subscription.charged": "the subscription charged successfully",
    "subscription.activated": "the subscription is active again",
    "payment_link.paid": "the payment link was paid",
    "invoice.paid": "the invoice was paid",
    "order.paid": "the order was paid",
    "payment.captured": "the payment was captured",
    "virtual_account.credited": "the virtual account was credited",
}
CHURN_EVENTS = {
    "subscription.cancelled": "the subscription was cancelled",
    "subscription.halted": "the subscription was halted after its retries",
}
PARTIAL_EVENTS = {"invoice.partially_paid", "payment_link.partially_paid"}
DEGRADATION_EVENTS = {"payment.downtime.started", "payment.downtime.updated", "payment.downtime.resolved"}


@dataclass
class WebhookResult:
    status: str  # applied | replayed | ignored | unmatched
    event: str
    detail: str
    event_id: str | None = None
    leak: str | None = None

    def public(self) -> dict:
        return {"status": self.status, "event": self.event, "detail": self.detail, "eventId": self.event_id, "leak": self.leak}


class WebhookReceiver:
    def __init__(self, store: Store, outcomes: OutcomeTracker, promises: PromiseBook, secret: str | None = None) -> None:
        self.store = store
        self.outcomes = outcomes
        self.promises = promises
        self.secret = (secret if secret is not None else os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")).strip()

    @property
    def configured(self) -> bool:
        return bool(self.secret)

    def describe(self) -> dict:
        return {
            "configured": self.configured,
            "path": "/webhooks/razorpay",
            "events": sorted(RECOVERY_EVENTS | CHURN_EVENTS) + sorted(PARTIAL_EVENTS | DEGRADATION_EVENTS),
            "note": (
                "Signature-verified. Deliveries are deduplicated on x-razorpay-event-id, so a Razorpay retry "
                "acknowledges without attributing twice."
                if self.configured
                else "Set RAZORPAY_WEBHOOK_SECRET in backend/.env and point a tunnel at /webhooks/razorpay. "
                "Until then the endpoint refuses every delivery — it will not accept unsigned payloads."
            ),
        }

    # ------------------------------------------------------------- security

    def verify(self, body: bytes, signature: str | None) -> bool:
        if not self.configured or not signature:
            return False
        expected = hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def already_seen(self, delivery_id: str | None) -> bool:
        if not delivery_id:
            return False
        return bool(self.store.audit_tail(kind="webhook.received", ref=delivery_id, limit=1))

    # -------------------------------------------------------------- handling

    def handle(self, payload: dict, delivery_id: str | None) -> WebhookResult:
        event = str(payload.get("event") or "")
        entities = _entities(payload)

        if delivery_id and self.already_seen(delivery_id):
            return WebhookResult("replayed", event, "This delivery was already processed; acknowledged without reprocessing.", delivery_id)

        self.store.append_audit(
            "webhook.received",
            {"event": event, "deliveryId": delivery_id, "entities": sorted(entities)},
            actor="webhook",
            ref=delivery_id,
        )

        if event in DEGRADATION_EVENTS:
            # The monitor re-reads the feed on the next batch; recording the
            # notification is what matters for the trail.
            return WebhookResult("applied", event, "Downtime notification recorded; the cohort view refreshes on the next batch.", delivery_id)

        recovered: bool | None
        if event in RECOVERY_EVENTS or event in PARTIAL_EVENTS:
            recovered, churned = True, False
        elif event in CHURN_EVENTS:
            recovered, churned = False, True
        else:
            return WebhookResult("ignored", event, "Not an event that changes a leak's outcome.", delivery_id)

        leak = self._match(entities)
        if leak is None:
            return WebhookResult("unmatched", event, "No leak on record matches this entity; nothing was attributed.", delivery_id)

        note = RECOVERY_EVENTS.get(event) or CHURN_EVENTS.get(event) or event
        applied = self.outcomes.attribute(
            leak["event_id"],
            recovered=recovered,
            churned=churned,
            source=f"webhook:{event}",
            note=f"Razorpay reported that {note}.",
        )
        if recovered and leak.get("counterparty_id"):
            self.promises.settle_from_outcome(leak["counterparty_id"], int(leak["amount_paise"]), f"webhook:{event}")
        return WebhookResult(
            "applied" if applied else "ignored",
            event,
            f"Attributed to {leak['event_id']}." if applied else "The leak already had an outcome; left unchanged.",
            delivery_id,
            leak["event_id"],
        )

    def _match(self, entities: dict[str, str]) -> dict | None:
        """Find the leak this webhook is about, by the Razorpay object it names."""
        for column, key in (
            ("external_id", "payment_link_id"),
            ("external_id", "order_id"),
            ("invoice_id", "invoice_id"),
            ("subscription_id", "subscription_id"),
            ("payment_id", "payment_id"),
            ("order_id", "order_id"),
        ):
            value = entities.get(key)
            if not value:
                continue
            with self.store.lock:
                row = self.store.conn.execute(
                    f"""SELECT l.* FROM leaks l JOIN batches b ON b.batch_id = l.batch_id
                        WHERE l.{column} = ? AND l.synthetic = 0
                        ORDER BY b.created_at DESC LIMIT 1""",
                    (value,),
                ).fetchone()
            if row:
                return self.store._leak_row(row)  # noqa: SLF001 — same module family
        return None


def _entities(payload: dict) -> dict[str, str]:
    """Pull the ids Razorpay nests under payload.<entity>.entity.id."""
    out: dict[str, str] = {}
    container = payload.get("payload")
    if not isinstance(container, dict):
        return out
    for name, wrapper in container.items():
        if not isinstance(wrapper, dict):
            continue
        entity: Any = wrapper.get("entity")
        if not isinstance(entity, dict):
            continue
        if entity.get("id"):
            out[f"{name}_id"] = str(entity["id"])
        for ref in ("subscription_id", "invoice_id", "order_id", "payment_id", "customer_id"):
            if entity.get(ref):
                out.setdefault(ref, str(entity[ref]))
    return out
