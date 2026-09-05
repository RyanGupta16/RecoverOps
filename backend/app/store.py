"""Durable storage: batches, traces, case memory and the audit chain, one SQLite file.

Before this module existed, batches lived in a dict capped at five and one
``latest.json`` overwritten on every run — a restart lost every trace link but
the last, and nothing could show a trend across runs. The track's word is
*audit trail*; a trail that vanishes on restart is not one.

Design:

- One long-lived connection in WAL mode behind an RLock. WAL lets the SSE
  stream read a batch while the next one is being written; a single connection
  avoids paying ``sqlite3.connect`` a thousand times per batch.
- Migrations keyed on ``PRAGMA user_version`` so the schema can grow without a
  hand-edited ledger. Each migration runs once, in order, inside a transaction.
- The audit log is append-only and hash-chained: every row carries the SHA-256
  of the previous row's hash plus its own canonical body. ``verify_audit``
  walks the chain and reports the first break. It costs nothing to implement
  and it is the difference between "we log things" and "the log cannot be
  quietly edited".
- Whole batch JSON is stored per row. A batch is ~2.5 MB with its stream
  script; SQLite reads that in milliseconds and it keeps the read path one
  query. Summaries are stored alongside so the history list never has to parse
  a batch.

Nothing above this module knows it is SQLite. That is the Postgres seam.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_PATH = DATA_DIR / "ledger.db"

GENESIS_HASH = "0" * 64


def canonical(obj) -> str:
    """Deterministic JSON: sorted keys, no whitespace, unicode intact."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _audit_body(kind: str, actor: str, ref: str | None, at: str, payload: dict) -> str:
    return canonical({"kind": kind, "actor": actor, "ref": ref, "at": at, "payload": payload})


