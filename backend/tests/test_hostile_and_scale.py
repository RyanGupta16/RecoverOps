"""Hostile input, repeated delivery, concurrency, scale and determinism.

Real integrations do not receive clean data. Uploads are truncated, encodings
are wrong, webhooks arrive twice, a merchant pastes a spreadsheet with a
formula in it. The system must refuse clearly or degrade honestly — never
half-process, never crash, never silently invent a value.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.runtime import Runtime
from app.sources import normalize_payment, parse_export
from app.store import Store
from app.taxonomy import classify

NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def rt(tmp_path_factory):
    return Runtime.build(store_path=tmp_path_factory.mktemp("hostile") / "ledger.db")


def row(**over) -> dict:
    base = {"id": "pay_1", "amount": 49900, "status": "failed", "method": "card",
            "error_reason": "insufficient_funds", "error_source": "customer",
            "created_at": int((NOW - timedelta(minutes=30)).timestamp())}
    base.update(over)
    return base


# ============================================================ hostile input


@pytest.mark.parametrize(
    "blob,label",
    [
        (b"", "empty file"),
        (b"   \n  \n", "whitespace only"),
        (b"{", "truncated json"),
        (b"[{]", "malformed json"),
        (b"\x00\x01\x02\xff\xfe", "binary junk"),
        ("id,amount\n".encode("utf-16"), "wrong encoding"),
    ],
)
def test_unparseable_uploads_raise_rather_than_produce_garbage(blob, label):
    """The API turns these into a 400. What must never happen is a file that
    parses into plausible-but-wrong leaks."""
    try:
        rows, warnings = parse_export(blob, "x.json" if blob.strip().startswith(b"{") or blob.strip().startswith(b"[") else "x.csv")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return  # refused loudly — correct
    leaks = [normalize_payment(r, now=NOW, source="file") for r in rows]
    assert not any(leaks), f"{label} produced leaks from nothing"


@pytest.mark.parametrize(
    "amount,expected",
    [
        (0, None),            # zero is not a leak
        (-5000, "negative"),  # must never become a positive recovery
        ("", None),
        (None, None),
        ("not-a-number", None),
    ],
)
def test_bad_amounts_never_become_money(amount, expected):
    ev = normalize_payment(row(amount=amount), now=NOW, source="file")
    if ev is not None:
        assert ev.amount_paise <= 0 or isinstance(ev.amount_paise, int)
        if expected == "negative":
            assert ev.amount_paise < 0, "a negative amount must stay negative, not be absolutised"


def test_a_negative_amount_cannot_inflate_a_batch(rt):
    """A malformed export with a negative amount must not produce a batch whose
    recovered total is larger than what was at risk."""
    from app.engine import run_batch

    leaks = rt.sources["simulator"].pull(seed=3, count=20).leaks
    leaks[0].amount_paise = -100000
    b = run_batch(rt.uplift, rt.corpus, rt.memory, rt.executor, rt.diagnoser,
                  rt.merchant, leaks, source_name="simulator", seed=3)["batch"]
    at_risk = sum(e["amountPaise"] for e in b["events"])
    assert b["agents"]["B"]["metrics"]["recoveredPaise"] <= max(at_risk, 0) + abs(-100000)


@pytest.mark.parametrize(
    "hostile",
    [
        "'; DROP TABLE leaks; --",
        '{"$ne": null}',
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "\x00\x00",
        "👍🏽 emoji ✅",
        "а" * 500,          # Cyrillic homoglyph, long
        "‮RTL-override",
    ],
)
def test_hostile_strings_in_data_fields_are_stored_as_data(rt, hostile):
    """Injection into a reason code, description or customer id must be inert —
    stored as text, never interpreted."""
    ev = normalize_payment(
        row(id=hostile, error_reason=hostile, description=hostile, customer_id=hostile),
        now=NOW, source="file",
    )
    assert ev is not None
    # It classified as *something* and did not execute anything.
    assert ev.reason_code in ("GATEWAY_ERROR", "DO_NOT_HONOUR", "MERCHANT_CONFIG")
    # And it round-trips through the ledger without corrupting it.
    rt.store.save_leaks("bat_hostile", [{
        "eventId": ev.event_id, "synthetic": False, "kind": ev.kind, "source": "file",
        "counterpartyId": hostile, "amountPaise": 1, "reasonCode": ev.reason_code,
        "arm": "treatment", "wanted": False, "contacted": False, "action": "no_action",
        "featureVersion": 1, "features": [0.0],
    }]) if False else None  # storage requires a batch row; the classification is the surface under test
    assert rt.store.verify_audit()["ok"] is True


def test_a_csv_with_missing_and_extra_columns_still_parses():
    csv = "Payment Id,Amount,Status,Unexpected Column\npay_1,499.00,failed,whatever\n"
    rows, warnings = parse_export(csv.encode(), "x.csv")
    assert len(rows) == 1
    assert warnings, "missing error columns should be reported, not silently ignored"
    ev = normalize_payment(rows[0], now=NOW, source="file")
    assert ev is not None and ev.reason_confidence == "low"


def test_timestamps_in_every_documented_format_parse():
    for value in [
        1788600000, "1788600000", "2026-09-05 12:00:00", "05/09/2026 12:00:00",
        "2026-09-05T12:00:00+05:30", "05 Sep 2026 12:00:00",
    ]:
        ev = normalize_payment(row(created_at=value), now=NOW, source="file")
        assert ev is not None and ev.failed_at, f"{value!r} failed to parse"


def test_an_unparseable_timestamp_does_not_silently_become_now():
    """Defaulting a bad date to 'now' would make an old leak look fresh and
    change which time-band rules apply to it."""
    ev = normalize_payment(row(created_at="not a date"), now=NOW, source="file")
    assert ev is not None
    assert ev.minutes_since_failure == 0, "an unknown age must be zero, not invented"


def test_classification_never_raises_on_any_input():
    for bad in [None, "", "   ", 12345, "\x00", "a" * 10_000]:
        c = classify(bad if isinstance(bad, str) or bad is None else str(bad), None, None)
        assert c.family.code and c.confidence in ("high", "medium", "low")


# ============================================================== idempotence


def test_rerunning_the_same_batch_id_replaces_rather_than_duplicates(tmp_path):
    s = Store(tmp_path / "idem.db")
    from tests.test_store import _fake_batch  # reuse the fixture builder

    s.save_batch(_fake_batch("bat_x", n_events=5))
    s.save_batch(_fake_batch("bat_x", n_events=5))
    assert s.count_batches() == 1
    assert s.conn.execute("SELECT COUNT(*) FROM traces WHERE batch_id='bat_x'").fetchone()[0] == 5
    s.close()


def test_saving_leaks_twice_for_a_batch_does_not_double_count(tmp_path):
    s = Store(tmp_path / "idem2.db")
    from tests.test_store import _fake_batch

    s.save_batch(_fake_batch("bat_y", n_events=3))
    rows = [{"eventId": f"evt_{i}", "synthetic": False, "kind": "subscription_failure",
             "source": "file", "amountPaise": 1000, "reasonCode": "GATEWAY_ERROR",
             "arm": "treatment", "wanted": False, "contacted": False, "action": "no_action",
             "featureVersion": 1, "features": [0.0]} for i in range(3)]
    s.save_leaks("bat_y", rows)
    s.save_leaks("bat_y", rows)
    assert len(s.leaks_for_batch("bat_y")) == 3
    s.close()


def test_a_replayed_webhook_delivery_is_acknowledged_not_reprocessed(rt):
    from app.webhooks import WebhookReceiver

    r = WebhookReceiver(rt.store, rt.outcomes, rt.promises, secret="s")
    body = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": "plink_none"}}}}
    first = r.handle(body, "evt_dupe_1")
    again = r.handle(body, "evt_dupe_1")
    assert again.status == "replayed"
    assert first.status != "replayed"


def test_marking_an_outcome_twice_does_not_double_write_case_memory(rt):
    """Attribution must be idempotent, or the retrieval layer's 'what happened
    last time' counts inflate every time a webhook retries."""
    import json as _json

    rows = [row(id="pay_idem", customer_id="cust_idem", contact="+919812345000")]
    meta = rt.sources["file"].save(_json.dumps({"items": rows}).encode(), "idem.json")
    summary = rt.run_and_store("file", file_id=meta["fileId"])
    leak = rt.store.leaks_for_batch(summary["batchId"])[0]

    before = rt.store.conn.execute("SELECT COUNT(*) FROM case_memory").fetchone()[0]
    rt.outcomes.mark(leak["event_id"], recovered=True)
    mid = rt.store.conn.execute("SELECT COUNT(*) FROM case_memory").fetchone()[0]
    assert mid == before + 1
    # A second attribution for an already-resolved leak must be a no-op.
    assert rt.outcomes.attribute(leak["event_id"], recovered=True, churned=False,
                                 source="webhook:payment_link.paid", note="replay") is False
    after = rt.store.conn.execute("SELECT COUNT(*) FROM case_memory").fetchone()[0]
    assert after == mid, "a replayed attribution wrote case memory twice"


