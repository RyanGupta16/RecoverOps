"""Outcome attribution: closing the loop on real leaks.

A decision is only a claim until the payment clears or the subscription
cancels. This module finds out which, from Razorpay, and writes it down:

- ``sync()`` polls Razorpay for every pending real leak — the payment link the
  executor created, the retry order, the subscription's state, or any captured
  payment from the same counterparty for the same amount — and attributes the
  outcome. Webhooks (phase 9) call ``attribute`` with the same semantics.
- ``mark()`` records an outcome by hand, labelled as such. It exists for
  operators reconciling offline and for demonstrations; it never pretends to
  be a webhook.

Every attribution appends an ``outcome.attributed`` audit row and writes the
resolved case back to case memory, which is where the retrieval layer's
"similar prior cases" numbers on real data come from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .leaks import LeakEvent
from .retrieval import CaseMemory
from .store import Store

TERMINAL_SUBSCRIPTION_RECOVERED = {"active", "authenticated", "completed", "resumed"}
TERMINAL_SUBSCRIPTION_CHURNED = {"cancelled", "expired"}
STALE_AFTER_DAYS = 21


@dataclass
class Attribution:
    batch_id: str
    event_id: str
    recovered: bool | None
    churned: bool | None
    source: str
    note: str
    state: str = "resolved"


class OutcomeTracker:
    def __init__(self, store: Store, memory: CaseMemory, client: Any | None) -> None:
        self.store = store
        self.memory = memory
        self.client = client

    # ---------------------------------------------------------------- public

    def mark(self, event_id: str, recovered: bool, churned: bool = False, note: str = "", actor: str = "operator") -> dict:
        leak = self.store.latest_leak(event_id)
        if leak is None:
            raise LookupError(f"no real leak on record for {event_id}")
        if leak["synthetic"]:
            raise ValueError("synthetic leaks already know their outcome; nothing to mark")
        att = Attribution(leak["batch_id"], event_id, recovered, churned, f"manual:{actor}", note or "Marked by an operator.")
        self._apply(leak, att)
        return {"eventId": event_id, "recovered": recovered, "churned": churned, "source": att.source}

    def attribute(self, event_id: str, *, recovered: bool | None, churned: bool | None, source: str, note: str) -> bool:
        """Attribute from an external signal (webhook or poll) to the latest row for the event."""
        leak = self.store.latest_leak(event_id)
        if leak is None or leak["synthetic"] or leak["outcome_state"] != "pending":
            return False
        self._apply(leak, Attribution(leak["batch_id"], event_id, recovered, churned, source, note))
        return True

    def sync(self, max_age_days: int = 30, limit: int = 500) -> dict:
        """Poll Razorpay for every pending real leak. Safe to call often."""
        pending = self.store.pending_real_leaks(max_age_days=max_age_days, limit=limit)
        report = {"checked": len(pending), "recovered": 0, "churned": 0, "stale": 0, "stillPending": 0, "errors": [], "live": self.client is not None}
        if not pending:
            return report
        if self.client is None:
            # No keys: nothing can be learned from Razorpay. Age out the very old
            # ones so they do not sit as "pending" forever.
            for leak in pending:
                if self._is_stale(leak):
                    self._apply(leak, Attribution(leak["batch_id"], leak["event_id"], None, None, "poll:stale", f"No signal within {STALE_AFTER_DAYS} days and no Razorpay keys to check.", state="unresolved"))
                    report["stale"] += 1
                else:
                    report["stillPending"] += 1
            return report

        for leak in pending:
            try:
                att = self._probe(leak)
            except Exception as exc:  # noqa: BLE001 — one bad probe must not stop the sync
                report["errors"].append(f"{leak['event_id']}: {type(exc).__name__}: {exc}")
                continue
            if att is None:
                if self._is_stale(leak):
                    self._apply(leak, Attribution(leak["batch_id"], leak["event_id"], None, None, "poll:stale", f"No recovery or cancellation signal within {STALE_AFTER_DAYS} days.", state="unresolved"))
                    report["stale"] += 1
                else:
                    report["stillPending"] += 1
                continue
            self._apply(leak, att)
            if att.recovered:
                report["recovered"] += 1
            elif att.churned:
                report["churned"] += 1
        self.store.append_audit("outcomes.synced", {k: v for k, v in report.items() if k != "errors"} | {"errorCount": len(report["errors"])}, actor="scheduler")
        return report

    # --------------------------------------------------------------- probing

    def _probe(self, leak: dict) -> Attribution | None:
        c = self.client
        bid, eid = leak["batch_id"], leak["event_id"]

        if leak.get("external_kind") == "payment_link" and leak.get("external_id"):
            link = c.payment_link.fetch(leak["external_id"])
            status = str(link.get("status", "")).lower()
            if status in ("paid", "partially_paid"):
                return Attribution(bid, eid, True, False, "poll:payment_link", f"Payment link {leak['external_id']} is {status}.")
            if status in ("expired", "cancelled"):
                # The link died, but the subscription may still have recovered on its own retry.
                pass

        if leak.get("external_kind") == "order" and leak.get("external_id"):
            order = c.order.fetch(leak["external_id"])
            if str(order.get("status", "")).lower() == "paid":
                return Attribution(bid, eid, True, False, "poll:order", f"Retry order {leak['external_id']} is paid.")

        if leak.get("subscription_id"):
            sub = c.subscription.fetch(leak["subscription_id"])
            status = str(sub.get("status", "")).lower()
            if status in TERMINAL_SUBSCRIPTION_RECOVERED:
                return Attribution(bid, eid, True, False, "poll:subscription", f"Subscription {leak['subscription_id']} is {status} again.")
            if status in TERMINAL_SUBSCRIPTION_CHURNED:
                return Attribution(bid, eid, False, True, "poll:subscription", f"Subscription {leak['subscription_id']} is {status}.")

        # Last resort: a captured payment from the same counterparty for the
        # same amount after the failure.
        cid = leak.get("counterparty_id")
        if cid and str(cid).startswith("cust_") and not str(cid).startswith("cust_" + "0" * 0):
            since = _epoch(leak.get("failed_at"))
            page = c.payment.all({"count": 50, "from": since} if since else {"count": 50})
            for p in page.get("items", []):
                if str(p.get("customer_id", "")) == str(cid) and str(p.get("status", "")).lower() == "captured" and int(p.get("amount", 0)) == int(leak["amount_paise"]):
                    return Attribution(bid, eid, True, False, "poll:payment", f"Captured payment {p.get('id')} for the same amount from the same customer.")
        return None

    @staticmethod
    def _is_stale(leak: dict) -> bool:
        created = leak.get("failed_at") or leak.get("created_at")
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days >= STALE_AFTER_DAYS

    # --------------------------------------------------------------- writing

    def _apply(self, leak: dict, att: Attribution) -> None:
        self.store.set_outcome(att.batch_id, att.event_id, recovered=att.recovered, churned=att.churned, source=att.source, state=att.state)
        if att.state == "resolved" and att.recovered is not None:
            ev = LeakEvent(
                event_id=att.event_id,
                kind=leak["kind"],
                source=leak["source"],
                amount_paise=leak["amount_paise"],
                method=leak.get("method") or "card",
                reason_code=leak["reason_code"],
            )
            self.memory.record(ev, leak["action"], bool(leak["contacted"]), bool(att.recovered), bool(att.churned), att.batch_id, kind=leak["kind"])
        self.store.append_audit(
            "outcome.attributed",
            {
                "eventId": att.event_id,
                "batchId": att.batch_id,
                "arm": leak["arm"],
                "contacted": bool(leak["contacted"]),
                "action": leak["action"],
                "recovered": att.recovered,
                "churned": att.churned,
                "state": att.state,
                "source": att.source,
                "note": att.note,
                "amountPaise": leak["amount_paise"],
            },
            actor="webhook" if att.source.startswith("webhook") else ("scheduler" if att.source.startswith("poll") else att.source),
            ref=att.event_id,
        )


def _epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())
