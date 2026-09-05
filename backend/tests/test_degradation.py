"""Degradation cohorts: the downtime feed, the detector, and the hold."""

from __future__ import annotations

import time

from app.degradation import (
    DegradationMonitor,
    DowntimeFeed,
    SuccessRateDetector,
    cohort_key,
    leak_cohort_keys,
)
from app.leaks import LeakEvent
from app.merchant import MerchantConfig
from app.policy import evaluate_gate


class FakeRazorpay:
    """The shape the real client returns, from this account's actual feed."""

    def __init__(self, items):
        self._items = items
        self.calls = 0

    class _P:
        def __init__(self, outer):
            self.outer = outer

        def fetchDownTime(self):
            self.outer.calls += 1
            if isinstance(self.outer._items, Exception):
                raise self.outer._items
            return {"entity": "collection", "count": len(self.outer._items), "items": self.outer._items}

    @property
    def payment(self):
        return FakeRazorpay._P(self)


REAL_SHAPE = [
    {"id": "down_a", "entity": "payment.downtime", "method": "card", "begin": 1785387445, "end": None,
     "status": "started", "scheduled": False, "severity": "high", "instrument": {"issuer": "PUNB"}},
    {"id": "down_b", "entity": "payment.downtime", "method": "netbanking", "begin": 1788612866, "end": None,
     "status": "started", "scheduled": False, "severity": "high", "instrument": {"bank": "SBIN"}},
    {"id": "down_c", "entity": "payment.downtime", "method": "upi", "begin": 1785841792, "end": 1785845000,
     "status": "resolved", "scheduled": False, "severity": "medium", "instrument": {"vpa_handle": "kotak811"}},
]


def leak(**over) -> LeakEvent:
    base = dict(event_id="evt_x", amount_paise=49900, method="card", issuer="PUNB", network="Visa",
                minutes_since_failure=90, local_hour_ist=14, attempts_this_cycle=1, consent_on_file=True,
                reason_code="INSUFFICIENT_FUNDS", reason_label="Insufficient balance", failure_side="customer")
    base.update(over)
    return LeakEvent(**base)


def test_cohort_keys_prefer_the_specific_instrument():
    assert cohort_key("card", {"issuer": "PUNB"}) == "card:issuer=PUNB"
    assert cohort_key("netbanking", {"bank": "SBIN"}) == "netbanking:bank=SBIN"
    assert cohort_key("upi", {"vpa_handle": "kotak811"}) == "upi:vpa_handle=kotak811"
    assert cohort_key("card", None) == "card:*"
    keys = leak_cohort_keys(leak())
    assert "card:*" in keys and "card:issuer=PUNB" in keys


def test_downtime_feed_parses_and_caches():
    fake = FakeRazorpay(REAL_SHAPE)
    feed = DowntimeFeed(fake, ttl_seconds=60)
    cohorts = feed.cohorts()
    assert len(cohorts) == 3 and fake.calls == 1
    feed.cohorts()  # cached
    assert fake.calls == 1
    live = [c for c in cohorts if c.live]
    assert len(live) == 2  # the resolved one is not live
    assert {c.key for c in live} == {"card:issuer=PUNB", "netbanking:bank=SBIN"}
    assert all(c.source == "razorpay" and c.external_id for c in cohorts)


def test_feed_failure_is_survivable():
    feed = DowntimeFeed(FakeRazorpay(RuntimeError("upstream 500")))
    assert feed.cohorts() == []
    assert "RuntimeError" in (feed.last_error or "")


def test_no_client_means_no_cohorts():
    assert DowntimeFeed(None).cohorts() == []
    assert DegradationMonitor(None).view().public()["live"] == 0


def _stream(now: int, n_ok: int, n_fail: int, bucket: int, issuer="HDFC") -> list[dict]:
    """`now` must be bucket-aligned, or events spill into the next bucket and
    the stream no longer says what the test thinks it says."""
    assert now % 300 == 0, "align `now` to a bucket boundary"
    out = []
    for i in range(n_ok):
        out.append({"created_at": now + bucket * 300 + i, "method": "card", "status": "captured", "card": {"issuer": issuer}})
    for i in range(n_fail):
        out.append({"created_at": now + bucket * 300 + 100 + i, "method": "card", "status": "failed", "card": {"issuer": issuer}})
    return out