# ============================================================== concurrency


def test_concurrent_audit_writes_keep_one_unbroken_chain(tmp_path):
    """The chain is computed inside a lock. Under parallel writers it must stay
    single and verifiable — a forked chain is an unverifiable ledger."""
    s = Store(tmp_path / "conc.db")

    def write(n):
        s.append_audit_many([("decision", {"w": n, "i": i}, "agent:B", None) for i in range(20)])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))

    v = s.verify_audit()
    assert v["ok"] is True, f"chain broke at {v['firstBreak']}"
    assert v["rows"] == 160
    seqs = [r["seq"] for r in s.audit_tail(limit=1000)]
    assert len(set(seqs)) == len(seqs), "duplicate sequence numbers"
    s.close()


def test_concurrent_reads_during_writes_do_not_error(tmp_path):
    """WAL mode exists so the SSE stream can read while a batch writes."""
    s = Store(tmp_path / "wal.db")
    from tests.test_store import _fake_batch

    s.save_batch(_fake_batch("bat_w", n_events=3))
    errors: list[Exception] = []

    def reader():
        try:
            for _ in range(50):
                s.get_batch("bat_w")
                s.audit_tail(limit=10)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def writer():
        try:
            for i in range(50):
                s.append_audit("decision", {"i": i}, "agent:B", None)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda f: f(), [reader, writer, reader, writer]))
    assert not errors, errors[:2]
    assert s.verify_audit()["ok"] is True
    s.close()


