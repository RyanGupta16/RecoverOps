"""B2B receivables: invoices that are past due, and the ladder that chases them.

The counterparty is a business with an accounts-payable process, not a person
with a phone. Three things follow, and each one is a rule rather than a tone:

- **The ladder is longer and slower.** Reminder → statement of account →
  payment link → a Smart Collect virtual account so their AP team can pay by
  NEFT/RTGS/UPI and it reconciles itself → the statutory notice → a human.
- **A dispute stops it cold.** Chasing a disputed invoice is how a supplier
  relationship ends; the gate blocks every action while one is open.
- **The strongest lever is statutory, and it is late.** Under MSMED s.15–16 a
  buyer must pay a registered micro or small supplier within 15 days (no written
  agreement) or a maximum of 45 (with one); past that, interest runs at three
  times the RBI bank rate, compounded monthly, and under s.43B(h) the buyer
  cannot deduct the expense until it actually pays. That is real leverage — so
  it is used lawfully and late, never as a first message, and only when the
  supplier really is a registered MSE.

Invoices come from the merchant's own Razorpay account (``GET /v1/invoices``),
so on a live account these are real receivables, not a simulation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .leaks import LeakEvent
from .merchant import MerchantConfig

# MSMED Act 2006, s.15: 15 days without a written agreement, 45 with one.
MSMED_DAYS_NO_AGREEMENT = 15
MSMED_DAYS_WITH_AGREEMENT = 45
# s.16: three times the RBI bank rate, compounded monthly. Bank rate 5.50% (2026).
RBI_BANK_RATE = 0.0550
MSMED_INTEREST_RATE = 3 * RBI_BANK_RATE

AGEING_BUCKETS = ((0, 15, "0-15"), (16, 45, "16-45"), (46, 90, "46-90"), (91, 10_000, "90+"))

# The ladder, in order. Each rung names the action the engine will propose.
LADDER = [
    {"step": 1, "min_days": 0, "action": "invoice_reminder", "label": "Invoice reminder"},
    {"step": 2, "min_days": 7, "action": "statement_of_account", "label": "Statement of account"},
    {"step": 3, "min_days": 21, "action": "payment_link_sms", "label": "Payment link"},
    {"step": 4, "min_days": 35, "action": "virtual_account", "label": "Virtual account for bank transfer"},
    {"step": 5, "min_days": 46, "action": "msmed_notice", "label": "MSMED statutory interest notice"},
    {"step": 6, "min_days": 75, "action": "escalate", "label": "Human escalation"},
]


def ageing_bucket(days: int) -> str:
    for lo, hi, name in AGEING_BUCKETS:
        if lo <= days <= hi:
            return name
    return "90+"


def msmed_deadline_days(has_written_agreement: bool) -> int:
    return MSMED_DAYS_WITH_AGREEMENT if has_written_agreement else MSMED_DAYS_NO_AGREEMENT


def msmed_interest_paise(amount_paise: int, days_overdue: int, deadline_days: int) -> int:
    """Compound monthly at 3× the bank rate, from the statutory deadline."""
    late_days = max(0, days_overdue - deadline_days)
    if late_days <= 0:
        return 0
    months = late_days / 30.0
    return int(round(amount_paise * ((1 + MSMED_INTEREST_RATE / 12) ** months - 1)))


def ladder_step(days_overdue: int, is_mse: bool, dispute_open: bool) -> dict:
    """Where this invoice sits on the ladder. The statutory rung is skipped
    entirely when the supplier is not a registered MSE — the leverage does not
    exist, so claiming it would be a false threat."""
    if dispute_open:
        return {"step": 0, "action": "no_action", "label": "Held — dispute open", "reason": "A dispute is open on this invoice."}
    chosen = LADDER[0]
    for rung in LADDER:
        if days_overdue >= rung["min_days"]:
            if rung["action"] == "msmed_notice" and not is_mse:
                continue
            chosen = rung
    return {**chosen, "reason": f"{days_overdue} days past due."}


@dataclass
class InvoiceSource:
    """Razorpay invoices in ``issued`` or ``partially_paid`` state, past their
    due date, as receivable leaks."""

    name: str = "receivables"
    client: Any | None = None
    merchant: MerchantConfig | None = None

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": self.client is not None,
            "dataMode": "real",
            "note": (
                "Pulls issued and partially-paid invoices past their due date from your Razorpay account."
                if self.client
                else "Set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in backend/.env to pull invoices from your account."
            ),
        }

    def pull(self, limit: int = 500, include_not_yet_due: bool = False, **_: Any) -> tuple[list[LeakEvent], dict]:
        if self.client is None:
            return [], {"error": "no Razorpay keys configured"}
        now = datetime.now(timezone.utc)
        errors: list[str] = []
        items: list[dict] = []
        skip = 0
        while len(items) < limit * 2:
            try:
                page = self.client.invoice.all({"count": 100, "skip": skip})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"invoices: {type(exc).__name__}: {exc}")
                break
            batch = page.get("items", []) if isinstance(page, dict) else []
            items.extend(batch)
            if len(batch) < 100:
                break
            skip += 100

        leaks: list[LeakEvent] = []
        for inv in items:
            ev = self.normalize(inv, now=now, merchant=self.merchant, include_not_yet_due=include_not_yet_due)
            if ev:
                leaks.append(ev)
        leaks.sort(key=lambda e: -e.days_overdue)
        overdue = [e for e in leaks if e.days_overdue > 0]
        return leaks[:limit], {
            "invoicesScanned": len(items),
            "overdue": len(overdue),
            "amountPaise": sum(e.amount_paise for e in leaks[:limit]),
            "errors": errors,
        }

    @staticmethod
    def normalize(inv: dict, *, now: datetime, merchant: MerchantConfig | None, include_not_yet_due: bool = False) -> LeakEvent | None:
        status = str(inv.get("status", "")).lower()
        if status not in ("issued", "partially_paid"):
            return None
        due_ts = inv.get("expire_by") or inv.get("date")
        if not due_ts:
            return None
        due = datetime.fromtimestamp(float(due_ts), tz=timezone.utc)
        days_overdue = (now - due).days
        if days_overdue < 0 and not include_not_yet_due:
            return None

        amount_due = int(inv.get("amount_due") or inv.get("amount") or 0)
        if amount_due <= 0:
            return None
        cust = inv.get("customer_details") or inv.get("customer") or {}
        notes = inv.get("notes") if isinstance(inv.get("notes"), dict) else {}
        # A merchant flags an MSE counterparty and a written agreement on the
        # invoice; nothing is assumed, because a wrong assumption here is a
        # false statutory claim.
        is_mse = str(notes.get("mse_supplier", "")).lower() in ("1", "true", "yes")
        has_agreement = str(notes.get("written_agreement", "")).lower() in ("1", "true", "yes")
        dispute_open = str(notes.get("dispute", "")).lower() in ("1", "true", "yes", "open")
        deadline = msmed_deadline_days(has_agreement)

        inv_id = str(inv.get("id", ""))
        eid = "evt_" + hashlib.sha256(f"receivable|{inv_id}|{due.date()}".encode()).hexdigest()[:12]
        return LeakEvent(
            event_id=eid,
            kind="receivable_overdue",
            source="razorpay",
            payment_id="",
            subscription_id="",
            invoice_id=inv_id,
            customer_id=str(inv.get("customer_id") or notes.get("customer_id") or f"biz_{hashlib.sha256(inv_id.encode()).hexdigest()[:10]}"),
            counterparty_type="business",
            contact=cust.get("contact"),
            email=cust.get("email"),
            failed_at=due.isoformat(),
            amount_paise=amount_due,
            plan_name=str((inv.get("line_items") or [{}])[0].get("name") or inv.get("description") or "Invoice"),
            minutes_since_failure=max(0, int((now - due).total_seconds() // 60)),
            local_hour_ist=now.astimezone(timezone(timedelta(hours=5, minutes=30))).hour,
            customer_initiated=False,
            has_relationship=True,
            method="netbanking",
            issuer="",
            reason_code="RECEIVABLE_OVERDUE",
            reason_label=f"Invoice {days_overdue} days past due" if days_overdue > 0 else "Invoice due",
            failure_side="customer",
            reason_confidence="high",
            ambiguous=False,
            retriable=False,
            hard_decline=False,
            merchant_side=False,
            dispute_open=dispute_open,
            attempts_this_cycle=1,
            contacts_last_7d=0,
            retries_30d=0,
            consent_on_file=True,  # an existing invoice is a contractual relationship
            engagement=0.5,
            tenure_days=max(30, days_overdue),
            features_are_proxies=True,
            extras={
                "days_overdue": max(0, days_overdue),
                "ageing": ageing_bucket(max(0, days_overdue)),
                "mse_supplier": is_mse,
                "written_agreement": has_agreement,
                "statutory_deadline_days": deadline,
                "statutory_interest_paise": msmed_interest_paise(amount_due, max(0, days_overdue), deadline) if is_mse else 0,
                "invoice_status": status,
                "amount_paid_paise": int(inv.get("amount_paid") or 0),
                "short_url": inv.get("short_url"),
                "ladder": ladder_step(max(0, days_overdue), is_mse, dispute_open),
                "customer_name": cust.get("name"),
            },
        )
