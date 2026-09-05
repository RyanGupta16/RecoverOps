"""Outcome attribution: how a decision becomes a measured result.

This is the module that turns a claim into evidence, so its failure modes
matter more than most: attributing the wrong outcome corrupts the training set
and the measured effect at the same time. Razorpay is polled through a fake
client here so every branch — including the ones a live account rarely hits —
is exercised deterministically.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.outcomes import STALE_AFTER_DAYS
from app.runtime import Runtime


class FakeRazorpay:
    """Returns whatever the test sets, and records what was asked for."""

    def __init__(self, links=None, orders=None, subs=None, payments=None, raises=None):
        self.links, self.orders, self.subs = links or {}, orders or {}, subs or {}
        self._payments, self.raises = payments or [], raises
        self.asked: list[str] = []
        outer = self

        class _PL:
            def fetch(self, i):
                outer.asked.append(f"payment_link:{i}")
                if outer.raises: raise outer.raises
                return outer.links[i]

        class _O:
            def fetch(self, i):
                outer.asked.append(f"order:{i}")
                return outer.orders[i]

        class _S:
            def fetch(self, i):
                outer.asked.append(f"subscription:{i}")
                return outer.subs[i]

        class _P:
            def all(self, params=None):
                outer.asked.append("payment.all")
                return {"items": outer._payments}

        self.payment_link, self.order, self.subscription, self.payment = _PL(), _O(), _S(), _P()


@pytest.fixture
def rt(tmp_path):
    return Runtime.build(store_path=tmp_path / "oa.db")


def seed_real_leak(rt, *, external_kind=None, external_id=None, subscription_id=None,
                   customer_id="cust_oa", amount=249900, minutes_ago=90):
    now = int(datetime.now(timezone.utc).timestamp())
    rows = [{
        "id": "pay_oa", "amount": amount, "status": "failed", "method": "card",
        "card": {"network": "Visa", "issuer": "HDFC"}, "token_id": "tok",
        "customer_id": customer_id, "contact": "+919812345111",
        "error_reason": "insufficient_funds", "error_source": "customer",
        "created_at": now - minutes_ago * 60,
    }]
    meta = rt.sources["file"].save(json.dumps({"items": rows}).encode(), "oa.json")
    summary = rt.run_and_store("file", file_id=meta["fileId"])
    leak = rt.store.leaks_for_batch(summary["batchId"])[0]
    sets, params = [], []
    for col, val in (("external_kind", external_kind), ("external_id", external_id),
                     ("subscription_id", subscription_id)):
        if val is not None:
            sets.append(f"{col} = ?"); params.append(val)
    if sets:
        params += [summary["batchId"], leak["event_id"]]
        with rt.store.transaction() as c:
            c.execute(f"UPDATE leaks SET {', '.join(sets)} WHERE batch_id = ? AND event_id = ?", params)
    return summary["batchId"], rt.store.latest_leak(leak["event_id"])


# ------------------------------------------------------------------ polling


def test_a_paid_payment_link_resolves_the_leak(rt):
    _, leak = seed_real_leak(rt, external_kind="payment_link", external_id="plink_1")
    rt.outcomes.client = FakeRazorpay(links={"plink_1": {"id": "plink_1", "status": "paid"}})
    report = rt.outcomes.sync()
    assert report["recovered"] == 1 and report["churned"] == 0
    after = rt.store.latest_leak(leak["event_id"])
    assert after["outcome_state"] == "resolved" and after["outcome_recovered"] == 1
    assert after["outcome_source"] == "poll:payment_link"


def test_a_partially_paid_link_still_counts_as_recovered(rt):
    _, leak = seed_real_leak(rt, external_kind="payment_link", external_id="plink_2")
    rt.outcomes.client = FakeRazorpay(links={"plink_2": {"id": "plink_2", "status": "partially_paid"}})
    rt.outcomes.sync()
    assert rt.store.latest_leak(leak["event_id"])["outcome_recovered"] == 1


def test_an_expired_link_does_not_resolve_on_its_own(rt):
    """An expired link is not a churn signal — the subscription may still
    recover on its own retry, so the leak stays pending."""
    _, leak = seed_real_leak(rt, external_kind="payment_link", external_id="plink_3")
    rt.outcomes.client = FakeRazorpay(links={"plink_3": {"id": "plink_3", "status": "expired"}})
    report = rt.outcomes.sync()
    assert report["stillPending"] == 1 and report["recovered"] == 0
    assert rt.store.latest_leak(leak["event_id"])["outcome_state"] == "pending"


def test_a_paid_retry_order_resolves_the_leak(rt):
    _, leak = seed_real_leak(rt, external_kind="order", external_id="order_1")
    rt.outcomes.client = FakeRazorpay(orders={"order_1": {"id": "order_1", "status": "paid"}})
    rt.outcomes.sync()
    got = rt.store.latest_leak(leak["event_id"])
    assert got["outcome_recovered"] == 1 and got["outcome_source"] == "poll:order"


@pytest.mark.parametrize("status,recovered", [("active", 1), ("authenticated", 1), ("completed", 1),
                                              ("resumed", 1), ("cancelled", 0), ("expired", 0)])
def test_subscription_states_resolve_in_the_right_direction(rt, status, recovered):
    _, leak = seed_real_leak(rt, subscription_id="sub_1")
    rt.outcomes.client = FakeRazorpay(subs={"sub_1": {"id": "sub_1", "status": status}})
    rt.outcomes.sync()
    got = rt.store.latest_leak(leak["event_id"])
    assert got["outcome_state"] == "resolved"
    assert got["outcome_recovered"] == recovered
    assert got["outcome_churned"] == (0 if recovered else 1)


def test_a_still_pending_subscription_is_left_alone(rt):
    _, leak = seed_real_leak(rt, subscription_id="sub_2")
    rt.outcomes.client = FakeRazorpay(subs={"sub_2": {"id": "sub_2", "status": "pending"}})
    report = rt.outcomes.sync()
    assert report["stillPending"] == 1
    assert rt.store.latest_leak(leak["event_id"])["outcome_state"] == "pending"


def test_a_matching_captured_payment_is_the_last_resort(rt):
    """With no Razorpay object attached, a captured payment from the same
    customer for the same amount is the remaining signal."""
    _, leak = seed_real_leak(rt, customer_id="cust_match", amount=249900)
    rt.outcomes.client = FakeRazorpay(payments=[
        {"id": "pay_other", "customer_id": "cust_match", "status": "captured", "amount": 111},
        {"id": "pay_hit", "customer_id": "cust_match", "status": "captured", "amount": 249900},
    ])
    rt.outcomes.sync()
    got = rt.store.latest_leak(leak["event_id"])
    assert got["outcome_recovered"] == 1 and got["outcome_source"] == "poll:payment"


def test_a_different_amount_or_customer_does_not_match(rt):
    _, leak = seed_real_leak(rt, customer_id="cust_a", amount=249900)
    rt.outcomes.client = FakeRazorpay(payments=[
        {"id": "p1", "customer_id": "cust_b", "status": "captured", "amount": 249900},
        {"id": "p2", "customer_id": "cust_a", "status": "captured", "amount": 100},
        {"id": "p3", "customer_id": "cust_a", "status": "failed", "amount": 249900},
    ])
    rt.outcomes.sync()
    assert rt.store.latest_leak(leak["event_id"])["outcome_state"] == "pending"


# ------------------------------------------------------------- failure modes


def test_a_failing_probe_is_recorded_and_does_not_stop_the_sync(rt):
    """One unreadable object must not abandon every other pending leak."""
    _, leak = seed_real_leak(rt, external_kind="payment_link", external_id="plink_boom")
    rt.outcomes.client = FakeRazorpay(links={}, raises=RuntimeError("upstream 500"))
    report = rt.outcomes.sync()
    assert report["errors"] and "RuntimeError" in report["errors"][0]
    assert rt.store.latest_leak(leak["event_id"])["outcome_state"] == "pending"


def test_stale_leaks_age_out_as_unresolved_not_as_failures(rt):
    """After three weeks with no signal, 'we never found out' is the honest
    state — leaving it pending forever would inflate the pending count and
    starve the learning loop."""
    _, leak = seed_real_leak(rt, minutes_ago=(STALE_AFTER_DAYS + 2) * 24 * 60)
    rt.outcomes.client = None
    report = rt.outcomes.sync()
    assert report["stale"] == 1 and report["live"] is False
    got = rt.store.latest_leak(leak["event_id"])
    assert got["outcome_state"] == "unresolved"
    assert got["outcome_recovered"] is None, "an unresolved leak must not claim an outcome"


def test_an_unresolved_leak_is_excluded_from_the_training_set(rt):
    _, leak = seed_real_leak(rt, minutes_ago=(STALE_AFTER_DAYS + 2) * 24 * 60)
    rt.outcomes.client = None
    rt.outcomes.sync()
    assert all(r["event_id"] != leak["event_id"] for r in rt.store.resolved_real_leaks())


def test_marking_a_synthetic_leak_is_refused(rt):
    """Synthetic leaks already know both branches; overwriting one would
    silently corrupt the exact comparison."""
    summary = rt.run_and_store("simulator", seed=5, count=20)
    ev = rt.store.leaks_for_batch(summary["batchId"])[0]["event_id"]
    with pytest.raises(ValueError):
        rt.outcomes.mark(ev, recovered=True)


def test_attribution_writes_case_memory_and_an_audit_row(rt):
    _, leak = seed_real_leak(rt, external_kind="payment_link", external_id="plink_cm")
    rt.outcomes.client = FakeRazorpay(links={"plink_cm": {"id": "plink_cm", "status": "paid"}})
    before = rt.store.conn.execute("SELECT COUNT(*) FROM case_memory").fetchone()[0]
    rt.outcomes.sync()
    assert rt.store.conn.execute("SELECT COUNT(*) FROM case_memory").fetchone()[0] == before + 1
    rows = rt.store.audit_tail(kind="outcome.attributed", ref=leak["event_id"])
    assert rows and rows[0]["payload"]["source"] == "poll:payment_link"
    assert rt.store.verify_audit()["ok"] is True


def test_a_sync_with_nothing_pending_is_a_cheap_no_op(rt):
    rt.outcomes.client = FakeRazorpay()
    report = rt.outcomes.sync()
    assert report["checked"] == 0 and not rt.outcomes.client.asked