def _chain_hash(prev_hash: str, body: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{body}".encode("utf-8")).hexdigest()


# Each entry: (version, [statements]). Version 1 is the ledger exactly as it
# existed before this module, so a pre-existing ledger.db at user_version 0
# passes through it as a no-op and picks up version 2's additions.
MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            """CREATE TABLE IF NOT EXISTS case_memory (
                id INTEGER PRIMARY KEY,
                reason_code TEXT, method TEXT, amount_band TEXT,
                action TEXT, contacted INTEGER,
                recovered INTEGER, churned INTEGER,
                batch_id TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_case_facets ON case_memory (reason_code, method, amount_band)",
        ],
    ),
    (
        2,
        [
            """CREATE TABLE batches (
                batch_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                seed INTEGER,
                source TEXT NOT NULL,
                label TEXT,
                event_count INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                batch_json TEXT NOT NULL
            )""",
            "CREATE INDEX idx_batches_created ON batches (created_at DESC)",
            """CREATE TABLE traces (
                event_id TEXT NOT NULL,
                batch_id TEXT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
                trace_json TEXT NOT NULL,
                PRIMARY KEY (event_id, batch_id)
            )""",
            "CREATE INDEX idx_traces_event ON traces (event_id)",
            """CREATE TABLE audit_log (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                actor TEXT NOT NULL,
                kind TEXT NOT NULL,
                ref TEXT,
                payload_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            )""",
            "CREATE INDEX idx_audit_kind ON audit_log (kind)",
            "CREATE INDEX idx_audit_ref ON audit_log (ref)",
            # Case memory becomes joinable to the event that produced it, and
            # windowable in time. New columns are nullable so legacy rows stand.
            "ALTER TABLE case_memory ADD COLUMN created_at TEXT",
            "ALTER TABLE case_memory ADD COLUMN event_id TEXT",
            "ALTER TABLE case_memory ADD COLUMN kind TEXT",
            "CREATE INDEX IF NOT EXISTS idx_case_created ON case_memory (created_at)",
        ],
    ),
    (
        3,
        [
            # One row per leak per batch: what the pipeline saw, which arm the
            # counterparty was in, what B did and with what propensity, and the
            # outcome once it is known. This is the learning loop's training
            # set and the measurement's ledger.
            """CREATE TABLE leaks (
                batch_id TEXT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
                event_id TEXT NOT NULL,
                synthetic INTEGER NOT NULL,
                kind TEXT NOT NULL,
                source TEXT NOT NULL,
                counterparty_id TEXT,
                contact_hash TEXT,
                amount_paise INTEGER NOT NULL,
                method TEXT,
                network TEXT,
                reason_code TEXT NOT NULL,
                raw_reason TEXT,
                failed_at TEXT,
                arm TEXT NOT NULL,
                wanted INTEGER NOT NULL,
                explored INTEGER NOT NULL DEFAULT 0,
                propensity REAL,
                contacted INTEGER NOT NULL,
                action TEXT NOT NULL,
                message_class TEXT,
                payment_id TEXT,
                subscription_id TEXT,
                invoice_id TEXT,
                order_id TEXT,
                external_kind TEXT,
                external_id TEXT,
                feature_version INTEGER NOT NULL,
                features_json TEXT NOT NULL,
                outcome_state TEXT NOT NULL DEFAULT 'pending',
                outcome_recovered INTEGER,
                outcome_churned INTEGER,
                outcome_source TEXT,
                outcome_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (batch_id, event_id)
            )""",
            "CREATE INDEX idx_leaks_event ON leaks (event_id)",
            "CREATE INDEX idx_leaks_outcome ON leaks (synthetic, outcome_state)",
            "CREATE INDEX idx_leaks_counterparty ON leaks (counterparty_id)",
            """CREATE TABLE learning_runs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                rows_used INTEGER NOT NULL,
                treated_rows INTEGER NOT NULL,
                control_rows INTEGER NOT NULL,
                estimator TEXT NOT NULL,
                feature_version INTEGER NOT NULL,
                report_json TEXT NOT NULL
            )""",
        ],
    ),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = str(path or DEFAULT_PATH)
        self.lock = threading.RLock()
        # isolation_level=None puts the connection in autocommit; transactions
        # are explicit via transaction(), so a failed write never leaves an
        # open transaction holding the WAL.
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # ------------------------------------------------------------------ infra

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        with self.lock:
            current = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                with self.transaction() as c:
                    for sql in statements:
                        c.execute(sql)
                    c.execute(f"PRAGMA user_version = {version}")

    @property
    def schema_version(self) -> int:
        with self.lock:
            return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # ---------------------------------------------------------------- batches

    @staticmethod
    def summarize(batch: dict) -> dict:
        """The slim row the history list renders. camelCase to match src/lib/types.ts."""

        def agent(key: str) -> dict:
            m = batch["agents"][key]["metrics"]
            return {
                "contactsMade": m["contactsMade"],
                "recoveredPaise": m["recoveredPaise"],
                "netValuePaise": m["netValuePaise"],
                "sleepingDogsTouched": m["sleepingDogsTouched"],
                "wastedContacts": m["wastedContacts"],
                "escalations": m["escalations"],
                "recoveryRate": m["recoveryRate"],
                "outcomesPending": m.get("outcomesPending", 0),
            }

        return {
            "batchId": batch["batchId"],
            "label": batch.get("label"),
            "source": batch.get("source", "live"),
            "seed": batch.get("seed"),
            "eventCount": batch["eventCount"],
            "generatedBy": batch.get("generatedBy"),
            "dataMode": batch.get("dataMode", "synthetic"),
            "sourceName": batch.get("sourceName", "simulator"),
            "agents": {"A": agent("A"), "B": agent("B")},
            "sleepingDogs": len(batch.get("sleepingDogs", [])),
            "exceptions": len(batch.get("exceptions", [])),
            "pipelineStats": batch.get("pipelineStats"),
        }

    def save_batch(self, result: dict, created_at: str | None = None) -> str:
        batch = result["batch"]
        traces: dict[str, dict] = result.get("traces", {})
        batch_id = batch["batchId"]
        summary = self.summarize(batch)
        created = created_at or now_iso()
        summary["createdAt"] = created
        with self.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO batches
                   (batch_id, created_at, seed, source, label, event_count, summary_json, batch_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    batch_id,
                    created,
                    batch.get("seed"),
                    batch.get("source", "live"),
                    batch.get("label"),
                    batch["eventCount"],
                    canonical(summary),
                    json.dumps(batch, ensure_ascii=False),
                ),
            )
            c.execute("DELETE FROM traces WHERE batch_id = ?", (batch_id,))
            c.executemany(
                "INSERT INTO traces (event_id, batch_id, trace_json) VALUES (?,?,?)",
                ((eid, batch_id, json.dumps(t, ensure_ascii=False)) for eid, t in traces.items()),
            )
        return batch_id

    def get_batch(self, batch_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT batch_json FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return json.loads(row["batch_json"]) if row else None

    def latest_batch_id(self) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT batch_id FROM batches ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        return row["batch_id"] if row else None

    def count_batches(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0])

    def list_batches(self, limit: int = 25) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT summary_json FROM batches ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r["summary_json"]) for r in rows]

    def get_trace(self, event_id: str, batch_id: str | None = None) -> dict | None:
        with self.lock:
            if batch_id:
                row = self.conn.execute(
                    "SELECT trace_json FROM traces WHERE event_id = ? AND batch_id = ?", (event_id, batch_id)
                ).fetchone()
            else:
                # Most recent batch containing this event wins — event ids are
                # unique per batch in practice, but a replayed seed could repeat one.
                row = self.conn.execute(
                    """SELECT t.trace_json FROM traces t JOIN batches b ON b.batch_id = t.batch_id
                       WHERE t.event_id = ? ORDER BY b.created_at DESC, b.rowid DESC LIMIT 1""",
                    (event_id,),
                ).fetchone()
        return json.loads(row["trace_json"]) if row else None

    # ------------------------------------------------------------------ leaks

    def save_leaks(self, batch_id: str, rows: Iterable[dict]) -> int:
        """One row per leak for this batch. Replaces rows for the same batch id."""
        created = now_iso()
        n = 0
        with self.transaction() as c:
            c.execute("DELETE FROM leaks WHERE batch_id = ?", (batch_id,))
            for r in rows:
                c.execute(
                    """INSERT INTO leaks (
                        batch_id, event_id, synthetic, kind, source, counterparty_id, contact_hash, amount_paise,
                        method, network, reason_code, raw_reason, failed_at, arm, wanted, explored, propensity,
                        contacted, action, message_class, payment_id, subscription_id, invoice_id, order_id,
                        external_kind, external_id, feature_version, features_json,
                        outcome_state, outcome_recovered, outcome_churned, outcome_source, outcome_at, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        batch_id, r["eventId"], int(r["synthetic"]), r["kind"], r["source"], r.get("counterpartyId"),
                        r.get("contactHash"), r["amountPaise"], r.get("method"), r.get("network"), r["reasonCode"],
                        r.get("rawReason"), r.get("failedAt"), r["arm"], int(r["wanted"]), int(r.get("explored", False)),
                        r.get("propensity"), int(r["contacted"]), r["action"], r.get("messageClass"), r.get("paymentId"),
                        r.get("subscriptionId"), r.get("invoiceId"), r.get("orderId"), r.get("externalKind"),
                        r.get("externalId"), r["featureVersion"], canonical(r["features"]),
                        r.get("outcomeState", "pending"), r.get("outcomeRecovered"), r.get("outcomeChurned"),
                        r.get("outcomeSource"), r.get("outcomeAt"), created,
                    ),
                )
                n += 1
        return n

    @staticmethod
    def _leak_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["features"] = json.loads(d.pop("features_json"))
        return d

    def leaks_for_batch(self, batch_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM leaks WHERE batch_id = ? ORDER BY rowid", (batch_id,)).fetchall()
        return [self._leak_row(r) for r in rows]

    def latest_leak(self, event_id: str) -> dict | None:
        with self.lock:
            r = self.conn.execute(
                """SELECT l.* FROM leaks l JOIN batches b ON b.batch_id = l.batch_id
                   WHERE l.event_id = ? ORDER BY b.created_at DESC, b.rowid DESC LIMIT 1""",
                (event_id,),
            ).fetchone()
        return self._leak_row(r) if r else None

    def pending_real_leaks(self, max_age_days: int = 30, limit: int = 2000) -> list[dict]:
        """Real leaks whose outcome is not yet known, newest first."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT * FROM leaks WHERE synthetic = 0 AND outcome_state = 'pending'
                   AND created_at >= datetime('now', ?) ORDER BY created_at DESC LIMIT ?""",
                (f"-{int(max_age_days)} days", limit),
            ).fetchall()
        return [self._leak_row(r) for r in rows]

    def set_outcome(self, batch_id: str, event_id: str, *, recovered: bool | None, churned: bool | None, source: str, state: str = "resolved") -> bool:
        with self.transaction() as c:
            cur = c.execute(
                """UPDATE leaks SET outcome_state = ?, outcome_recovered = ?, outcome_churned = ?, outcome_source = ?, outcome_at = ?
                   WHERE batch_id = ? AND event_id = ?""",
                (state, None if recovered is None else int(recovered), None if churned is None else int(churned), source, now_iso(), batch_id, event_id),
            )
        return cur.rowcount > 0

    def resolved_real_leaks(self) -> list[dict]:
        """Real leaks with a known outcome — the learning loop's training set.
        One row per event (the latest batch that saw it)."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT l.* FROM leaks l JOIN batches b ON b.batch_id = l.batch_id
                   WHERE l.synthetic = 0 AND l.outcome_state = 'resolved'
                   AND b.created_at = (
                       SELECT MAX(b2.created_at) FROM leaks l2 JOIN batches b2 ON b2.batch_id = l2.batch_id
                       WHERE l2.event_id = l.event_id
                   )
                   ORDER BY l.created_at"""
            ).fetchall()
        return [self._leak_row(r) for r in rows]

    def leak_counts(self) -> dict:
        with self.lock:
            row = self.conn.execute(
                """SELECT
                     SUM(CASE WHEN synthetic = 0 THEN 1 ELSE 0 END),
                     SUM(CASE WHEN synthetic = 0 AND outcome_state = 'pending' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN synthetic = 0 AND outcome_state = 'resolved' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN synthetic = 0 AND arm = 'control' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN synthetic = 0 AND explored = 1 THEN 1 ELSE 0 END),
                     SUM(CASE WHEN synthetic = 1 THEN 1 ELSE 0 END)
                   FROM leaks"""
            ).fetchone()
        real, pending, resolved, control, explored, synthetic = (int(x or 0) for x in row)
        return {"real": real, "pending": pending, "resolved": resolved, "control": control, "explored": explored, "synthetic": synthetic}

    # -------------------------------------------------------------- learning

    def save_learning_run(self, report: dict) -> int:
        with self.transaction() as c:
            cur = c.execute(
                "INSERT INTO learning_runs (at, rows_used, treated_rows, control_rows, estimator, feature_version, report_json) VALUES (?,?,?,?,?,?,?)",
                (now_iso(), report["rowsUsed"], report["treatedRows"], report["controlRows"], report["estimator"], report["featureVersion"], canonical(report)),
            )
        return int(cur.lastrowid)

    def latest_learning_run(self) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT at, report_json FROM learning_runs ORDER BY seq DESC LIMIT 1").fetchone()
        if not r:
            return None
        out = json.loads(r["report_json"])
        out["at"] = r["at"]
        return out

    # ------------------------------------------------------------------ audit

    def _head_hash(self, c: sqlite3.Connection) -> str:
        row = c.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        return row["hash"] if row else GENESIS_HASH

    def append_audit(self, kind: str, payload: dict, actor: str = "agent", ref: str | None = None) -> dict:
        return self.append_audit_many([(kind, payload, actor, ref)])[0]

    def append_audit_many(self, rows: Iterable[tuple[str, dict, str, str | None]]) -> list[dict]:
        """Append in one transaction. The chain is computed sequentially inside
        the lock, so concurrent writers cannot interleave and fork it."""
        out: list[dict] = []
        with self.transaction() as c:
            prev = self._head_hash(c)
            for kind, payload, actor, ref in rows:
                at = now_iso()
                body = _audit_body(kind, actor, ref, at, payload)
                h = _chain_hash(prev, body)
                cur = c.execute(
                    "INSERT INTO audit_log (at, actor, kind, ref, payload_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                    (at, actor, kind, ref, canonical(payload), prev, h),
                )
                out.append({"seq": cur.lastrowid, "at": at, "actor": actor, "kind": kind, "ref": ref, "hash": h, "prevHash": prev})
                prev = h
        return out

    def verify_audit(self) -> dict:
        """Recompute every hash from genesis. Returns the first break, if any."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT seq, at, actor, kind, ref, payload_json, prev_hash, hash FROM audit_log ORDER BY seq ASC"
            ).fetchall()
        prev = GENESIS_HASH
        for r in rows:
            body = _audit_body(r["kind"], r["actor"], r["ref"], r["at"], json.loads(r["payload_json"]))
            expected = _chain_hash(prev, body)
            if r["prev_hash"] != prev or r["hash"] != expected:
                return {"ok": False, "rows": len(rows), "firstBreak": r["seq"], "head": rows[-1]["hash"]}
            prev = r["hash"]
        return {"ok": True, "rows": len(rows), "firstBreak": None, "head": prev}

    def audit_tail(self, limit: int = 100, kind: str | None = None, ref: str | None = None) -> list[dict]:
        sql = "SELECT seq, at, actor, kind, ref, payload_json, prev_hash, hash FROM audit_log"
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if ref:
            clauses.append("ref = ?")
            params.append(ref)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "seq": r["seq"],
                "at": r["at"],
                "actor": r["actor"],
                "kind": r["kind"],
                "ref": r["ref"],
                "payload": json.loads(r["payload_json"]),
                "prevHash": r["prev_hash"],
                "hash": r["hash"],
            }
            for r in rows
        ]

    def audit_count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
