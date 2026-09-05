"""Degradation detection: the one direction where the customer is never the answer.

When an issuer's success rate falls off a cliff, every message sent about it is
pure spend that also blames the customer for a bank's problem. The right action
for the whole affected cohort is: hold, back off, retry after it clears.

Two signals, deliberately independent:

- **Razorpay's downtime feed** (``GET /v1/payments/downtimes``) — the acquirer's
  own view: ``method``, ``severity``, ``instrument.{bank, issuer, network, psp,
  vpa_handle}``, ``begin``/``end``. Authoritative, but it only sees what
  Razorpay declares.
- **Our own detector** — an EWMA baseline plus a one-sided CUSUM on the success
  rate per (method × instrument) computed from the payment stream. It catches
  degradation Razorpay has not declared, and it catches merchant-side breakage
  (a bad deploy shows as every method failing at once), which no acquirer feed
  reports.

A cohort from either source blocks customer contact on its members until it
clears. Both are recorded, so a trace always says which one held the event and
whether the two agreed.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .leaks import LeakEvent

# CUSUM on the success rate. k is the slack (how much of a drop we tolerate as
# noise); h is the decision threshold in units of the same scale. Tuned so a
# sustained ~15-point drop over a handful of buckets fires, and ordinary
# bucket-to-bucket wobble does not.
CUSUM_SLACK = 0.05
CUSUM_THRESHOLD = 0.25
EWMA_ALPHA = 0.25
MIN_ATTEMPTS = 12  # per cohort, before any claim is made
BUCKET_SECONDS = 300
# CUSUM answers "has this drifted down?"; an outage is a *present* condition.
# Without this, a long run of small drift eventually trips the threshold and
# declares a cohort down while it is currently serving fine — which would hold
# real customers' payments for nothing.
MIN_CURRENT_DROP = 0.15


def cohort_key(method: str | None, instrument: dict | str | None) -> str:
    """A stable name for a (method × instrument) cohort, used by both sources."""
    m = (method or "unknown").lower()
    if isinstance(instrument, str):
        return f"{m}:{instrument}"
    inst = instrument or {}
    for field_name in ("issuer", "bank", "vpa_handle", "psp", "network", "wallet"):
        v = inst.get(field_name)
        if v:
            return f"{m}:{field_name}={v}"
    return f"{m}:*"


def leak_cohort_keys(ev: LeakEvent) -> list[str]:
    """Every cohort a leak belongs to — the specific instrument and the
    method-wide one, so a method-level outage catches it too."""
    keys = [f"{ev.method.lower()}:*"]
    for field_name, value in (("issuer", ev.issuer), ("bank", ev.issuer), ("psp", ev.psp), ("network", ev.network)):
        if value:
            keys.append(f"{ev.method.lower()}:{field_name}={value}")
    # UPI handles arrive as the issuer on normalised leaks, and a UPI Autopay
    # mandate rides the same handle a plain UPI payment does — so an outage
    # declared on `upi` must hold `upi_autopay` leaks too.
    if ev.method in ("upi", "upi_autopay") and ev.issuer:
        handle = ev.issuer.lower()
        keys += [f"{ev.method.lower()}:vpa_handle={handle}", f"upi:vpa_handle={handle}", "upi:*"]
    return list(dict.fromkeys(keys))


@dataclass
class Cohort:
    key: str
    source: str  # razorpay | detector
    method: str
    instrument: dict
    severity: str  # high | medium | low
    began_at: str
    ended_at: str | None = None
    status: str = "started"  # started | resolved | scheduled
    detail: str = ""
    external_id: str | None = None
    success_rate: float | None = None
    baseline_rate: float | None = None
    attempts: int = 0

    @property
    def live(self) -> bool:
        return self.status in ("started", "scheduled") and self.ended_at is None

    def public(self) -> dict:
        return {
            "key": self.key,
            "source": self.source,
            "method": self.method,
            "instrument": self.instrument,
            "severity": self.severity,
            "beganAt": self.began_at,
            "endedAt": self.ended_at,
            "status": self.status,
            "detail": self.detail,
            "externalId": self.external_id,
            "successRate": self.success_rate,
            "baselineRate": self.baseline_rate,
            "attempts": self.attempts,
        }


def _iso(ts: int | float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")


class DowntimeFeed:
    """Razorpay's declared downtimes, cached briefly so a batch does not
    re-fetch per event."""

    def __init__(self, client: Any | None, ttl_seconds: int = 60) -> None:
        self.client = client
        self.ttl = ttl_seconds
        self._at: float = 0.0
        self._cohorts: list[Cohort] = []
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return self.client is not None

    def cohorts(self, force: bool = False) -> list[Cohort]:
        if self.client is None:
            return []
        now = time.time()
        if not force and self._cohorts and (now - self._at) < self.ttl:
            return self._cohorts
        try:
            page = self.client.payment.fetchDownTime()
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 — a feed outage must not stop a batch
            self.last_error = f"{type(exc).__name__}: {exc}"
            return self._cohorts
        out: list[Cohort] = []
        for item in page.get("items", []) if isinstance(page, dict) else []:
            inst = item.get("instrument") or {}
            out.append(
                Cohort(
                    key=cohort_key(item.get("method"), inst),
                    source="razorpay",
                    method=str(item.get("method") or "unknown"),
                    instrument=inst,
                    severity=str(item.get("severity") or "medium"),
                    began_at=_iso(item.get("begin")) or "",
                    ended_at=_iso(item.get("end")),
                    status=str(item.get("status") or "started"),
                    detail=f"Razorpay declared {item.get('severity')} {item.get('method')} downtime"
                    + (f" on {', '.join(f'{k}={v}' for k, v in inst.items())}" if inst else ""),
                    external_id=str(item.get("id")) if item.get("id") else None,
                )
            )
        self._cohorts, self._at = out, now
        return out


class SuccessRateDetector:
    """EWMA baseline + one-sided CUSUM on the success rate, per cohort.

    Fed the payment stream a source already pulled — no extra API calls. Buckets
    are five minutes; a cohort must have MIN_ATTEMPTS before it can be declared,
    so a single failed payment on a rare issuer never trips it.
    """

    def __init__(self) -> None:
        self.state: dict[str, dict] = {}

    def observe(self, payments: Iterable[dict]) -> list[Cohort]:
        buckets: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
        for p in payments:
            ts = p.get("created_at")
            if not isinstance(ts, (int, float)):
                continue
            method = str(p.get("method") or "unknown").lower()
            card = p.get("card") if isinstance(p.get("card"), dict) else {}
            inst: dict = {}
            if card.get("issuer"):
                inst = {"issuer": card["issuer"]}
            elif p.get("bank"):
                inst = {"bank": p["bank"]}
            elif p.get("wallet"):
                inst = {"wallet": p["wallet"]}
            elif isinstance(p.get("vpa"), str) and "@" in p["vpa"]:
                inst = {"vpa_handle": p["vpa"].split("@", 1)[1].lower()}
            ok = 1 if str(p.get("status", "")).lower() in ("captured", "authorized") else 0
            b = int(ts) // BUCKET_SECONDS
            for key in {cohort_key(method, inst), cohort_key(method, None)}:
                buckets[key][b].append(ok)

        out: list[Cohort] = []
        for key, by_bucket in buckets.items():
            total = sum(len(v) for v in by_bucket.values())
            if total < MIN_ATTEMPTS:
                continue
            st = self.state.setdefault(key, {"ewma": None, "cusum": 0.0, "firing": False, "began": None})
            latest_rate = None
            for b in sorted(by_bucket):
                obs = by_bucket[b]
                rate = sum(obs) / len(obs)
                latest_rate = rate
                if st["ewma"] is None:
                    st["ewma"] = rate
                    continue
                # One-sided: only a drop below the running baseline accumulates.
                st["cusum"] = max(0.0, st["cusum"] + (st["ewma"] - rate - CUSUM_SLACK))
                currently_bad = (st["ewma"] - rate) >= MIN_CURRENT_DROP
                st["ewma"] = EWMA_ALPHA * rate + (1 - EWMA_ALPHA) * st["ewma"]
                if st["cusum"] >= CUSUM_THRESHOLD and currently_bad and not st["firing"]:
                    st["firing"] = True
                    st["began"] = _iso(b * BUCKET_SECONDS)
                elif st["cusum"] == 0.0 and st["firing"]:
                    st["firing"] = False
                    st["began"] = None
            if st["firing"]:
                method, _, rest = key.partition(":")
                inst = {}
                if "=" in rest:
                    k, _, v = rest.partition("=")
                    inst = {k: v}
                drop = (st["ewma"] or 0) - (latest_rate or 0)
                out.append(
                    Cohort(
                        key=key,
                        source="detector",
                        method=method,
                        instrument=inst,
                        severity="high" if drop > 0.3 else "medium",
                        began_at=st["began"] or _iso(time.time()) or "",
                        status="started",
                        detail=(
                            f"Success rate on {key} fell to {(latest_rate or 0):.0%} against a {(st['ewma'] or 0):.0%} baseline "
                            f"over {total} attempts (CUSUM {st['cusum']:.2f} ≥ {CUSUM_THRESHOLD})."
                        ),
                        success_rate=round(latest_rate or 0, 4),
                        baseline_rate=round(st["ewma"] or 0, 4),
                        attempts=total,
                    )
                )
        return out


@dataclass
class DegradationView:
    """The live cohorts for one batch, and which leaks they hold."""

    cohorts: list[Cohort] = field(default_factory=list)
    feed_error: str | None = None
    feed_available: bool = False

    def by_key(self) -> dict[str, Cohort]:
        # A declared Razorpay downtime outranks our detector on the same key.
        out: dict[str, Cohort] = {}
        for c in self.cohorts:
            if not c.live:
                continue
            if c.key not in out or c.source == "razorpay":
                out[c.key] = c
        return out

    def holding(self, ev: LeakEvent) -> Cohort | None:
        live = self.by_key()
        for key in leak_cohort_keys(ev):
            if key in live:
                return live[key]
        return None

    def public(self) -> dict:
        return {
            "cohorts": [c.public() for c in self.cohorts],
            "live": len([c for c in self.cohorts if c.live]),
            "feedAvailable": self.feed_available,
            "feedError": self.feed_error,
            "sources": sorted({c.source for c in self.cohorts}),
        }


class DegradationMonitor:
    def __init__(self, client: Any | None) -> None:
        self.feed = DowntimeFeed(client)
        self.detector = SuccessRateDetector()

    def view(self, payments: Iterable[dict] | None = None, extra: Iterable[Cohort] | None = None) -> DegradationView:
        cohorts = list(self.feed.cohorts())
        if payments:
            cohorts.extend(self.detector.observe(payments))
        if extra:
            cohorts.extend(extra)
        return DegradationView(cohorts=cohorts, feed_error=self.feed.last_error, feed_available=self.feed.available)