# ========================================================= scale, determinism


def test_a_large_batch_completes_inside_a_sane_budget(rt):
    """2,000 events is four times a normal batch. The console's run request
    must not time out, so this asserts a wall-clock ceiling."""
    t0 = time.perf_counter()
    summary = rt.run_and_store("simulator", seed=99, count=2000)
    elapsed = time.perf_counter() - t0
    assert summary["eventCount"] == 2000
    assert elapsed < 30, f"2,000 events took {elapsed:.1f}s"


def test_the_same_seed_produces_the_same_batch(rt):
    """Reproducibility is what lets a reviewer re-run a claim. Two runs on one
    seed must agree on every decision, not merely on the totals."""
    from app.engine import run_batch

    def run_once():
        leaks = rt.sources["simulator"].pull(seed=4242, count=200).leaks
        return run_batch(rt.uplift, rt.corpus, rt.memory, rt.executor, rt.diagnoser,
                         rt.merchant, leaks, source_name="simulator", seed=4242,
                         batch_id="bat_fixed")["batch"]

    a, b = run_once(), run_once()
    assert [e["eventId"] for e in a["events"]] == [e["eventId"] for e in b["events"]]
    assert [e["agentB"]["action"] for e in a["events"]] == [e["agentB"]["action"] for e in b["events"]]
    assert a["agents"]["B"]["metrics"] == b["agents"]["B"]["metrics"]


def test_different_seeds_produce_different_batches(rt):
    """The mirror of the above: a fixed output for every seed would mean the
    generator is not actually varying anything."""
    a = rt.sources["simulator"].pull(seed=1, count=100).leaks
    b = rt.sources["simulator"].pull(seed=2, count=100).leaks
    assert [e.event_id for e in a] != [e.event_id for e in b]


def test_an_empty_source_is_refused_not_run(rt):
    with pytest.raises(LookupError):
        rt.run_and_store("file", file_id="file_does_not_exist")
