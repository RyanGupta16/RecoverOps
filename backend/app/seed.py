"""Seed the ledger so a fresh clone is not empty.

    python -m app.seed --batches 3            # from backend/, venv active
    python -m app.seed --batches 1 --seed 42

Each batch runs through every layer exactly as a console run does, so the
case memory fills with resolved cases and the history list has rows. Without
this, the retrieval layer's first answer on a fresh clone is "no similar prior
cases in memory yet" — honest, but not what a first visitor should see.
"""

from __future__ import annotations

import argparse
import sys

from .runtime import Runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed RecoverOps' ledger with batches.")
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--seed", type=int, default=None, help="base seed; batch i uses seed + i")
    args = parser.parse_args(argv)

    rt = Runtime.build()
    for i in range(args.batches):
        seed = None if args.seed is None else args.seed + i
        summary = rt.run_and_store("simulator", seed=seed)
        b = summary["agents"]["B"]
        print(
            f"{summary['batchId']}  seed={summary['seed']}  events={summary['eventCount']}  "
            f"contacts={b['contactsMade']}  net=₹{b['netValuePaise'] / 100:,.0f}",
            file=sys.stderr,
        )
    verify = rt.store.verify_audit()
    print(f"audit chain: {verify['rows']} rows, ok={verify['ok']}", file=sys.stderr)
    return 0 if verify["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
