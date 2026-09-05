"""Regenerate the bundled demo batch from the real engine.

    python -m app.export_sample            # from backend/, venv active
    npm run gen                            # same thing, from the repo root

Writes data/sample-batch.json and data/sample-traces.json in the shape the
frontend serves from /api/sample/*. Fixed seed, fixed batch id, so the bundled
batch is byte-stable across runs and identical in every respect — gate, reason
families, message classes, honesty text — to what a live run produces. There
is one engine and one gate; the demo is not a second implementation of them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .engine import run_batch
from .runtime import Runtime

ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_BATCH_ID = "bat_sample_20260903"
SAMPLE_SEED = 20260903


def main() -> int:
    rt = Runtime.build(store_path=":memory:")
    pulled = rt.sources["simulator"].pull(seed=SAMPLE_SEED)
    result = run_batch(
        rt.uplift, rt.corpus, rt.memory, rt.executor, rt.diagnoser, rt.merchant, pulled.leaks,
        source_name="simulator", seed=SAMPLE_SEED, batch_id=SAMPLE_BATCH_ID,
    )
    batch = result["batch"]
    batch["source"] = "sample"
    batch["label"] = "Bundled synthetic batch"
    batch["generatedBy"] = "backend/app/export_sample.py"
    # Timing is not a property of the bundled data.
    traces = [result["traces"][ev["eventId"]] for ev in batch["events"]]

    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "sample-batch.json").write_text(json.dumps(batch, indent=2, ensure_ascii=False) + "\n")
    (data_dir / "sample-traces.json").write_text(
        json.dumps({"source": "sample", "batchId": SAMPLE_BATCH_ID, "traces": traces}, indent=2, ensure_ascii=False) + "\n"
    )
    a, b = batch["agents"]["A"]["metrics"], batch["agents"]["B"]["metrics"]
    print("data/sample-batch.json + data/sample-traces.json written", file=sys.stderr)
    for name, m in (("Agent A (probability)", a), ("Agent B (uplift)", b)):
        print(
            f"  {name:24s} contacts={m['contactsMade']:3d} wasted={m['wastedContacts']:3d} dogs={m['sleepingDogsTouched']:2d} "
            f"recovered=₹{m['recoveredPaise'] / 100:,.0f} net=₹{m['netValuePaise'] / 100:,.0f}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
