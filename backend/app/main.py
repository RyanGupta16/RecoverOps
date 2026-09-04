"""RecoverOps FastAPI backend.

Endpoints match src/lib/api.ts on the frontend exactly:

  GET  /api/health                     layers, benchmark, key status, audit state
  POST /api/batch/run                  run a batch, returns {batchId}
  GET  /api/batches?limit=             batch history, newest first (summaries)
  GET  /api/batch/latest               most recent BatchResult
  GET  /api/batch/{id}/results         one BatchResult
  GET  /api/batch/{id}/sleeping-dogs   that batch's no-action ledger
  GET  /api/batch/{id}/exceptions      that batch's escalations
  GET  /api/batch/stream?batch_id      SSE replay of the decision stream
  GET  /api/events/{id}/trace          DecisionTrace (optionally ?batch_id=)
  GET  /api/sleeping-dogs              latest batch's no-action ledger
  GET  /api/exceptions                 latest batch's escalations
  GET  /api/audit?limit=&kind=&ref=    audit log tail
  GET  /api/audit/verify               walk the hash chain, report the first break

Run:  uvicorn app.main:app --port 8000    (from backend/, venv active)

Every batch and every trace is persisted in data/ledger.db; a restart serves
the same history. See store.py for the schema and the audit chain.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .runtime import Runtime

rt = Runtime.build()
rt.import_legacy_batch()
rt.ensure_first_batch()

app = FastAPI(title="RecoverOps backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3100"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _batch_or_404(batch_id: str) -> dict:
    batch = rt.store.get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, f"Unknown batch {batch_id}")
    return batch


def _latest_or_404() -> dict:
    latest = rt.store.latest_batch_id()
    if latest is None:
        raise HTTPException(404, "No batch has been run yet.")
    return _batch_or_404(latest)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "estimator": rt.uplift.label,
        "benchmark": rt.uplift.benchmark["estimators"],
        "retrieval": rt.corpus.benchmark(),
        "razorpayLive": rt.executor.client is not None,
        "llmLive": bool(rt.diagnoser.api_key),
        "store": {
            "schemaVersion": rt.store.schema_version,
            "batches": rt.store.count_batches(),
            "auditRows": rt.store.audit_count(),
        },
    }


@app.post("/api/batch/run")
def batch_run() -> dict:
    summary = rt.run_and_store()
    return {"batchId": summary["batchId"]}


@app.get("/api/batches")
def batches(limit: int = Query(25, ge=1, le=200)) -> list:
    return rt.store.list_batches(limit=limit)


@app.get("/api/batch/latest")
def batch_latest() -> dict:
    return _latest_or_404()


@app.get("/api/batch/{batch_id}/results")
def batch_results(batch_id: str) -> dict:
    return _batch_or_404(batch_id)


@app.get("/api/batch/{batch_id}/sleeping-dogs")
def batch_sleeping_dogs(batch_id: str) -> list:
    return _batch_or_404(batch_id)["sleepingDogs"]


@app.get("/api/batch/{batch_id}/exceptions")
def batch_exceptions(batch_id: str) -> list:
    return _batch_or_404(batch_id)["exceptions"]


@app.get("/api/batch/stream")
async def batch_stream(batch_id: str) -> StreamingResponse:
    script = _batch_or_404(batch_id)["streamScript"]

    async def gen():
        for line in script:
            yield f"data: {json.dumps(line)}\n\n"
            # Pace the replay so the console reads as a stream, not a dump.
            await asyncio.sleep(0.012 if line["kind"] == "decision" else 0.05)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


@app.get("/api/events/{event_id}/trace")
def event_trace(event_id: str, batch_id: str | None = None) -> dict:
    trace = rt.store.get_trace(event_id, batch_id)
    if trace is None:
        raise HTTPException(404, f"No trace for event {event_id}")
    return trace


@app.get("/api/sleeping-dogs")
def sleeping_dogs() -> list:
    return _latest_or_404()["sleepingDogs"]


@app.get("/api/exceptions")
def exceptions() -> list:
    return _latest_or_404()["exceptions"]


@app.get("/api/audit")
def audit(limit: int = Query(100, ge=1, le=1000), kind: str | None = None, ref: str | None = None) -> dict:
    return {"rows": rt.store.audit_tail(limit=limit, kind=kind, ref=ref), "total": rt.store.audit_count()}


@app.get("/api/audit/verify")
def audit_verify() -> dict:
    return rt.store.verify_audit()
