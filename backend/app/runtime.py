"""Shared runtime: the live layers, wired once, and the one way to run a batch.

Both the API server and the seed script need the same objects and the same
"pull leaks from a source, assign arms, run the pipeline, persist, audit"
sequence. Keeping that here means there is exactly one place that decides
what a batch run writes down.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv  # noqa: E402  (import order is deliberate: env before layers)

# RECOVEROPS_NO_DOTENV lets the test suite run identically on a machine that has
# real API keys and one that does not. Without it a developer's own .env would
# quietly turn hermetic tests into live-API tests.
if os.environ.get("RECOVEROPS_NO_DOTENV") != "1":
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .degradation import DegradationMonitor  # noqa: E402
from .diagnosis import Diagnoser  # noqa: E402
from .engine import run_batch  # noqa: E402
from .executor import Executor  # noqa: E402
from .learning import RealLearner  # noqa: E402
from .merchant import MerchantConfig  # noqa: E402
from .outcomes import OutcomeTracker  # noqa: E402
from .promises import PromiseBook  # noqa: E402
from .retrieval import CaseMemory, Corpus  # noqa: E402
from .sources import (  # noqa: E402
    CheckoutSource,
    FileSource,
    LeakSource,
    RazorpaySource,
    ReceivablesSource,
    SimulatorSource,
    apply_holdout,
)
from .store import DATA_DIR, Store  # noqa: E402
from .uplift import UpliftEngine  # noqa: E402
from .voice import SarvamVoice, run_call  # noqa: E402
from .webhooks import WebhookReceiver  # noqa: E402

LEGACY_BATCH_DIR = DATA_DIR / "batches"
AUTO_RETRAIN_EVERY = 50  # newly resolved real outcomes between automatic refits


@dataclass
class Runtime:
    store: Store
    merchant: MerchantConfig
    corpus: Corpus
    memory: CaseMemory
    executor: Executor
    diagnoser: Diagnoser
    uplift: UpliftEngine
    sources: dict[str, LeakSource]
    outcomes: OutcomeTracker
    learner: RealLearner
    promises: PromiseBook
    degradation: DegradationMonitor
    voice: SarvamVoice
    webhooks: WebhookReceiver
    _resolved_at_last_fit: int = 0

    @classmethod
    def build(cls, store_path: Path | str | None = None, merchant_path: Path | str | None = None) -> "Runtime":
        store = Store(store_path)
        merchant = MerchantConfig.load(merchant_path)
        corpus = Corpus()
        executor = Executor()
        memory = CaseMemory(store)
        sources: dict[str, LeakSource] = {
            "simulator": SimulatorSource(),
            "razorpay": RazorpaySource(executor.client, merchant),
            "file": FileSource(),
            "receivables": ReceivablesSource(executor.client, merchant),
            "checkout": CheckoutSource(merchant),
        }
        rt = cls(
            store=store,
            merchant=merchant,
            corpus=corpus,
            memory=memory,
            executor=executor,
            diagnoser=Diagnoser(corpus),
            uplift=UpliftEngine(),  # trains on first ever boot, then loads the pickle cache
            sources=sources,
            outcomes=OutcomeTracker(store, memory, executor.client),
            learner=RealLearner(store) if store.path != ":memory:" else RealLearner(store, path=Path(os.devnull)),
            promises=PromiseBook(store),
            degradation=DegradationMonitor(executor.client),
            voice=SarvamVoice(),
            webhooks=WebhookReceiver(store, OutcomeTracker(store, memory, executor.client), PromiseBook(store)),
        )
        rt._resolved_at_last_fit = store.leak_counts()["resolved"]
        return rt

    # --------------------------------------------------------------- sources

    def describe_sources(self) -> list[dict]:
        return [s.describe() for s in self.sources.values()]

    # ------------------------------------------------------------------ runs

    def run_and_store(self, source: str = "simulator", seed: int | None = None, batch_source_label: str = "live", **pull_kwargs) -> dict:
        """Pull from a source, assign arms, run every layer, persist, audit.

        Returns the stored summary. The audit log gets one ``decision`` row per
        event for Agent B — action, message class, arm, propensity, every rule
        verdict, the execution record, the outcome — plus a ``batch.completed``
        row carrying the summary. A blocked action leaves the same trail as an
        executed one.
        """
        src = self.sources.get(source)
        if src is None:
            raise ValueError(f"unknown source {source!r}; expected one of {sorted(self.sources)}")
        pulled = src.pull(seed=seed, **pull_kwargs)
        if not pulled.leaks:
            raise LookupError(pulled.meta.get("error") or f"source {source!r} returned no leak events")
        seed = pulled.meta.get("seed", seed)
        apply_holdout(pulled.leaks, self.merchant.holdout_share)

        # Promises age before anything is decided, so a promise that broke
        # overnight stops holding this batch's leaks.
        self.promises.tick()
        promise_holds = {cid: p.public() for cid, p in self.promises.open_map().items()}
        # Degradation: Razorpay's declared downtimes plus our own detector over
        # whatever payment stream this pull happened to see.
        view = self.degradation.view(payments=pulled.meta.get("raw_payments") or None)

        self.store.append_audit(
            "batch.started",
            {
                "source": source,
                "seed": seed,
                "leaks": len(pulled.leaks),
                "holdout": sum(1 for ev in pulled.leaks if ev.holdout),
                "liveCohorts": view.public()["live"],
                "openPromises": len(promise_holds),
                "estimator": self.learner.label if not pulled.leaks[0].is_synthetic else self.uplift.label,
                "merchant": self.merchant.name,
                "pull": _audit_safe(pulled.meta),
            },
            actor="engine",
        )
        result = run_batch(
            self.uplift,
            self.corpus,
            self.memory,
            self.executor,
            self.diagnoser,
            self.merchant,
            pulled.leaks,
            source_name=source,
            seed=seed,
            source_meta=_audit_safe(pulled.meta),
            real_learner=self.learner,
            degradation=view,
            promise_holds=promise_holds,
        )
        result["batch"]["source"] = batch_source_label
        batch_id = self.store.save_batch(result)
        self.store.save_leaks(batch_id, result["leakRows"])
        deg = result["batch"].get("degradation") or {}
        if deg.get("cohorts"):
            self.store.save_degradations(batch_id, deg["cohorts"], deg.get("eventsHeld", {}))
        rows = [
            ("decision", _decision_audit_payload(trace), "agent:B", event_id)
            for event_id, trace in result["traces"].items()
        ]
        summary = self.store.summarize(result["batch"])
        rows.append(("batch.completed", summary, "engine", batch_id))
        self.store.append_audit_many(rows)
        return self.store.list_batches(limit=1)[0]

    # -------------------------------------------------------------- outcomes

    def sync_outcomes(self) -> dict:
        report = self.outcomes.sync()
        resolved = self.store.leak_counts()["resolved"]
        if resolved - self._resolved_at_last_fit >= AUTO_RETRAIN_EVERY:
            report["retrain"] = self.retrain(trigger="auto")
        return report

    def retrain(self, trigger: str = "manual") -> dict:
        report = self.learner.fit()
        self._resolved_at_last_fit = self.store.leak_counts()["resolved"]
        self.store.append_audit("learning.retrained", {**report, "trigger": trigger}, actor="scheduler" if trigger == "auto" else "operator")
        return report

    def learning_status(self) -> dict:
        return self.learner.status()

    # ------------------------------------------------------------- promises

    def capture_promise(self, event_id: str, amount_paise: int, due_at: str, captured_via: str, verbatim: str = "") -> dict:
        leak = self.store.latest_leak(event_id)
        counterparty = leak["counterparty_id"] if leak else event_id
        p = self.promises.record(counterparty, amount_paise, due_at, captured_via, verbatim, event_id)
        return p.public()

    def promises_view(self) -> dict:
        self.promises.tick()
        return {"stats": self.promises.stats(), "promises": [p.public() for p in self.promises.all(limit=200)]}

    # ---------------------------------------------------------- degradation

    def degradation_view(self) -> dict:
        view = self.degradation.view()
        return {
            **view.public(),
            "history": self.store.recent_degradations(limit=40),
            "detectorNote": (
                "Two independent signals: Razorpay's declared downtime feed, and a CUSUM changepoint on the success "
                "rate per instrument computed from the payment stream. Either one holds the cohort; a declared "
                "downtime outranks the detector on the same key."
            ),
        }

    # ---------------------------------------------------------------- voice

    def place_call(self, event_id: str, seed: int | None = None) -> dict:
        """Run the scripted Hinglish call for one leak over a simulated line.
        A promise captured on the call is recorded through the same book that
        holds every other action."""
        import numpy as np

        trace = self.store.get_trace(event_id)
        leak = self.store.latest_leak(event_id)
        if trace is None or leak is None:
            raise LookupError(f"no leak on record for {event_id}")
        ev = _leak_to_event(leak, trace)
        rng = np.random.default_rng(seed if seed is not None else abs(hash(event_id)) % (2**31))
        result = run_call(ev, self.merchant.name, self.voice, rng)
        payload = result.public()
        if result.promise:
            p = self.capture_promise(
                event_id,
                int(result.promise["amountPaise"]),
                str(result.promise["dueAt"]),
                "voice",
                str(result.promise["verbatim"]),
            )
            payload["recordedPromise"] = p
        self.store.append_audit(
            "voice.call",
            {
                "eventId": event_id,
                "outcome": result.outcome,
                "audioLive": result.audio_live,
                "turns": len(result.turns),
                "durationSeconds": result.duration_s,
                "promise": result.promise,
                "note": result.note,
            },
            actor="agent:B",
            ref=event_id,
        )
        return payload

    def trace_with_outcome(self, event_id: str, batch_id: str | None = None) -> dict | None:
        """The stored trace, with the attributed outcome overlaid when a real
        leak has resolved since the batch ran. The trace itself is immutable."""
        trace = self.store.get_trace(event_id, batch_id)
        if trace is None:
            return None
        leak = self.store.latest_leak(event_id)
        if leak and not leak["synthetic"] and leak["outcome_state"] != "pending":
            if leak["outcome_state"] == "resolved" and leak["outcome_recovered"] is not None:
                trace["agentB"]["outcome"] = {"recovered": bool(leak["outcome_recovered"]), "churned": bool(leak["outcome_churned"])}
            trace["outcomeAttribution"] = {
                "state": leak["outcome_state"],
                "source": leak["outcome_source"],
                "at": leak["outcome_at"],
            }
        return trace

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
        /api/batch/latest. One simulator batch on first boot; the console and
        the landing page then have live data before anyone presses Run."""
        if os.environ.get("RECOVEROPS_SKIP_FIRST_BATCH") == "1":
            return
        if self.store.count_batches() == 0:
            self.run_and_store("simulator")


