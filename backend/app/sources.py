"""Leak sources: where LeakEvents come from.

Three sources behind one interface, so the engine never knows which fed it:

- SimulatorSource   the seeded generator with both branches known (test fixture)
- RazorpaySource    pulls failed payments and pending/halted subscriptions from
                    the merchant's own Razorpay account on test-mode keys
- FileSource        a Razorpay payments export (API JSON or dashboard CSV) or
                    any CSV with the same columns, uploaded through the console

The Razorpay and file sources share one normaliser, ``normalize_payment``,
which turns a payment entity into a LeakEvent using the taxonomy. History
features that real data does not carry (attempts this cycle, engagement,
tenure) are estimated from the pulled window and flagged as proxies, so the
trace can say which inputs were measured and which were inferred.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .leaks import LeakEvent
from .merchant import MerchantConfig
from .sim import CONFIG, generate_events
from .store import DATA_DIR, canonical, now_iso
from .taxonomy import FAMILIES, classify

IST = ZoneInfo("Asia/Kolkata")
UPLOAD_DIR = DATA_DIR / "uploads"


@dataclass
class PullResult:
    leaks: list[LeakEvent]
    meta: dict = field(default_factory=dict)


class LeakSource(Protocol):
    name: str

    def describe(self) -> dict: ...

    def pull(self, **kwargs: Any) -> PullResult: ...


# ----------------------------------------------------------------- simulator


class SimulatorSource:
    name = "simulator"

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": True,
            "dataMode": "synthetic",
            "note": "Seeded generator with both potential outcomes known. The comparison on these batches is exact.",
        }

    def pull(self, seed: int | None = None, count: int | None = None, **_: Any) -> PullResult:
        import secrets

        seed = seed if seed is not None else secrets.randbelow(2**31)
        leaks = generate_events(seed, count or CONFIG["eventCount"])
        return PullResult(leaks, {"seed": seed, "count": len(leaks), **_summary(leaks)})


class CheckoutSource:
    """Abandoned carts. Synthetic with segment truth, so the two-arm incentive
    decision can be graded; the same shape a Magic Checkout webhook delivers."""

    name = "checkout"

    def __init__(self, merchant: MerchantConfig) -> None:
        self.merchant = merchant

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": True,
            "dataMode": "synthetic",
            "note": "Abandoned checkouts with known ground truth, in the shape of Razorpay's Magic Checkout abandoned-cart webhook. Grades the discount decision, which is the expensive one.",
        }

    def pull(self, seed: int | None = None, count: int | None = None, **_: Any) -> PullResult:
        import secrets

        from .checkout import generate_carts

        seed = seed if seed is not None else secrets.randbelow(2**31)
        leaks = generate_carts(seed, count or 300, self.merchant)
        return PullResult(leaks, {"seed": seed, "count": len(leaks), **_summary(leaks)})


class ReceivablesSource:
    """Overdue invoices from the merchant's own Razorpay account — real
    receivables where the account has any, with a synthetic invoice book as the
    fallback so the ladder can be demonstrated on an empty test account."""

    name = "receivables"

    def __init__(self, client: Any | None, merchant: MerchantConfig) -> None:
        self.client = client
        self.merchant = merchant

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": True,
            "dataMode": "real" if self.client is not None else "synthetic",
            "note": (
                "Issued and partially-paid invoices past their due date, pulled from your Razorpay account. Falls back to a synthetic invoice book when the account has none."
                if self.client
                else "No Razorpay keys — runs on a synthetic invoice book with payer archetypes and disputes."
            ),
        }

    def pull(self, seed: int | None = None, count: int | None = None, limit: int = 500, **_: Any) -> PullResult:
        from .receivables import InvoiceSource

        if self.client is not None:
            leaks, meta = InvoiceSource(client=self.client, merchant=self.merchant).pull(limit=limit)
            if leaks:
                return PullResult(leaks, {**meta, **_summary(leaks), "dataMode": "real"})
            # An account with no overdue invoices is not an error; fall through
            # to the synthetic book so the ladder is still demonstrable, and say so.
            fallback_note = f"Razorpay account has no overdue invoices ({meta.get('invoicesScanned', 0)} scanned) — using the synthetic invoice book."
        else:
            fallback_note = "No Razorpay keys — using the synthetic invoice book."

        import secrets

        from .invoice_sim import generate_invoices

        seed = seed if seed is not None else secrets.randbelow(2**31)
        leaks = generate_invoices(seed, count or 200, self.merchant)
        return PullResult(leaks, {"seed": seed, "count": len(leaks), "note": fallback_note, "dataMode": "synthetic", **_summary(leaks)})


# ---------------------------------------------------------------- normaliser


_VPA_HANDLE_TO_PSP = {
    "okhdfcbank": "google_pay",
    "okicici": "google_pay",
    "oksbi": "google_pay",
    "okaxis": "google_pay",
    "ybl": "phonepe",
    "ibl": "phonepe",
    "axl": "phonepe",
    "paytm": "paytm",
    "ptsbi": "paytm",
    "pthdfc": "paytm",
    "ptaxis": "paytm",
    "upi": "bhim",
    "apl": "amazon_pay",
    "yapl": "amazon_pay",
}


def _to_int(v: Any, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        return int(float(str(v).replace(",", "")))
    except ValueError:
        return default


def _parse_ts(v: Any) -> datetime | None:
    """Razorpay uses Unix seconds; dashboard exports use human strings."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{9,13}", v.strip())):
        n = float(v)
        if n > 1e12:  # milliseconds
            n /= 1000
        return datetime.fromtimestamp(n, tz=timezone.utc)
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y %H:%M:%S", "%d %b %Y, %I:%M %p"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=IST)
    except ValueError:
        return None