def _aligned_now() -> int:
    return ((int(time.time()) - 3600) // 300) * 300


def test_detector_fires_on_a_sustained_drop_and_not_on_noise():
    now = _aligned_now()
    det = SuccessRateDetector()
    healthy: list[dict] = []
    for b in range(6):
        healthy += _stream(now, 18, 2, b)
    assert det.observe(healthy) == [], "a healthy stream must not fire"

    det2 = SuccessRateDetector()
    degrading: list[dict] = []
    for b in range(4):
        degrading += _stream(now, 18, 2, b)      # 90%
    for b in range(4, 10):
        degrading += _stream(now, 4, 16, b)      # 20%
    fired = det2.observe(degrading)
    keys = {c.key for c in fired}
    assert "card:issuer=HDFC" in keys, f"expected the issuer cohort to fire, got {keys}"
    c = next(c for c in fired if c.key == "card:issuer=HDFC")
    assert c.source == "detector" and c.severity in ("high", "medium")
    assert c.success_rate is not None and c.baseline_rate is not None
    assert c.success_rate < c.baseline_rate


def test_detector_ignores_cohorts_below_the_attempt_floor():
    now = _aligned_now()
    det = SuccessRateDetector()
    thin = [{"created_at": now + i, "method": "card", "status": "failed", "card": {"issuer": "RARE"}} for i in range(4)]
    assert not [c for c in det.observe(thin) if "RARE" in c.key]


def test_slow_drift_alone_does_not_declare_an_outage():
    """CUSUM accumulates on drift; an outage is a present condition. A cohort
    that is currently serving fine must not be held because it drifted."""
    now = _aligned_now()
    det = SuccessRateDetector()
    drifting: list[dict] = []
    for b in range(14):
        ok = 20 - min(b, 3)  # 100% → 85%, then flat: real but small, never an outage
        drifting += _stream(now, ok, 20 - ok, b)
    assert det.observe(drifting) == []


def test_hold_blocks_contact_but_allows_backoff_retry():
    merchant = MerchantConfig()
    view = DegradationMonitor(FakeRazorpay(REAL_SHAPE)).view()
    ev = leak()
    cohort = view.holding(ev)
    assert cohort is not None and cohort.key == "card:issuer=PUNB"
    ev.degradation_hold = cohort.public()

    contact = evaluate_gate(ev, "payment_link_sms", "B", 0.3, merchant)
    assert contact.blocked_by == "DEGRADATION_HOLD"
    note = next(g["note"] for g in contact.gate if g["ruleId"] == "DEGRADATION_HOLD")
    assert "razorpay" in note.lower()

    retry = evaluate_gate(ev, "silent_retry", "B", 0.3, merchant)
    assert retry.blocked_by != "DEGRADATION_HOLD"
    assert next(g["verdict"] for g in retry.gate if g["ruleId"] == "DEGRADATION_HOLD") == "PASS"


def test_resolved_cohort_releases_the_hold():
    view = DegradationMonitor(FakeRazorpay(REAL_SHAPE)).view()
    upi = leak(method="upi", issuer="kotak811", network=None)
    assert view.holding(upi) is None, "a resolved downtime must not hold anything"


def test_declared_downtime_outranks_the_detector_on_the_same_key():
    from app.degradation import Cohort, DegradationView

    declared = Cohort(key="card:issuer=PUNB", source="razorpay", method="card", instrument={"issuer": "PUNB"},
                      severity="high", began_at="2026-09-05T00:00:00+00:00", detail="declared")
    detected = Cohort(key="card:issuer=PUNB", source="detector", method="card", instrument={"issuer": "PUNB"},
                      severity="medium", began_at="2026-09-05T00:05:00+00:00", detail="detected")
    view = DegradationView(cohorts=[detected, declared])
    assert view.by_key()["card:issuer=PUNB"].source == "razorpay"
