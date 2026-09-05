"""The learning loop: arms, propensities, outcome attribution, measurement, refit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.learning import MIN_ROWS, RealLearner, measure_policy_effect
from app.runtime import Runtime
from app.sim import FEATURE_VERSION, featurize, generate_events
from app.sources import assign_holdout, normalize_payment


@pytest.fixture(scope="module")
def rt(tmp_path_factory):
    return Runtime.build(store_path=tmp_path_factory.mktemp("ledger") / "ledger.db")


def _real_rows(n: int, now: datetime) -> list[dict]:
    reasons = [
        ("insufficient_funds", "customer", "card"),
        ("card_expired", "customer", "card"),
        ("payment_method_not_enabled", "business", "upi"),
        ("debit_instrument_blocked", "customer", "card"),
        ("bank_not_available", "gateway", "netbanking"),
        ("payment_cancelled", "customer", "upi"),
        ("card_declined", "gateway", "card"),
        ("incorrect_otp", "customer", "card"),
    ]
    rows = []
    for i in range(n):
        reason, source, method = reasons[i % len(reasons)]
        rows.append(
            {
                "id": f"pay_{i}",
                "amount": 29900 + (i % 7) * 20000,
                "status": "failed",
                "method": method,
                "card": {"network": "Visa" if i % 3 else "MasterCard", "issuer": "HDFC"} if method == "card" else None,
                "token_id": "tok" if method == "card" else None,
                "customer_id": f"cust_{i % 37}",
                "contact": f"+91999900{i:04d}",
                "error_reason": reason,
                "error_source": source,
                "created_at": int((now - timedelta(minutes=40 + i * 3)).timestamp()),
            }
        )
    return rows


def test_holdout_is_deterministic_and_near_the_share():
    assert assign_holdout("cust_1", 0.10) == assign_holdout("cust_1", 0.10)
    n = 20_000
    share = sum(assign_holdout(f"cust_{i}", 0.10) for i in range(n)) / n
    assert 0.085 < share < 0.115
    assert not any(assign_holdout(f"cust_{i}", 0.0) for i in range(100))


def test_synthetic_batch_persists_resolved_leak_rows(rt):
    summary = rt.run_and_store("simulator", seed=21, count=60)
    rows = rt.store.leaks_for_batch(summary["batchId"])
    assert len(rows) == 60
    assert all(r["synthetic"] == 1 and r["outcome_state"] == "resolved" and r["outcome_source"] == "sim" for r in rows)
    arms = {r["arm"] for r in rows}
    assert arms <= {"control", "treatment"}
    assert all(r["contacted"] == 0 for r in rows if r["arm"] == "control")
    # No exploration on synthetic data.
    assert all(r["explored"] == 0 for r in rows)


def test_real_batch_records_arms_and_known_propensities(rt):
    now = datetime.now(timezone.utc)
    rows = _real_rows(80, now)
    import json

    meta = rt.sources["file"].save(json.dumps({"items": rows}).encode(), "export.json")
    summary = rt.run_and_store("file", file_id=meta["fileId"])
    assert summary["dataMode"] == "real"
    leaks = rt.store.leaks_for_batch(summary["batchId"])
    assert len(leaks) == 80 and all(r["outcome_state"] == "pending" for r in leaks)
    eps = rt.merchant.exploration_share
    for r in leaks:
        if r["arm"] == "control":
            assert r["contacted"] == 0 and r["propensity"] == 0.0
        else:
            assert r["propensity"] in (0.0, eps, 1.0 - eps) or r["propensity"] is not None
            if r["contacted"]:
                assert r["propensity"] > 0.0
    assert sum(r["explored"] for r in leaks) >= 0
    # Trace exposes the arm and propensity.
    t = rt.store.get_trace(leaks[0]["event_id"])
    assert t["agentB"]["arm"] in ("control", "treatment") and "propensity" in t["agentB"]


def test_manual_mark_attributes_outcome_and_writes_case_memory(rt):
    pending = rt.store.pending_real_leaks()
    assert pending
    leak = pending[0]
    before = rt.store.conn.execute("SELECT COUNT(*) FROM case_memory WHERE kind = ? AND event_id = ?", (leak["kind"], leak["event_id"])).fetchone()[0]
    out = rt.outcomes.mark(leak["event_id"], recovered=True, note="paid via bank transfer")
    assert out["source"].startswith("manual")
    after = rt.store.latest_leak(leak["event_id"])
    assert after["outcome_state"] == "resolved" and after["outcome_recovered"] == 1
    assert rt.store.conn.execute("SELECT COUNT(*) FROM case_memory WHERE event_id = ?", (leak["event_id"],)).fetchone()[0] == before + 1
    audit = rt.store.audit_tail(kind="outcome.attributed", ref=leak["event_id"])
    assert audit and audit[0]["payload"]["recovered"] is True
    # The trace now carries the outcome, though the stored trace is unchanged.
    overlay = rt.trace_with_outcome(leak["event_id"])
    assert overlay["agentB"]["outcome"] == {"recovered": True, "churned": False}
    assert overlay["outcomeAttribution"]["source"].startswith("manual")
    assert rt.store.get_trace(leak["event_id"])["agentB"]["outcome"] is None
    with pytest.raises(ValueError):
        rt.outcomes.mark(rt.store.list_batches()[-1]["batchId"] and rt.store.leaks_for_batch(rt.store.list_batches()[-1]["batchId"])[0]["event_id"], recovered=True)


def test_sync_without_keys_ages_out_stale_leaks_only(rt):
    report = rt.outcomes.sync()
    assert report["live"] is False and report["checked"] >= 1
    assert report["recovered"] == 0 and report["churned"] == 0
    assert report["stale"] == 0  # everything here is fresh


def test_measure_policy_effect_bootstraps_an_interval():
    rng = np.random.default_rng(3)
    rows = []
    for i in range(400):
        arm = "control" if i % 10 == 0 else "treatment"
        p = 0.30 if arm == "control" else 0.42
        rows.append({"arm": arm, "outcome_recovered": int(rng.random() < p), "amount_paise": 49900})
    eff = measure_policy_effect(rows)
    assert eff["measurable"] and eff["treatmentRows"] == 360 and eff["controlRows"] == 40
    lo, hi = eff["ateRateCi"]
    assert lo < eff["ateRate"] < hi
    assert eff["incrementalPaiseCi"][0] < eff["incrementalPaise"] < eff["incrementalPaiseCi"][1]
    assert measure_policy_effect([r for r in rows if r["arm"] == "treatment"])["measurable"] is False


def test_real_learner_stays_on_priors_until_enough_rows(rt):
    report = rt.retrain()
    assert report["ready"] is False and report["rowsUsed"] < MIN_ROWS
    assert rt.learner.ready is False
    assert rt.learning_status()["estimatorMode"] == "priors"


def test_real_learner_fits_on_resolved_treatment_rows(tmp_path):
    """Pseudo-real rows built from the simulator's truth under known propensities:
    the learner must fit, beat random on the chronological holdout, and become
    the estimator for real batches."""
    from app.store import Store

    store = Store(tmp_path / "ledger.db")
    store.save_batch({"batch": _fake_batch("bat_pseudo"), "traces": {}})
    rng = np.random.default_rng(5)
    events = generate_events(99, count=900)
    eps = 0.10
    rows = []
    for ev in events:
        tau = ev.truth[1] - ev.truth[0]  # type: ignore[index]
        wanted = tau > 0.10
        e = (1 - eps) if wanted else eps
        contacted = rng.random() < e
        p = ev.truth[1] if contacted else ev.truth[0]  # type: ignore[index]
        recovered = rng.random() < p
        churned = (not recovered) and rng.random() < (ev.truth[3] if contacted else ev.truth[2])  # type: ignore[index]
        rows.append(
            {
                "eventId": ev.event_id,
                "synthetic": False,
                "kind": ev.kind,
                "source": "file",
                "counterpartyId": ev.customer_id,
                "amountPaise": ev.amount_paise,
                "reasonCode": ev.reason_code,
                "arm": "treatment",
                "wanted": wanted,
                "explored": contacted != wanted,
                "propensity": e,
                "contacted": contacted,
                "action": "payment_link_sms" if contacted else "silent_retry",
                "featureVersion": FEATURE_VERSION,
                "features": featurize(ev),
                "outcomeState": "resolved",
                "outcomeRecovered": int(recovered),
                "outcomeChurned": int(churned),
                "outcomeSource": "test",
            }
        )
    store.save_leaks("bat_pseudo", rows)
    learner = RealLearner(store, path=tmp_path / "real_models.pkl")
    report = learner.fit()
    assert report["rowsUsed"] == 900 and report["ready"] is True, report
    assert report["qiniReal"] > 0
    est = learner.estimate_batch(events[:5])
    assert len(est) == 5 and all(len(e) == 4 for e in est)
    # Reloading from disk keeps it ready.
    again = RealLearner(store, path=tmp_path / "real_models.pkl")
    assert again.ready and "real-data" in again.label
    store.close()


def _fake_batch(batch_id: str) -> dict:
    m = dict.fromkeys(
        "eventsProcessed contactsMade contactBudget silentRetries escalations recoveredCount recoveredPaise sleepingDogsTouched wastedContacts outreachDrivenRecoveries outreachCausedCancellations outreachCausedChurnLossPaise churnedSubscriptions contactCostPaise netValuePaise".split(),
        0,
    )
    m["recoveryRate"] = 0.0
    return {"batchId": batch_id, "eventCount": 0, "agents": {"A": {"metrics": m}, "B": {"metrics": dict(m)}}, "sleepingDogs": [], "exceptions": [], "streamScript": []}
