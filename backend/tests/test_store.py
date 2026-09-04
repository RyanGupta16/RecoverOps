"""Store: migrations, batch/trace persistence, and the audit chain."""

from __future__ import annotations

import sqlite3

import pytest

from app.store import GENESIS_HASH, SCHEMA_VERSION, Store, canonical


def _fake_batch(batch_id: str, n_events: int = 3) -> dict:
    metrics = {
        "eventsProcessed": n_events,
        "contactsMade": 1,
        "contactBudget": 2,
        "silentRetries": 1,
        "escalations": 1,
        "recoveredCount": 1,
        "recoveredPaise": 49900,
        "recoveryRate": 0.33,
        "sleepingDogsTouched": 0,
        "wastedContacts": 0,
        "outreachDrivenRecoveries": 1,
        "outreachCausedCancellations": 0,
        "outreachCausedChurnLossPaise": 0,
        "churnedSubscriptions": 0,
        "contactCostPaise": 120,
        "netValuePaise": 49780,
    }
    events = [f"evt_{batch_id}_{i}" for i in range(n_events)]
    batch = {
        "source": "live",
        "batchId": batch_id,
        "label": f"test {batch_id}",
        "seed": 7,
        "eventCount": n_events,
        "agents": {"A": {"metrics": dict(metrics)}, "B": {"metrics": dict(metrics)}},
        "sleepingDogs": [{"eventId": events[0]}],
        "exceptions": [],
        "streamScript": [{"kind": "system", "text": "hi", "counters": None}],
        "pipelineStats": {"deterministicLookups": 3, "llmFallbacks": 0, "deterministicShare": 1.0},
    }
    traces = {eid: {"eventId": eid, "agentB": {"chosenAction": "silent_retry"}} for eid in events}
    return {"batch": batch, "traces": traces}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ledger.db")
    yield s
    s.close()


def test_migrations_reach_latest_and_are_idempotent(tmp_path):
    path = tmp_path / "ledger.db"
    s1 = Store(path)
    assert s1.schema_version == SCHEMA_VERSION
    s1.close()
    s2 = Store(path)  # reopening must not re-run migrations or fail on ALTER
    assert s2.schema_version == SCHEMA_VERSION
    s2.close()


def test_legacy_ledger_upgrades_in_place(tmp_path):
    """A ledger.db created by the pre-store code (user_version 0, case_memory
    only) must pass through migration 1 untouched and gain version 2's columns
    without losing its rows."""
    path = tmp_path / "ledger.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        """CREATE TABLE case_memory (
            id INTEGER PRIMARY KEY, reason_code TEXT, method TEXT, amount_band TEXT,
            action TEXT, contacted INTEGER, recovered INTEGER, churned INTEGER, batch_id TEXT)"""
    )
    legacy.execute("CREATE INDEX idx_case_facets ON case_memory (reason_code, method, amount_band)")
    legacy.execute(
        "INSERT INTO case_memory VALUES (1,'INSUFFICIENT_FUNDS','card','lt500','silent_retry',0,1,0,'bat_old')"
    )
    legacy.commit()
    legacy.close()

    s = Store(path)
    assert s.schema_version == SCHEMA_VERSION
    cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(case_memory)")}
    assert {"created_at", "event_id", "kind"} <= cols
    assert s.conn.execute("SELECT COUNT(*) FROM case_memory").fetchone()[0] == 1
    s.close()


def test_batch_roundtrip_and_history_order(store):
    store.save_batch(_fake_batch("bat_a"), created_at="2026-09-05T10:00:00.000+00:00")
    store.save_batch(_fake_batch("bat_b"), created_at="2026-09-05T11:00:00.000+00:00")

    assert store.count_batches() == 2
    assert store.latest_batch_id() == "bat_b"
    assert store.get_batch("bat_a")["batchId"] == "bat_a"
    assert store.get_batch("bat_missing") is None

    history = store.list_batches()
    assert [h["batchId"] for h in history] == ["bat_b", "bat_a"]
    assert history[0]["agents"]["B"]["netValuePaise"] == 49780
    assert history[0]["sleepingDogs"] == 1
    assert history[0]["createdAt"].startswith("2026-09-05T11")


def test_trace_lookup_prefers_latest_batch(store):
    store.save_batch(_fake_batch("bat_a"), created_at="2026-09-05T10:00:00.000+00:00")
    store.save_batch(_fake_batch("bat_b"), created_at="2026-09-05T11:00:00.000+00:00")
    assert store.get_trace("evt_bat_a_0")["eventId"] == "evt_bat_a_0"
    assert store.get_trace("evt_bat_b_2", batch_id="bat_b")["eventId"] == "evt_bat_b_2"
    assert store.get_trace("evt_bat_b_2", batch_id="bat_a") is None
    assert store.get_trace("nope") is None


def test_resaving_a_batch_replaces_its_traces(store):
    store.save_batch(_fake_batch("bat_a", n_events=3))
    store.save_batch(_fake_batch("bat_a", n_events=2))
    n = store.conn.execute("SELECT COUNT(*) FROM traces WHERE batch_id='bat_a'").fetchone()[0]
    assert n == 2
    assert store.count_batches() == 1


def test_audit_chain_verifies_and_detects_tamper(store):
    first = store.append_audit("batch.started", {"seed": 1}, actor="engine")
    assert first["prevHash"] == GENESIS_HASH
    rows = store.append_audit_many(
        [("decision", {"eventId": f"evt_{i}", "action": "silent_retry"}, "agent:B", f"evt_{i}") for i in range(5)]
    )
    assert [r["seq"] for r in rows] == [2, 3, 4, 5, 6]
    # Chain continuity across the two calls.
    assert rows[0]["prevHash"] == first["hash"]
    for a, b in zip(rows, rows[1:]):
        assert b["prevHash"] == a["hash"]

    verify = store.verify_audit()
    assert verify == {"ok": True, "rows": 6, "firstBreak": None, "head": rows[-1]["hash"]}

    # Quietly edit one payload behind the store's back.
    store.conn.execute(
        "UPDATE audit_log SET payload_json = ? WHERE seq = 4",
        (canonical({"eventId": "evt_2", "action": "payment_link_sms"}),),
    )
    broken = store.verify_audit()
    assert broken["ok"] is False
    assert broken["firstBreak"] == 4


def test_audit_tail_filters(store):
    store.append_audit("batch.started", {}, actor="engine")
    store.append_audit_many([("decision", {"i": i}, "agent:B", "evt_x" if i % 2 else "evt_y") for i in range(4)])
    assert len(store.audit_tail(limit=2)) == 2
    assert store.audit_tail(kind="batch.started")[0]["kind"] == "batch.started"
    only_x = store.audit_tail(ref="evt_x")
    assert len(only_x) == 2 and all(r["ref"] == "evt_x" for r in only_x)
    assert store.audit_count() == 5
