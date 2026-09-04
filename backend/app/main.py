"""RecoverOps FastAPI backend.

Endpoints match src/lib/api.ts on the frontend exactly:

  POST /api/batch/run              start a batch, returns {batchId}
  GET  /api/batch/stream?batch_id  SSE replay of the decision stream
  GET  /api/batch/latest           most recent BatchResult
  GET  /api/batch/{id}/results     one BatchResult
  GET  /api/events/{id}/trace      DecisionTrace
  GET  /api/sleeping-dogs          latest batch's no-action ledger
  GET  /api/exceptions             latest batch's escalations

Run:  uvicorn app.main:app --port 8000    (from backend/, venv active)

Batches are kept in memory (latest few) and the latest is also written to
data/batches/ so a restart still has something to serve.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import OrderedDict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .diagnosis import Diagnoser  # noqa: E402
from .engine import run_batch  # noqa: E402
from .executor import Executor  # noqa: E402
from .retrieval import CaseMemory, Corpus  # noqa: E402
from .uplift import UpliftEngine  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BATCH_DIR = DATA_DIR / "batches"
BATCH_DIR.mkdir(exist_ok=True)
MAX_BATCHES_IN_MEMORY = 5

app = FastAPI(title="RecoverOps backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3100"],
    allow_methods=["*"],
    allow_headers=["*"],
)

corpus = Corpus()
memory = CaseMemory()
executor = Executor()
diagnoser = Diagnoser(corpus)
uplift = UpliftEngine()  # trains on first ever boot, then loads the pickle cache

_lock = threading.Lock()
_batches: OrderedDict[str, dict] = OrderedDict()  # batchId -> {"batch": ..., "traces": ...}
_latest_id: str | None = None


def _remember(result: dict) -> str:
    global _latest_id
    batch_id = result["batch"]["batchId"]
    with _lock:
        _batches[batch_id] = result
        while len(_batches) > MAX_BATCHES_IN_MEMORY:
            _batches.popitem(last=False)
        _latest_id = batch_id
    (BATCH_DIR / "latest.json").write_text(json.dumps(result))
    return batch_id


def _load_persisted() -> None:
    global _latest_id
    path = BATCH_DIR / "latest.json"
    if path.exists():
        try:
            result = json.loads(path.read_text())
            _batches[result["batch"]["batchId"]] = result
            _latest_id = result["batch"]["batchId"]
        except (json.JSONDecodeError, KeyError):
            pass


_load_persisted()


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "estimator": uplift.label,
        "benchmark": uplift.benchmark["estimators"],
        "retrieval": corpus.benchmark(),
        "razorpayLive": executor.client is not None,
        "llmLive": bool(diagnoser.api_key),
    }


@app.post("/api/batch/run")
def batch_run() -> dict:
    result = run_batch(uplift, corpus, memory, executor, diagnoser)
    batch_id = _remember(result)
    return {"batchId": batch_id}


@app.get("/api/batch/latest")
def batch_latest() -> dict:
    with _lock:
        if _latest_id is None:
            raise HTTPException(404, "No batch has been run yet.")
        return _batches[_latest_id]["batch"]


@app.get("/api/batch/{batch_id}/results")
def batch_results(batch_id: str) -> dict:
    with _lock:
        entry = _batches.get(batch_id)
    if entry is None:
        raise HTTPException(404, f"Unknown batch {batch_id}")
    return entry["batch"]


@app.get("/api/batch/stream")
async def batch_stream(batch_id: str) -> StreamingResponse:
    with _lock:
        entry = _batches.get(batch_id)
    if entry is None:
        raise HTTPException(404, f"Unknown batch {batch_id}")
    script = entry["batch"]["streamScript"]

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
def event_trace(event_id: str) -> dict:
    with _lock:
        entries = list(_batches.values())
    for entry in reversed(entries):
        trace = entry["traces"].get(event_id)
        if trace:
            return trace
    raise HTTPException(404, f"No trace for event {event_id}")


@app.get("/api/sleeping-dogs")
def sleeping_dogs() -> list:
    with _lock:
        if _latest_id is None:
            raise HTTPException(404, "No batch has been run yet.")
        return _batches[_latest_id]["batch"]["sleepingDogs"]


@app.get("/api/exceptions")
def exceptions() -> list:
    with _lock:
        if _latest_id is None:
            raise HTTPException(404, "No batch has been run yet.")
        return _batches[_latest_id]["batch"]["exceptions"]
