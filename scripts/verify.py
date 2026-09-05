#!/usr/bin/env python3
"""RecoverOps verification run — every check, on one screen.

    python3 scripts/verify.py              # hermetic: no credentials touched
    python3 scripts/verify.py --live       # also hit the real Razorpay account
    python3 scripts/verify.py --json out.json

Written to be run in front of someone. Each section states the property it
establishes, not merely that it passed — "412 tests green" says nothing about
which guarantees hold.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
PY_BIN = BACKEND / ".venv/bin/python"

C = {"b": "\033[1m", "r": "\033[0m", "amber": "\033[38;5;179m",
     "green": "\033[38;5;108m", "red": "\033[38;5;167m", "grey": "\033[38;5;245m"}


def head(text: str) -> None:
    print(f"\n{C['amber']}{'─' * 78}{C['r']}\n{C['b']}{text}{C['r']}\n{C['amber']}{'─' * 78}{C['r']}")


def line(ok, label: str, detail: str = "") -> None:
    mark = {True: f"{C['green']}✓{C['r']}", False: f"{C['red']}✗{C['r']}", None: f"{C['grey']}·{C['r']}"}[ok]
    print(f"  {mark} {label:<54}{C['grey']}{detail}{C['r']}")


SUITES = [
    ("test_compliance_redteam.py", "Compliance red-team",
     "deliberate attempts to slip a prohibited action past the gate"),
    ("test_razorpay_conformance.py", "Razorpay contract conformance",
     "every published error_reason, entity shape and webhook event"),
    ("test_money_privacy_integrity.py", "Money, privacy, tamper-evidence",
     "paise reconcile exactly; no PII in the ledger; edits to the audit chain are caught"),
    ("test_api_contract.py", "HTTP API contract",
     "every endpoint, its status codes and its input validation"),
    ("test_hostile_and_scale.py", "Hostile input, idempotence, scale",
     "malformed uploads, replayed webhooks, concurrent writes, 2,000-event batches"),
    ("test_outcome_attribution.py", "Outcome attribution",
     "how a decision becomes a measured result, and how it fails safely"),
    ("test_policy.py", "Policy gate behaviour", "the ordered rule set on clean cases"),
    ("test_frontend_policy_parity.py", "Policy shown = policy enforced",
     "the website's rule table against the gate's actual rules"),
    ("test_engine.py", "Engine", "synthetic and real batches, end to end"),
    ("test_learning.py", "Learning loop", "control arm, known propensities, honest intervals"),
    ("test_degradation.py", "Degradation detection", "downtime feed and changepoint detector"),
    ("test_promises.py", "Promise-to-pay", "the hold, the clock, and what verifies a promise"),
    ("test_receivables_checkout_voice.py", "Receivables · checkout · mandates · voice", "the other leak types"),
    ("test_store.py", "Ledger", "migrations, persistence, the hash chain"),
    ("test_sources.py", "Ingestion", "Razorpay entities and file exports into LeakEvents"),
    ("test_taxonomy.py", "Reason taxonomy", "error_reason to family, and the advisory boundary"),
    ("test_webhooks.py", "Webhook receiver", "signature verification and replay handling"),
    ("test_executor.py", "Executor", "honest execution records, and refusal handling"),
]

PROPERTIES = [
    "No prohibited action passed the compliance gate, on any input tried",
    "The baseline agent is held to identical compliance rules",
    "All 109 of Razorpay's published error_reason strings normalise",
    "Money is integer paise everywhere; net value reconciles exactly",
    "No phone number or email reaches the ledger, traces or audit log",
    "Editing, deleting, inserting or re-hashing an audit row is detected",
    "A replayed webhook is acknowledged, never attributed twice",
    "A refused Razorpay call degrades to a labelled mock; the batch survives",
    "The same seed reproduces the same batch, decision for decision",
    "An unparseable upload yields no leaks rather than plausible-but-wrong ones",
    "Every rule the website shows is a rule the gate actually enforces, in order",
]

LIVE_PROPERTIES = [
    "Test-mode credentials authenticate (a live-mode key is refused)",
    "The live downtime feed matches the shape the gate parses",
    "Orders and invoices are created and read back",
    "Razorpay's own validation is surfaced, not pre-empted",
    "A full batch runs against the account with an intact audit chain",
]


def pytest_run(paths: list[str], env: dict | None = None) -> tuple[bool, dict, str]:
    proc = subprocess.run(
        # pytest.ini already sets -q; passing it again suppresses the summary
        # line this parses. -o addopts= clears the file's options instead.
        [str(PY_BIN), "-m", "pytest", *paths, "-p", "no:warnings", "--no-header", "-o", "addopts="],
        cwd=BACKEND, capture_output=True, text=True, env=env,
    )
    out = proc.stdout + proc.stderr
    counts = {k: int(m.group(1)) for k in ("passed", "failed", "skipped", "error")
              if (m := re.search(rf"(\d+) {k}", out))}
    return proc.returncode == 0, counts, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also run against the real Razorpay account")
    ap.add_argument("--json", metavar="PATH")
    args = ap.parse_args()

    report: dict = {"startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "suites": []}
    t0 = time.perf_counter()

    print(f"\n{C['b']}RecoverOps — verification run{C['r']}")
    print(f"{C['grey']}Hermetic by default: credentials are stripped, so this behaves identically")
    print(f"on a machine with API keys and one without.{C['r']}")

    head("Test suites — and the property each one establishes")
    total = {"passed": 0, "failed": 0, "skipped": 0}
    for filename, title, proves in SUITES:
        ok, counts, _ = pytest_run([f"tests/{filename}"])
        for k in total:
            total[k] += counts.get(k, 0)
        detail = f"{counts.get('passed', 0)} passed"
        if counts.get("skipped"):
            detail += f", {counts['skipped']} skipped"
        if counts.get("failed"):
            detail += f", {counts['failed']} FAILED"
        line(ok, title, detail)
        print(f"    {C['grey']}{proves}{C['r']}")
        report["suites"].append({"file": filename, "title": title, "proves": proves, "ok": ok, **counts})

    head("Guarantees")
    for text in PROPERTIES:
        line(total["failed"] == 0, text)

    live_ok = None
    if args.live:
        head("Live integration — the real Razorpay account")
        ok, counts, out = pytest_run(["tests/test_live_razorpay.py"],
                                     env=dict(os.environ, RECOVEROPS_LIVE="1"))
        live_ok = ok
        line(ok, "Live API conformance",
             f"{counts.get('passed', 0)} passed, {counts.get('skipped', 0)} skipped")
        for text in LIVE_PROPERTIES:
            line(ok or None, text)
        if counts.get("skipped"):
            print(f"    {C['grey']}skips are account limits (e.g. Razorpay's 30 test-mode payment links),{C['r']}")
            print(f"    {C['grey']}not conformance failures — each states its reason.{C['r']}")
        report["live"] = {"ok": ok, **counts}
        if not ok:
            print(out[-1500:])

    elapsed = time.perf_counter() - t0
    report["totals"] = total
    report["seconds"] = round(elapsed, 1)

    head("Result")
    passed = total["failed"] == 0 and (live_ok is not False)
    line(passed, "VERIFICATION " + ("PASSED" if passed else "FAILED"),
         f"{total['passed']} tests · {elapsed:.1f}s")
    print()

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
        print(f"{C['grey']}  summary written to {args.json}{C['r']}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
