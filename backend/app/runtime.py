"""Shared runtime: the live layers, wired once, and the one way to run a batch.

Both the API server and the seed script need the same objects and the same
"run a batch, persist it, write the audit rows" sequence. Keeping that here
means there is exactly one place that decides what a batch run writes down.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .diagnosis import Diagnoser  # noqa: E402
from .engine import run_batch  # noqa: E402
from .executor import Executor  # noqa: E402
from .retrieval import CaseMemory, Corpus  # noqa: E402
from .store import DATA_DIR, Store  # noqa: E402
from .uplift import UpliftEngine  # noqa: E402

LEGACY_BATCH_DIR = DATA_DIR / "batches"


@dataclass
class Runtime:
    store: Store
    corpus: Corpus
    memory: CaseMemory
    executor: Executor
    diagnoser: Diagnoser
    uplift: UpliftEngine

    @classmethod
    def build(cls, store_path: Path | str | None = None) -> "Runtime":
        store = Store(store_path)
        corpus = Corpus()
        return cls(
            store=store,
            corpus=corpus,
            memory=CaseMemory(store),
            executor=Executor(),
            diagnoser=Diagnoser(corpus),
            uplift=UpliftEngine(),  # trains on first ever boot, then loads the pickle cache
        )

    # ------------------------------------------------------------------ runs

    def run_and_store(self, seed: int | None = None, source: str = "live") -> dict:
        """Run one batch through every layer, persist it, and write the audit rows.

        Returns the stored summary. The audit log gets one ``decision`` row per
        event for Agent B — action, message class, every rule verdict, the
        execution record, the outcome — plus a ``batch.completed`` row carrying
        the summary. A blocked action leaves the same trail as an executed one.
        """
        self.store.append_audit(
            "batch.started",
            {"seed": seed, "source": source, "estimator": self.uplift.label},
            actor="engine",
        )
        result = run_batch(self.uplift, self.corpus, self.memory, self.executor, self.diagnoser, seed=seed)
        result["batch"]["source"] = source
        batch_id = self.store.save_batch(result)
        rows = [
            ("decision", _decision_audit_payload(trace), "agent:B", event_id)
            for event_id, trace in result["traces"].items()
        ]
        summary = self.store.summarize(result["batch"])
        rows.append(("batch.completed", summary, "engine", batch_id))
        self.store.append_audit_many(rows)
        return self.store.list_batches(limit=1)[0]

    # ------------------------------------------------------------- first boot

    def import_legacy_batch(self) -> str | None:
        """One-time import of the pre-store ``data/batches/latest.json`` so an
        upgrade keeps the batch it had. Renamed after import so it runs once."""
        path = LEGACY_BATCH_DIR / "latest.json"
        if not path.exists() or self.store.count_batches() > 0:
            return None
        try:
            result = json.loads(path.read_text())
            batch_id = self.store.save_batch(result)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        self.store.append_audit(
            "batch.imported",
            {"batchId": batch_id, "from": str(path.relative_to(DATA_DIR.parent))},
            actor="system",
            ref=batch_id,
        )
        path.rename(path.with_suffix(".json.imported"))
        return batch_id

    def ensure_first_batch(self) -> None:
        """A fresh clone should not greet its first visitor with a 404 on
        /api/batch/latest. One batch on first boot; the console and the
        landing page then have live data before anyone presses Run."""
        if os.environ.get("RECOVEROPS_SKIP_FIRST_BATCH") == "1":
            return
        if self.store.count_batches() == 0:
            self.run_and_store()


def _decision_audit_payload(trace: dict) -> dict:
    b = trace["agentB"]
    return {
        "eventId": trace["eventId"],
        "action": b["chosenAction"],
        "messageClass": b["messageClass"],
        "blockedBy": b["blockedBy"],
        "deniedAction": b["deniedAction"],
        "deniedBy": b["deniedBy"],
        "gate": [{"ruleId": g["ruleId"], "verdict": g["verdict"]} for g in b["gate"]],
        "execution": {"mode": b["execution"]["mode"], "mocked": b["execution"]["mocked"], "detail": b["execution"]["detail"]},
        "outcome": b["outcome"],
        "upliftHat": trace["uplift"]["upliftHat"],
        "estimator": trace["uplift"]["estimator"],
        "diagnosis": trace["diagnosis"]["method"],
    }
