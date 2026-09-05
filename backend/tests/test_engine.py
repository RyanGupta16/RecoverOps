"""End-to-end: the engine on synthetic and on real leaks, through the Runtime.

Uses the trained models (cached after the first boot) and a throwaway store.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.engine import run_batch
from app.runtime import Runtime
from app.sources import normalize_payment


@pytest.fixture(scope="module")
def rt(tmp_path_factory):
    return Runtime.build(store_path=tmp_path_factory.mktemp("ledger") / "ledger.db")


def test_synthetic_batch_has_exact_outcomes_and_three_class_messages(rt):
    pulled = rt.sources["simulator"].pull(seed=5, count=80)
    result = run_batch(rt.uplift, rt.corpus, rt.memory, rt.executor, rt.diagnoser, rt.merchant, pulled.leaks, source_name="simulator", seed=5)
    batch = result["batch"]
    assert batch["dataMode"] == "synthetic" and batch["estimatorMode"] == "learned"
    assert batch["eventCount"] == 80 and len(result["traces"]) == 80
    assert batch["agents"]["B"]["metrics"]["outcomesPending"] == 0
    assert batch["agents"]["B"]["curve"] and batch["agents"]["B"]["segments"]
    classes = {row["messageClass"] for row in batch["events"] if row["agentB"]["contacted"]}
    # Recurring charges are merchant-initiated: contacted rows are service (or promotional for incentives), never transactional.
    assert "transactional" not in classes
    assert classes <= {"service", "promotional"}
    for t in result["traces"].values():
        assert t["truth"] is not None and t["agentB"]["outcome"] is not None
        assert all("citation" in g for g in t["agentB"]["gate"])


def test_real_batch_reports_pending_outcomes_and_priors(rt):
    now = datetime.now(timezone.utc)
    rows = []
    for i, (reason, source, method) in enumerate(
        [
            ("insufficient_funds", "customer", "card"),
            ("card_expired", "customer", "card"),
            ("payment_method_not_enabled", "business", "upi"),
            ("debit_instrument_blocked", "customer", "card"),
            ("bank_not_available", "gateway", "netbanking"),
            ("payment_cancelled", "customer", "upi"),
        ]
        * 3
    ):
        rows.append(
            {
                "id": f"pay_{i}",
                "amount": 49900 + i * 100,
                "status": "failed",
                "method": method,
                "card": {"network": "Visa", "issuer": "HDFC"} if method == "card" else None,
                "token_id": "tok" if method == "card" else None,
                "customer_id": f"cust_{i % 4}",
                "contact": f"+9199990000{i:02d}",
                "error_reason": reason,
                "error_source": source,
                "created_at": int((now - timedelta(minutes=10 + i * 7)).timestamp()),
            }
        )
    leaks = [ev for ev in (normalize_payment(r, now=now, source="file") for r in rows) if ev]
    result = run_batch(rt.uplift, rt.corpus, rt.memory, rt.executor, rt.diagnoser, rt.merchant, leaks, source_name="file", seed=1)
    batch = result["batch"]
    assert batch["dataMode"] == "real" and batch["estimatorMode"] == "priors"
    m = batch["agents"]["B"]["metrics"]
    assert m["outcomesPending"] == len(leaks) and m["recoveredPaise"] == 0
    assert batch["agents"]["B"]["curve"] == [] and batch["agents"]["B"]["segments"] == []
    for row in batch["events"]:
        assert row["truthSegment"] is None and row["agentB"]["recovered"] is None
    trace = next(iter(result["traces"].values()))
    assert trace["truth"] is None and trace["agentB"]["outcome"] is None
    assert trace["leak"]["featuresAreProxies"] is True
    # Raw contact details never leave the process; only the hash is in the trace.
    assert "contact" not in trace["leak"] and "email" not in trace["leak"]
    assert trace["leak"]["contactHash"] and "+91" not in json.dumps(trace)
    # Merchant-side and blocked-instrument leaks are handled deterministically.
    by_id = {row["eventId"]: row for row in batch["events"]}
    for ev in leaks:
        row = by_id[ev.event_id]
        if ev.merchant_side:
            assert row["agentB"]["contacted"] is False
        if ev.reason_code == "INSTRUMENT_BLOCKED":
            assert row["agentB"]["action"] != "silent_retry"


def test_mixed_batches_are_refused(rt):
    sim = rt.sources["simulator"].pull(seed=2, count=5).leaks
    real = normalize_payment({"id": "p", "amount": 100, "status": "failed", "method": "card", "error_reason": "insufficient_funds", "created_at": 1_700_000_000}, now=datetime.now(timezone.utc), source="file")
    with pytest.raises(ValueError):
        run_batch(rt.uplift, rt.corpus, rt.memory, rt.executor, rt.diagnoser, rt.merchant, sim + [real], source_name="mixed")


def test_run_and_store_persists_and_audits(rt):
    summary = rt.run_and_store("simulator", seed=9, count=40)
    assert summary["eventCount"] == 40 and summary["dataMode"] == "synthetic" and summary["sourceName"] == "simulator"
    assert rt.store.verify_audit()["ok"]
    assert rt.store.get_trace(rt.store.get_batch(summary["batchId"])["events"][0]["eventId"]) is not None
    with pytest.raises(LookupError):
        rt.run_and_store("file", file_id="file_missing")
