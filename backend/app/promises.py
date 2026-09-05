"""Promise-to-pay: the strongest stopping rule there is.

While a promise is live, every other action on that counterparty is blocked —
including the silent retry. An agent that keeps chasing after "I'll pay Friday"
is the agent that gets a complaint, and the RBI's recovery-agent norms treat
repeat contact after an agreed date as harassment.

The state machine follows collections practice:

    open → reminded (T−1) → kept | broken (T+3) → recontacted (≤48h)
                                 → second_broken → risk_escalated

Industry guidance: a reminder the day before lifts the kept rate materially,
a promise is not "broken" until a few days past its date, and a second broken
promise is a risk signal rather than another collections cycle. Verification is
never self-reported: a promise is kept only when Razorpay says the money
arrived (``payment_link.paid``, ``invoice.paid``, ``virtual_account.credited``),
which is the same attribution path as any other outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .store import Store, now_iso

BROKEN_AFTER_DAYS = 3  # flag as broken this long past the promised date
RECONTACT_WITHIN_HOURS = 48  # after breaking, before escalating
REMIND_BEFORE_HOURS = 24

STATES = ("open", "reminded", "kept", "partially_kept", "broken", "recontacted", "second_broken", "risk_escalated", "cancelled")
OPEN_STATES = ("open", "reminded", "recontacted")


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Promise:
    promise_id: int
    counterparty_id: str
    event_id: str | None
    amount_paise: int
    due_at: str
    state: str
    captured_via: str
    verbatim: str
    created_at: str
    reminded_at: str | None = None
    resolved_at: str | None = None
    verified_by: str | None = None
    amount_paid_paise: int = 0
    broken_count: int = 0

    def public(self) -> dict:
        return {
            "promiseId": self.promise_id,
            "counterpartyId": self.counterparty_id,
            "eventId": self.event_id,
            "amountPaise": self.amount_paise,
            "dueAt": self.due_at,
            "state": self.state,
            "capturedVia": self.captured_via,
            "verbatim": self.verbatim,
            "createdAt": self.created_at,
            "remindedAt": self.reminded_at,
            "resolvedAt": self.resolved_at,
            "verifiedBy": self.verified_by,
            "amountPaidPaise": self.amount_paid_paise,
            "brokenCount": self.broken_count,
            "open": self.state in OPEN_STATES,
        }


class PromiseBook:
    def __init__(self, store: Store) -> None:
        self.store = store

    # ------------------------------------------------------------------ read

    @staticmethod
    def _row(r) -> Promise:
        return Promise(
            promise_id=r["promise_id"],
            counterparty_id=r["counterparty_id"],
            event_id=r["event_id"],
            amount_paise=r["amount_paise"],
            due_at=r["due_at"],
            state=r["state"],
            captured_via=r["captured_via"],
            verbatim=r["verbatim"] or "",
            created_at=r["created_at"],
            reminded_at=r["reminded_at"],
            resolved_at=r["resolved_at"],
            verified_by=r["verified_by"],
            amount_paid_paise=r["amount_paid_paise"] or 0,
            broken_count=r["broken_count"] or 0,
        )

    def all(self, limit: int = 200) -> list[Promise]:
        with self.store.lock:
            rows = self.store.conn.execute("SELECT * FROM promises ORDER BY due_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def open_for(self, counterparty_id: str) -> Promise | None:
        """The live promise holding this counterparty, if any."""
        if not counterparty_id:
            return None
        with self.store.lock:
            r = self.store.conn.execute(
                f"SELECT * FROM promises WHERE counterparty_id = ? AND state IN ({','.join('?' * len(OPEN_STATES))}) ORDER BY due_at DESC LIMIT 1",
                (counterparty_id, *OPEN_STATES),
            ).fetchone()
        return self._row(r) if r else None

    def latest_for(self, counterparty_id: str) -> Promise | None:
        """The most recent promise in any state. Used to carry the broken count
        forward: a second promise from someone who already broke one is a
        different situation, and it stops being one if the count resets."""
        if not counterparty_id:
            return None
        with self.store.lock:
            r = self.store.conn.execute(
                "SELECT * FROM promises WHERE counterparty_id = ? ORDER BY promise_id DESC LIMIT 1",
                (counterparty_id,),
            ).fetchone()
        return self._row(r) if r else None

    def open_map(self) -> dict[str, Promise]:
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT * FROM promises WHERE state IN ({','.join('?' * len(OPEN_STATES))})", OPEN_STATES
            ).fetchall()
        return {r["counterparty_id"]: self._row(r) for r in rows}

    def get(self, promise_id: int) -> Promise | None:
        with self.store.lock:
            r = self.store.conn.execute("SELECT * FROM promises WHERE promise_id = ?", (promise_id,)).fetchone()
        return self._row(r) if r else None

    # ----------------------------------------------------------------- write

    def record(
        self,
        counterparty_id: str,
        amount_paise: int,
        due_at: str,
        captured_via: str,
        verbatim: str = "",
        event_id: str | None = None,
    ) -> Promise:
        """Capture a promise. A second open promise for the same counterparty
        replaces the first: people renegotiate, and two live promises would be
        two live holds with different dates."""
        # The broken count follows the counterparty, not the promise: it comes
        # from the last promise in any state, so breaking one and making
        # another does not wipe the history that made it a risk signal.
        previous = self.latest_for(counterparty_id)
        broken_count = previous.broken_count if previous else 0
        existing = self.open_for(counterparty_id)
        if existing:
            self._set_state(existing.promise_id, "cancelled", resolved_at=now_iso())
        with self.store.transaction() as c:
            cur = c.execute(
                """INSERT INTO promises (counterparty_id, event_id, amount_paise, due_at, state, captured_via,
                                          verbatim, created_at, broken_count)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (counterparty_id, event_id, amount_paise, due_at, "open", captured_via, verbatim, now_iso(), broken_count),
            )
            pid = int(cur.lastrowid)
        self.store.append_audit(
            "promise.recorded",
            {
                "promiseId": pid,
                "counterpartyId": counterparty_id,
                "eventId": event_id,
                "amountPaise": amount_paise,
                "dueAt": due_at,
                "capturedVia": captured_via,
                "verbatim": verbatim[:400],
                "replacedOpenPromise": existing.promise_id if existing else None,
            },
            actor="agent:B" if captured_via != "operator" else "operator",
            ref=event_id or counterparty_id,
        )
        return self.get(pid)  # type: ignore[return-value]

    def _set_state(self, promise_id: int, state: str, **fields) -> None:
        sets = ["state = ?"]
        vals: list = [state]
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(promise_id)
        with self.store.transaction() as c:
            c.execute(f"UPDATE promises SET {', '.join(sets)} WHERE promise_id = ?", vals)

    def mark_kept(self, promise_id: int, amount_paid_paise: int, verified_by: str) -> None:
        p = self.get(promise_id)
        if p is None:
            return
        state = "kept" if amount_paid_paise >= p.amount_paise else "partially_kept"
        self._set_state(promise_id, state, resolved_at=now_iso(), verified_by=verified_by, amount_paid_paise=amount_paid_paise)
        self.store.append_audit(
            "promise.kept",
            {"promiseId": promise_id, "counterpartyId": p.counterparty_id, "state": state, "amountPaidPaise": amount_paid_paise, "verifiedBy": verified_by},
            actor="webhook" if verified_by.startswith(("poll", "webhook")) else "operator",
            ref=p.event_id or p.counterparty_id,
        )

    def settle_from_outcome(self, counterparty_id: str, amount_paise: int, source: str) -> bool:
        """A recovered payment from a counterparty with a live promise keeps it."""
        p = self.open_for(counterparty_id)
        if p is None:
            return False
        self.mark_kept(p.promise_id, amount_paise, verified_by=source)
        return True

    # ------------------------------------------------------------------ tick

    def tick(self, now: datetime | None = None) -> dict:
        """Advance the state machine: send reminders, flag broken promises,
        escalate second breaks. Idempotent — safe to call on every batch."""
        now = now or datetime.now(timezone.utc)
        report = {"reminded": 0, "broken": 0, "escalated": 0, "checked": 0}
        for p in self.all(limit=1000):
            if p.state not in OPEN_STATES:
                continue
            report["checked"] += 1
            due = _parse(p.due_at)
            if due is None:
                continue
            already_broken = now >= due + timedelta(days=BROKEN_AFTER_DAYS)
            # Break before reminding. A promise recorded long after its date —
            # or a tick that has not run for days — must not send a "due
            # tomorrow" reminder for a date that has already passed.
            if not already_broken and p.state == "open" and not p.reminded_at and now >= due - timedelta(hours=REMIND_BEFORE_HOURS):
                self._set_state(p.promise_id, "reminded", reminded_at=now_iso())
                self.store.append_audit(
                    "promise.reminded",
                    {"promiseId": p.promise_id, "counterpartyId": p.counterparty_id, "dueAt": p.due_at,
                     "note": f"Reminder scheduled {REMIND_BEFORE_HOURS}h before the promised date."},
                    actor="scheduler", ref=p.event_id or p.counterparty_id,
                )
                report["reminded"] += 1
                continue
            if p.state in ("open", "reminded") and already_broken:
                broken = p.broken_count + 1
                state = "second_broken" if broken >= 2 else "broken"
                self._set_state(p.promise_id, state, broken_count=broken, resolved_at=None)
                self.store.append_audit(
                    "promise.broken",
                    {"promiseId": p.promise_id, "counterpartyId": p.counterparty_id, "dueAt": p.due_at,
                     "brokenCount": broken, "state": state,
                     "note": f"No payment {BROKEN_AFTER_DAYS} days past the promised date."},
                    actor="scheduler", ref=p.event_id or p.counterparty_id,
                )
                report["broken"] += 1
                if state == "second_broken":
                    self._set_state(p.promise_id, "risk_escalated", resolved_at=now_iso())
                    self.store.append_audit(
                        "promise.escalated",
                        {"promiseId": p.promise_id, "counterpartyId": p.counterparty_id, "brokenCount": broken,
                         "note": "Second broken promise — escalated from collections to a risk decision."},
                        actor="scheduler", ref=p.event_id or p.counterparty_id,
                    )
                    report["escalated"] += 1
        return report

    # ---------------------------------------------------------------- stats

    def stats(self) -> dict:
        with self.store.lock:
            rows = self.store.conn.execute("SELECT state, captured_via, COUNT(*) n, SUM(amount_paise) amt, SUM(amount_paid_paise) paid FROM promises GROUP BY state, captured_via").fetchall()
        by_state: dict[str, int] = {}
        by_channel: dict[str, dict[str, int]] = {}
        total = kept = broken = 0
        promised = paid = 0
        for r in rows:
            by_state[r["state"]] = by_state.get(r["state"], 0) + r["n"]
            ch = by_channel.setdefault(r["captured_via"], {"kept": 0, "broken": 0, "total": 0})
            ch["total"] += r["n"]
            total += r["n"]
            promised += r["amt"] or 0
            paid += r["paid"] or 0
            if r["state"] in ("kept", "partially_kept"):
                kept += r["n"]
                ch["kept"] += r["n"]
            if r["state"] in ("broken", "second_broken", "risk_escalated"):
                broken += r["n"]
                ch["broken"] += r["n"]
        resolved = kept + broken
        return {
            "total": total,
            "byState": by_state,
            "byChannel": by_channel,
            "keptRate": round(kept / resolved, 4) if resolved else None,
            "open": sum(by_state.get(s, 0) for s in OPEN_STATES),
            "promisedPaise": promised,
            "paidPaise": paid,
            "brokenAfterDays": BROKEN_AFTER_DAYS,
            "recontactWithinHours": RECONTACT_WITHIN_HOURS,
        }