def _hash_id(*parts: str) -> str:
    return hashlib.sha256("|".join(p for p in parts if p).encode("utf-8")).hexdigest()[:12]


def assign_holdout(counterparty_key: str, share: float, salt: str = "recoverops-holdout-v1") -> bool:
    """Deterministic per-counterparty arm assignment. Hash-based so a customer
    is in the same arm on every batch — an arm that flips between runs would
    contaminate the comparison — and reproducible without storing anything."""
    if share <= 0:
        return False
    h = hashlib.sha256(f"{salt}|{counterparty_key}".encode("utf-8")).digest()
    u = int.from_bytes(h[:8], "big") / float(1 << 64)
    return u < share


def apply_holdout(leaks: list[LeakEvent], share: float) -> list[LeakEvent]:
    for ev in leaks:
        key = ev.customer_id or ev.contact_hash() or ev.event_id
        ev.holdout = assign_holdout(key, share)
    return leaks


def _get(p: dict, *keys: str, default: Any = None) -> Any:
    """First present, non-empty key — tolerates API and export column names."""
    for k in keys:
        if k in p and p[k] not in (None, ""):
            return p[k]
    return default


def normalize_payment(
    p: dict,
    *,
    now: datetime,
    source: str,
    history: "_History | None" = None,
    invoice_to_subscription: dict[str, dict] | None = None,
) -> LeakEvent | None:
    """A Razorpay payment entity (or export row) with status=failed → LeakEvent.
    Returns None for rows that are not failed payments."""
    status = str(_get(p, "status", default="")).lower()
    if status != "failed":
        return None

    payment_id = str(_get(p, "id", "payment_id", default=""))
    created = _parse_ts(_get(p, "created_at", "created at", "date", "Created At"))
    if created is None:
        created = now
    amount = _to_int(_get(p, "amount", "Amount", default=0))
    if isinstance(_get(p, "amount", "Amount"), str) and "." in str(_get(p, "amount", "Amount")):
        # Dashboard exports list rupees with decimals; the API lists paise.
        amount = int(round(float(str(_get(p, "amount", "Amount")).replace(",", "")) * 100))

    method = str(_get(p, "method", "Method", default="card")).lower()
    card = p.get("card") if isinstance(p.get("card"), dict) else {}
    token_id = _get(p, "token_id")
    notes = p.get("notes") if isinstance(p.get("notes"), dict) else {}
    invoice_id = _get(p, "invoice_id")
    sub_ctx = (invoice_to_subscription or {}).get(str(invoice_id), {}) if invoice_id else {}
    subscription_id = str(_get(notes, "subscription_id", default=sub_ctx.get("subscription_id", "")) or "")
    recurring = bool(token_id) or bool(subscription_id) or method in ("emandate", "nach") or str(_get(p, "recurring", default="")).lower() in ("1", "true", "yes")

    if method in ("nach",):
        method = "emandate"
    elif method == "upi" and recurring:
        method = "upi_autopay"
    elif method not in ("card", "upi", "upi_autopay", "emandate", "netbanking", "wallet"):
        method = "card" if method in ("emi",) else method

    vpa = str(_get(p, "vpa", default="") or "")
    handle = vpa.split("@", 1)[1].lower() if "@" in vpa else ""
    issuer = str(
        _get(card, "issuer", default=None)
        or _get(p, "bank", "Bank", default=None)
        or _get(p, "wallet", "Wallet", default=None)
        or (handle.upper() if handle else "")
        or ""
    )
    network = _get(card, "network", default=None) or _get(p, "card_network", "Card Network", default=None)
    psp = _VPA_HANDLE_TO_PSP.get(handle) if handle else None

    cls = classify(
        _get(p, "error_reason", "Error Reason", default=None),
        _get(p, "error_source", "Error Source", default=None),
        _get(p, "error_description", "Error Description", default=None),
    )
    fam = cls.family

    contact = _get(p, "contact", "Contact", default=None)
    email = _get(p, "email", "Email", default=None)
    customer_id = str(_get(p, "customer_id", default="") or sub_ctx.get("customer_id", "") or "")
    counterparty_key = customer_id or (str(contact) if contact else "") or (str(email) if email else "") or payment_id

    minutes = max(0, int((now - created).total_seconds() // 60))
    hist = history.for_(counterparty_key, created) if history else None

    ev = LeakEvent(
        event_id=f"evt_{_hash_id(source, payment_id or counterparty_key, created.isoformat())}",
        kind="subscription_failure" if recurring else "checkout_abandonment",
        source=source,
        payment_id=payment_id,
        subscription_id=subscription_id,
        invoice_id=str(invoice_id) if invoice_id else None,
        order_id=_get(p, "order_id"),
        # A real Razorpay customer id keeps its cust_ prefix; one we fabricate
        # from a contact or an email is prefixed anon_ so downstream code can
        # tell the difference. Outcome attribution searches Razorpay by
        # customer, which is only valid for an id Razorpay actually issued.
        customer_id=customer_id or f"anon_{_hash_id(counterparty_key)}",
        counterparty_type="consumer",
        contact=str(contact) if contact else None,
        email=str(email) if email else None,
        failed_at=created.isoformat(),
        amount_paise=amount,
        plan_name=str(sub_ctx.get("plan_name") or _get(p, "description", "Description", default="") or ("Recurring charge" if recurring else "One-time payment")),
        minutes_since_failure=minutes,
        local_hour_ist=created.astimezone(IST).hour,
        customer_initiated=not recurring,
        has_relationship=bool(recurring or customer_id or subscription_id),
        method=method,
        issuer=issuer,
        network=str(network) if network else None,
        psp=psp,
        reason_code=fam.code,
        reason_label=fam.label,
        failure_side=fam.side,
        raw_reason=str(_get(p, "error_reason", "Error Reason", default="") or "") or None,
        raw_source=str(_get(p, "error_source", "Error Source", default="") or "") or None,
        raw_description=str(_get(p, "error_description", "Error Description", default="") or "") or None,
        reason_confidence=cls.confidence,
        ambiguous=fam.ambiguous or cls.confidence == "low",
        retriable=fam.retriable,
        hard_decline=fam.hard_decline,
        merchant_side=fam.merchant_side,
        attempts_this_cycle=hist.attempts if hist else 1,
        contacts_last_7d=0,
        retries_30d=hist.retries_30d if hist else 1,
        consent_on_file=False,
        consent_granted_days_ago=None,
        dnd_registered=False,
        engagement=hist.engagement if hist else 0.5,
        tenure_days=hist.tenure_days if hist else 90,
        features_are_proxies=True,
        extras={
            "classification": cls.note,
            "matchedBy": cls.matched_by,
            "errorCode": _get(p, "error_code", "Error Code"),
            "errorStep": _get(p, "error_step", "Error Step"),
            "cardLast4": _get(card, "last4"),
            "cardType": _get(card, "type"),
            "vpaHandle": handle or None,
            "description": _get(p, "description", "Description"),
            "customer_name": _get(p, "customer_name", "Customer Name", default=None),
        },
    )
    return ev


class _History:
    """Per-counterparty history inside the pulled window: attempts, successes,
    first-seen. Everything the real data can support without a customer graph."""

    def __init__(self, payments: list[dict]) -> None:
        self._by_key: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        for p in payments:
            key = str(_get(p, "customer_id", default="") or _get(p, "contact", "Contact", default="") or _get(p, "email", "Email", default="") or _get(p, "id", default=""))
            ts = _parse_ts(_get(p, "created_at", "created at", "date", "Created At"))
            if ts is None:
                continue
            self._by_key[key].append((ts, str(_get(p, "status", default="")).lower()))
        for rows in self._by_key.values():
            rows.sort()

    @dataclass
    class Row:
        attempts: int
        retries_30d: int
        engagement: float
        tenure_days: int

    def for_(self, key: str, at: datetime) -> "_History.Row":
        rows = self._by_key.get(key, [])
        before = [(ts, st) for ts, st in rows if ts <= at]
        fails_before = [ts for ts, st in before if st == "failed"]
        attempts = max(1, len([ts for ts in fails_before if ts >= at - timedelta(days=7)]))
        retries_30d = max(1, len([ts for ts in fails_before if ts >= at - timedelta(days=30)]))
        succ = len([1 for _, st in before if st in ("captured", "authorized")])
        total = len(before)
        engagement = round(min(0.98, max(0.05, (succ + 1) / (total + 2))), 3)  # Laplace-smoothed success share
        first_seen = rows[0][0] if rows else at
        tenure = max(30, int((at - first_seen).days) or 30)
        return _History.Row(attempts=min(attempts, 4), retries_30d=min(retries_30d, 15), engagement=engagement, tenure_days=tenure)


def normalize_subscription(s: dict, *, now: datetime, source: str, plan_lookup: dict[str, dict]) -> LeakEvent | None:
    """A subscription entity in `pending` or `halted` → LeakEvent, for the
    cases where the failed payment itself is outside the pulled window."""
    status = str(s.get("status", "")).lower()
    if status not in ("pending", "halted"):
        return None
    plan = plan_lookup.get(str(s.get("plan_id", "")), {})
    item = plan.get("item", {}) if isinstance(plan, dict) else {}
    amount = _to_int(item.get("amount", 0)) or _to_int(s.get("amount", 0))
    charge_at = _parse_ts(s.get("charge_at")) or _parse_ts(s.get("current_start")) or now
    attempts = 4 if status == "halted" else 1
    fam = FAMILIES["GATEWAY_ERROR"]
    method = "upi_autopay" if str(s.get("payment_method", "")).lower() == "upi" else ("emandate" if str(s.get("payment_method", "")).lower() in ("emandate", "nach") else "card")
    return LeakEvent(
        event_id=f"evt_{_hash_id(source, str(s.get('id', '')), status, charge_at.isoformat())}",
        kind="subscription_failure",
        source=source,
        payment_id="",
        subscription_id=str(s.get("id", "")),
        customer_id=str(s.get("customer_id", "") or ""),
        failed_at=charge_at.isoformat(),
        amount_paise=amount,
        plan_name=str(item.get("name") or plan.get("id") or "Subscription"),
        minutes_since_failure=max(0, int((now - charge_at).total_seconds() // 60)),
        local_hour_ist=charge_at.astimezone(IST).hour,
        customer_initiated=False,
        has_relationship=True,
        method=method,
        issuer="",
        reason_code=fam.code,
        reason_label=f"Subscription {status} — last charge failed (reason not in pulled window)",
        failure_side=fam.side,
        reason_confidence="low",
        ambiguous=True,
        retriable=status != "halted",
        attempts_this_cycle=attempts,
        retries_30d=attempts,
        engagement=min(0.98, 0.3 + 0.1 * _to_int(s.get("paid_count", 0))),
        tenure_days=max(30, _to_int(s.get("paid_count", 0)) * 30),
        features_are_proxies=True,
        extras={"subscriptionStatus": status, "paidCount": s.get("paid_count"), "remainingCount": s.get("remaining_count")},
    )


# ------------------------------------------------------------------ razorpay


class RazorpaySource:
    name = "razorpay"

    def __init__(self, client: Any | None, merchant: MerchantConfig) -> None:
        self.client = client
        self.merchant = merchant

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": self.client is not None,
            "dataMode": "real",
            "note": (
                "Pulls failed payments and pending/halted subscriptions from your Razorpay account on the configured test-mode keys."
                if self.client
                else "Set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in backend/.env to pull from your account."
            ),
        }

    def _paginate(self, fn, params: dict, limit: int, errors: list[str], label: str) -> list[dict]:
        out: list[dict] = []
        skip = 0
        while len(out) < limit:
            try:
                page = fn({**params, "count": 100, "skip": skip})
            except Exception as exc:  # noqa: BLE001 — a single failed page must not sink the pull
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
                break
            items = page.get("items", []) if isinstance(page, dict) else []
            out.extend(items)
            if len(items) < 100:
                break
            skip += 100
        return out[:limit]

    def pull(self, days: int = 30, limit: int = 500, **_: Any) -> PullResult:
        if self.client is None:
            return PullResult([], {"error": "no Razorpay keys configured"})
        now = datetime.now(timezone.utc)
        since = int((now - timedelta(days=days)).timestamp())
        errors: list[str] = []

        payments = self._paginate(self.client.payment.all, {"from": since, "to": int(now.timestamp())}, limit * 4, errors, "payments")
        invoices = self._paginate(self.client.invoice.all, {}, 500, errors, "invoices")
        subscriptions = self._paginate(self.client.subscription.all, {}, 500, errors, "subscriptions")

        plan_lookup: dict[str, dict] = {}
        for s in subscriptions:
            pid = str(s.get("plan_id", ""))
            if pid and pid not in plan_lookup:
                try:
                    plan_lookup[pid] = self.client.plan.fetch(pid)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"plan {pid}: {type(exc).__name__}")
                    plan_lookup[pid] = {}

        inv_to_sub = {
            str(inv.get("id")): {
                "subscription_id": inv.get("subscription_id"),
                "customer_id": inv.get("customer_id"),
                "plan_name": (inv.get("line_items") or [{}])[0].get("name") if inv.get("line_items") else None,
            }
            for inv in invoices
            if inv.get("subscription_id")
        }

        history = _History(payments)
        leaks: list[LeakEvent] = []
        seen_subs: set[str] = set()
        for p in payments:
            ev = normalize_payment(p, now=now, source=self.name, history=history, invoice_to_subscription=inv_to_sub)
            if ev:
                leaks.append(ev)
                if ev.subscription_id:
                    seen_subs.add(ev.subscription_id)
        for s in subscriptions:
            if str(s.get("id")) in seen_subs:
                continue
            ev = normalize_subscription(s, now=now, source=self.name, plan_lookup=plan_lookup)
            if ev:
                leaks.append(ev)

        leaks.sort(key=lambda e: e.failed_at)
        leaks = leaks[-limit:]
        if not leaks:
            # An account with no failed payments is the normal state of a fresh
            # test account, not a fault. Say which of the two it is.
            scanned = len(payments)
            error = (
                f"Your Razorpay account has no failed payments in the last {days} days "
                f"({scanned} payment(s) scanned). Nothing to recover — which is the right answer, not an error. "
                "Upload a payments export, or run the simulator, receivables or checkout source."
                if scanned or not errors
                else f"Could not read payments from Razorpay: {errors[0]}"
            )
            return PullResult([], {"error": error, "paymentsScanned": scanned, "errors": errors, "days": days})
        return PullResult(
            leaks,
            {
                "paymentsScanned": len(payments),
                "failedPayments": sum(1 for p in payments if str(p.get("status", "")).lower() == "failed"),
                "subscriptionsScanned": len(subscriptions),
                "pendingOrHalted": sum(1 for s in subscriptions if str(s.get("status", "")).lower() in ("pending", "halted")),
                "invoicesScanned": len(invoices),
                "days": days,
                "errors": errors,
                # The detector runs on this stream; the runtime strips it before
                # anything is written to the audit log.
                "raw_payments": payments,
                **_summary(leaks),
            },
        )


# ---------------------------------------------------------------------- file


class FileSource:
    """Uploaded Razorpay exports. Raw rows are kept on disk under an id so a
    batch can be re-run against the same file later; normalisation happens at
    run time so `minutes since failure` is right for that run."""

    name = "file"

    def __init__(self, upload_dir: Path | None = None) -> None:
        # Resolved through the module global rather than captured at import, so
        # a test session can redirect the default away from the app's real data
        # directory without every construction site having to pass a path.
        self.dir = Path(upload_dir) if upload_dir else UPLOAD_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": True,
            "dataMode": "real",
            "note": "Upload a Razorpay payments export — API JSON or dashboard CSV — and run the batch on it.",
            "files": self.list_files(),
        }

    # -- registry

    def list_files(self) -> list[dict]:
        out = []
        for meta_path in sorted(self.dir.glob("*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                out.append(json.loads(meta_path.read_text()))
            except json.JSONDecodeError:
                continue
        return out

    def save(self, content: bytes, filename: str) -> dict:
        rows, warnings = parse_export(content, filename)
        file_id = f"file_{_hash_id(filename, hashlib.sha256(content).hexdigest())}"
        (self.dir / f"{file_id}.rows.json").write_text(json.dumps(rows, ensure_ascii=False))
        now = datetime.now(timezone.utc)
        preview_leaks = [ev for ev in (normalize_payment(r, now=now, source=self.name) for r in rows) if ev]
        meta = {
            "fileId": file_id,
            "filename": filename,
            "uploadedAt": now_iso(),
            "rows": len(rows),
            "failedRows": len(preview_leaks),
            "warnings": warnings,
            **_summary(preview_leaks),
        }
        (self.dir / f"{file_id}.meta.json").write_text(canonical(meta))
        return meta

    def get_meta(self, file_id: str) -> dict | None:
        p = self.dir / f"{file_id}.meta.json"
        return json.loads(p.read_text()) if p.exists() else None

    def pull(self, file_id: str | None = None, limit: int = 2000, **_: Any) -> PullResult:
        if not file_id:
            return PullResult([], {"error": "fileId is required for the file source"})
        rows_path = self.dir / f"{file_id}.rows.json"
        if not rows_path.exists():
            return PullResult([], {"error": f"unknown file {file_id}"})
        rows = json.loads(rows_path.read_text())
        now = datetime.now(timezone.utc)
        history = _History(rows)
        leaks = [ev for ev in (normalize_payment(r, now=now, source=self.name, history=history) for r in rows) if ev]
        leaks.sort(key=lambda e: e.failed_at)
        meta = self.get_meta(file_id) or {}
        return PullResult(leaks[-limit:], {**meta, "rowsScanned": len(rows), **_summary(leaks)})


def parse_export(content: bytes, filename: str) -> tuple[list[dict], list[str]]:
    """API JSON (`{"items": [...]}` or a list) or a CSV export → list of row dicts."""
    warnings: list[str] = []
    text = content.decode("utf-8-sig", errors="replace")
    stripped = text.lstrip()
    if filename.lower().endswith(".json") or stripped.startswith(("{", "[")):
        data = json.loads(stripped)
        if isinstance(data, dict):
            items = data.get("items") or data.get("payments") or data.get("data") or []
        else:
            items = data
        rows = [r for r in items if isinstance(r, dict)]
        if not rows:
            warnings.append("JSON contained no payment objects (expected a list or {items: [...]}).")
        return rows, warnings

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw in reader:
        row = {(k or "").strip().lower().replace(" ", "_"): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        # Dashboard exports label the id column differently across versions.
        if "payment_id" in row and "id" not in row:
            row["id"] = row["payment_id"]
        rows.append(row)
    if rows and "status" not in rows[0]:
        warnings.append("No `status` column found — every row will be ignored unless it says failed.")
    if rows and not any(k in rows[0] for k in ("error_reason", "error_description", "error_code")):
        warnings.append("No error columns found — reasons will be classified as ambiguous gateway errors.")
    return rows, warnings


def _summary(leaks: list[LeakEvent]) -> dict:
    by_family: dict[str, int] = defaultdict(int)
    by_method: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    for ev in leaks:
        by_family[ev.reason_code] += 1
        by_method[ev.method] += 1
        by_kind[ev.kind] += 1
    return {
        "leaks": len(leaks),
        "amountPaise": sum(ev.amount_paise for ev in leaks),
        "byFamily": dict(sorted(by_family.items(), key=lambda kv: -kv[1])),
        "byMethod": dict(sorted(by_method.items(), key=lambda kv: -kv[1])),
        "byKind": dict(by_kind),
        "lowConfidence": sum(1 for ev in leaks if ev.reason_confidence == "low"),
    }
