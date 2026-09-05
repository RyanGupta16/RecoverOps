"""Layer 01 — Diagnosis.

Deterministic reason-code lookup resolves most events at zero cost and zero
latency. Only codes marked ambiguous in the taxonomy (DO_NOT_HONOUR,
GATEWAY_ERROR — opaque issuer/gateway responses) go to the language model.

The LLM call is real when ANTHROPIC_API_KEY is set: Claude Haiku classifies
the failure, grounded in the top BM25 hits from the Razorpay error corpus
(retrieval-augmented, so the model chooses among documented codes instead of
free-associating). Result is cached per reason code for the process lifetime —
the ambiguity is a property of the code, not of the individual payment, so
re-asking per event would buy latency and spend for zero information.

Its classification is **advisory**. The gate runs on the deterministic family
mapping, never on the model's answer: a model that can move a leak from
"issuer-side, back off" to "merchant-side, never contact" is a model that can
change what the policy does, and the whole point of the gate sitting after the
engine is that it cannot. Where the two disagree the trace shows both, because
a persistent disagreement means the taxonomy is wrong and should be fixed at
the mapping rather than smoothed over per event.

Without a key the fallback is labelled as mocked in the trace note. It never
pretends a model was called.
"""

from __future__ import annotations

import json
import os
import time

import httpx

from .retrieval import Corpus
from .sim import Event

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"

_VALID_SIDES = {"customer", "issuer", "risk", "merchant"}


class Diagnoser:
    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self._cache: dict[str, dict] = {}

    def diagnose(self, ev: Event) -> dict:
        if not ev.ambiguous:
            via = f"Razorpay error_reason `{ev.raw_reason}` mapped to {ev.reason_code} ({ev.reason_confidence} confidence). " if ev.raw_reason else ""
            return {
                "method": "deterministic_lookup",
                "reasonCode": ev.reason_code,
                "reasonLabel": ev.reason_label,
                "failureSide": ev.failure_side,
                "modelFailureSide": None,
                "modelAdvisory": False,
                "disagreesWithGate": False,
                "latencyMs": 0,
                "note": f"{via}Resolved from the deterministic lookup table. No model call, no latency, no cost.",
            }

        cached = self._cache.get(ev.reason_code)
        if cached is None:
            cached = self._classify(ev)
            self._cache[ev.reason_code] = cached
        # The model's read is ADVISORY. The gate runs on the deterministic
        # family mapping, and must: a model that can move a leak between
        # "issuer-side, back off" and "merchant-side, never contact" is a model
        # that can change what the policy does. Where the two disagree, both
        # are shown — a persistent disagreement means the taxonomy needs fixing
        # at the mapping, not smoothing over per event.
        model_side = cached["failure_side"]
        disagrees = model_side != ev.failure_side
        note = cached["note"]
        if disagrees:
            note = (
                f"{note} The model reads this as {model_side}-side; the gate ran on the deterministic "
                f"{ev.failure_side}-side mapping for {ev.reason_code}, because a model must not be able "
                "to move a leak between policy paths."
            )
        return {
            "method": "llm_fallback",
            "reasonCode": ev.reason_code,
            "reasonLabel": ev.reason_label,
            # What the gate actually used.
            "failureSide": ev.failure_side,
            # What the model thought, kept separate and labelled.
            "modelFailureSide": model_side,
            "modelAdvisory": True,
            "disagreesWithGate": disagrees,
            "latencyMs": cached["latency_ms"],
            "note": note,
        }

    def _classify(self, ev: Event) -> dict:
        hits = self.corpus.search_bm25(f"{ev.reason_code} {ev.reason_label}", k=3)
        context = "\n\n".join(f"[{d['id']}] {d['title']}\n{d['text']}" for d, _ in hits)

        if not self.api_key:
            return {
                "failure_side": ev.failure_side,
                "latency_ms": 0,
                "note": (
                    "Reason code is ambiguous. LLM fallback is configured but ANTHROPIC_API_KEY is not set — "
                    "resolved from the corpus retrieval instead, labelled as such."
                ),
            }

        prompt = (
            "A payment failed. Gateway returned an ambiguous decline.\n"
            f"Reason family: {ev.reason_code}\nDescription: {ev.reason_label}\n"
            f"Razorpay error_reason: {ev.raw_reason or 'n/a'}; error_source: {ev.raw_source or 'n/a'}; "
            f"error_description: {ev.raw_description or 'n/a'}\n"
            f"Method: {ev.method}. Amount: ₹{ev.amount_paise / 100:.2f}.\n\n"
            f"Documented codes (retrieved):\n{context}\n\n"
            'Classify the failure side. Reply with JSON only: {"failure_side": "customer"|"issuer"|"risk"|"merchant", "rationale": "<one sentence>"}'
        )
        t0 = time.perf_counter()
        try:
            r = httpx.post(
                ANTHROPIC_URL,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json={"model": MODEL, "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]},
                timeout=20.0,
            )
            r.raise_for_status()
            latency = int((time.perf_counter() - t0) * 1000)
            text = r.json()["content"][0]["text"]
            parsed = json.loads(text[text.index("{") : text.rindex("}") + 1])
            side = parsed.get("failure_side", ev.failure_side)
            if side not in _VALID_SIDES:
                side = ev.failure_side
            return {
                "failure_side": side,
                "latency_ms": latency,
                "note": f"Ambiguous code escalated to {MODEL} with corpus grounding. {parsed.get('rationale', '')}".strip(),
            }
        except Exception as exc:  # noqa: BLE001 — any API failure degrades to the lookup, never crashes a batch
            return {
                "failure_side": ev.failure_side,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "note": f"LLM fallback attempted but failed ({type(exc).__name__}) — resolved from the corpus retrieval instead.",
            }
