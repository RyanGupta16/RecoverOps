"""Mandate retry sequencer: when to retry, not just whether.

For UPI Autopay and e-mandate the scarce resource is attempts, not messages.
NPCI allows one execution plus three retries per cycle, and from May 2026
executions must run in non-peak windows. A retry fired on the 28th against an
account credited on the 1st burns one of four attempts for nothing.

So the sequencer picks a slot. Two inputs:

- **P(balance sufficient | day of month)** — salary credits cluster at the
  start of the month and around the 7th for government and PSU payrolls; the
  end of the month is the worst time to ask. This is a prior, and it is
  labelled as one, not as a measurement.
- **The NPCI execution windows** — before 10:00, 13:00–17:00, after 21:00 IST.

The output is a scheduled datetime, the pre-debit notice time 24 hours before
it, and the expected recovery per attempt spent — which is what the value model
compares against spending a message instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .leaks import LeakEvent
from .merchant import MerchantConfig
from .policy import in_mandate_window, next_mandate_window_hour

IST = timezone(timedelta(hours=5, minutes=30))

# P(sufficient balance) by day of month. Salary credits land on the 1st and
# around the 7th; the 25th–31st is the trough. A prior, not a measurement —
# the learning loop replaces it once real outcomes exist.
DAY_OF_MONTH_LIQUIDITY = {
    1: 0.86, 2: 0.84, 3: 0.80, 4: 0.75, 5: 0.72, 6: 0.72, 7: 0.78,
    8: 0.74, 9: 0.70, 10: 0.68, 11: 0.65, 12: 0.63, 13: 0.61, 14: 0.60,
    15: 0.62, 16: 0.58, 17: 0.56, 18: 0.54, 19: 0.52, 20: 0.50,
    21: 0.48, 22: 0.46, 23: 0.44, 24: 0.42, 25: 0.40, 26: 0.38,
    27: 0.36, 28: 0.35, 29: 0.36, 30: 0.38, 31: 0.42,
}

MAX_LOOKAHEAD_DAYS = 10
PRE_DEBIT_NOTICE_HOURS = 24


def liquidity(day: int) -> float:
    return DAY_OF_MONTH_LIQUIDITY.get(max(1, min(31, day)), 0.5)


@dataclass
class Slot:
    at: datetime
    notice_at: datetime
    p_sufficient: float
    attempts_left: int
    rationale: str

    def public(self) -> dict:
        return {
            "scheduledFor": self.at.isoformat(),
            "scheduledHourIst": self.at.astimezone(IST).hour,
            "preDebitNoticeAt": self.notice_at.isoformat(),
            "preDebitNoticeHours": PRE_DEBIT_NOTICE_HOURS,
            "pSufficient": round(self.p_sufficient, 4),
            "attemptsLeft": self.attempts_left,
            "rationale": self.rationale,
        }


def fixed_clock_slot(ev: LeakEvent, now: datetime | None = None) -> Slot:
    """What Razorpay's own T+1 clock would do: tomorrow, same time, regardless
    of whether the account is likely to have money in it."""
    now = now or datetime.now(timezone.utc)
    at = now + timedelta(days=1)
    day = at.astimezone(IST).day
    return Slot(
        at=at,
        notice_at=at - timedelta(hours=PRE_DEBIT_NOTICE_HOURS),
        p_sufficient=liquidity(day),
        attempts_left=0,
        rationale=f"Fixed T+1 clock: retry tomorrow (day {day} of the month, P(balance) ≈ {liquidity(day):.0%}).",
    )


def choose_slot(ev: LeakEvent, merchant: MerchantConfig, now: datetime | None = None) -> Slot:
    """The best execution slot inside the next MAX_LOOKAHEAD_DAYS, subject to
    NPCI windows, the pre-debit notice, and how many attempts remain."""
    now = now or datetime.now(timezone.utc)
    attempts_left = max(0, merchant.max_mandate_attempts - ev.attempts_this_cycle)
    earliest = now + timedelta(hours=PRE_DEBIT_NOTICE_HOURS)

    best: Slot | None = None
    for day_offset in range(0, MAX_LOOKAHEAD_DAYS + 1):
        candidate_day = (now + timedelta(days=day_offset)).astimezone(IST)
        p = liquidity(candidate_day.day)
        # Prefer the first permissible window on that day, at or after 09:00
        # local so the debit is not a 3 a.m. surprise on the statement.
        hour = next_mandate_window_hour(max(candidate_day.hour if day_offset == 0 else 0, 8))
        at_ist = candidate_day.replace(hour=hour, minute=0, second=0, microsecond=0)
        at = at_ist.astimezone(timezone.utc)
        if at < earliest:
            continue
        if not in_mandate_window(at.astimezone(IST).hour):
            continue
        # Waiting has a cost: money later is worth slightly less, and the
        # subscription's other clocks keep running.
        discounted = p * (0.985 ** day_offset)
        if best is None or discounted > best.p_sufficient:
            best = Slot(
                at=at,
                notice_at=at - timedelta(hours=PRE_DEBIT_NOTICE_HOURS),
                p_sufficient=discounted,
                attempts_left=attempts_left,
                rationale=(
                    f"Day {at_ist.day} of the month, {at_ist.hour:02d}:00 IST — P(balance sufficient) ≈ {p:.0%}, "
                    f"inside an NPCI non-peak window, {attempts_left} of {merchant.max_mandate_attempts} attempts left."
                ),
            )
    if best is None:
        return fixed_clock_slot(ev, now)
    return best


def sequence(ev: LeakEvent, merchant: MerchantConfig, now: datetime | None = None) -> dict:
    """Compare the chosen slot against the fixed clock, and say what it buys."""
    chosen = choose_slot(ev, merchant, now)
    fixed = fixed_clock_slot(ev, now)
    lift = chosen.p_sufficient - fixed.p_sufficient
    return {
        "chosen": chosen.public(),
        "fixedClock": fixed.public(),
        "pSufficientLift": round(lift, 4),
        "expectedRecoveryPaise": int(round(chosen.p_sufficient * ev.amount_paise)),
        "fixedClockRecoveryPaise": int(round(fixed.p_sufficient * ev.amount_paise)),
        "attemptsLeft": chosen.attempts_left,
        "note": (
            f"Scheduling the retry for the chosen slot rather than the fixed T+1 clock moves P(balance sufficient) "
            f"by {lift:+.1%} on the same attempt. Attempts, not messages, are the scarce resource under NPCI's cap of "
            f"{merchant.max_mandate_attempts} per cycle."
        ),
    }


def apply_schedule(ev: LeakEvent, merchant: MerchantConfig, now: datetime | None = None) -> dict:
    """Attach the schedule to the leak so the gate can evaluate the execution
    window and the pre-debit notice against a concrete slot."""
    plan = sequence(ev, merchant, now)
    ev.extras["schedule"] = plan
    ev.extras["scheduled_hour_ist"] = plan["chosen"]["scheduledHourIst"]
    ev.extras["pre_debit_notice_hours"] = PRE_DEBIT_NOTICE_HOURS
    return plan