def _audit_safe(meta: dict) -> dict:
    """Pull metadata without anything that could identify a customer."""
    return {k: v for k, v in meta.items() if k not in ("files", "raw_payments")}


def _leak_to_event(leak: dict, trace: dict):
    """Rebuild enough of a LeakEvent from a stored row to place a call against
    it. The ledger holds the decision; this only needs what the script says."""
    from .leaks import LeakEvent

    raw = trace.get("leak") or {}
    return LeakEvent(
        event_id=leak["event_id"],
        kind=leak["kind"],
        source=leak["source"],
        customer_id=leak.get("counterparty_id") or "",
        amount_paise=leak["amount_paise"],
        plan_name=str(raw.get("planName") or ""),
        method=leak.get("method") or "card",
        reason_code=leak["reason_code"],
        reason_label=str(raw.get("reasonLabel") or leak["reason_code"]),
        contacts_last_7d=int(raw.get("contactsLast7d") or 0),
        segment=(trace.get("truth") or {}).get("segment") if trace.get("truth") else None,
        # Greet a person, not a line item. Sources that know the counterparty's
        # name put it here; the rest stay neutral rather than guessing one.
        extras={"customer_name": raw.get("customerName") or "Customer"},
    )


def _decision_audit_payload(trace: dict) -> dict:
    b = trace["agentB"]
    return {
        "eventId": trace["eventId"],
        "kind": trace.get("kind"),
        "dataMode": trace.get("dataMode"),
        "arm": b.get("arm"),
        "wanted": b.get("wanted"),
        "explored": b.get("explored"),
        "propensity": b.get("propensity"),
        "action": b["chosenAction"],
        "messageClass": b["messageClass"],
        "blockedBy": b["blockedBy"],
        "deniedAction": b["deniedAction"],
        "deniedBy": b["deniedBy"],
        "gate": [{"ruleId": g["ruleId"], "verdict": g["verdict"]} for g in b["gate"]],
        "execution": {
            "mode": b["execution"]["mode"],
            "mocked": b["execution"]["mocked"],
            "detail": b["execution"]["detail"],
            "externalKind": b["execution"].get("externalKind"),
            "externalId": b["execution"].get("externalId"),
        },
        "outcome": b["outcome"],
        "costPaise": b.get("costPaise"),
        "upliftHat": trace["uplift"]["upliftHat"],
        "estimator": trace["uplift"]["estimator"],
        "diagnosis": trace["diagnosis"]["method"],
        "reasonCode": trace["leak"]["reasonCode"],
        "rawReason": trace["leak"]["rawReason"],
        "amountPaise": trace["leak"]["amountPaise"],
        "contactHash": trace["leak"]["contactHash"],
    }
