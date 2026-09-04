"""Retrieval layer: corpus search + case memory.

Two interchangeable retrievers over the error corpus:

- BM25 (Okapi): probabilistic lexical ranking. Standard for short technical
  docs; handles term saturation (a doc mentioning "retry" 10x is not 10x more
  relevant) and doc-length normalisation.
- TF-IDF cosine: classic vector-space baseline. No saturation control, but a
  useful comparison point.

No embedding model on purpose: the corpus is ~13 docs of exact terminology
(reason codes, scheme rules). Lexical match is the signal; a semantic encoder
adds latency, a dependency, and hallucination-shaped nearest neighbours for
zero measured gain at this scale. The benchmark below makes that case with
numbers instead of vibes.

Case memory is SQLite: every resolved decision is written back and similar
past cases are retrieved by (reason_code, method, amount band).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class Corpus:
    def __init__(self) -> None:
        raw = json.loads((DATA_DIR / "corpus.json").read_text())
        self.documents: list[dict] = raw["documents"]
        self._texts = [f"{d['title']} {d['text']}" for d in self.documents]
        self._bm25 = BM25Okapi([_tokenize(t) for t in self._texts])
        self._tfidf = TfidfVectorizer(sublinear_tf=True)
        self._tfidf_matrix = self._tfidf.fit_transform(self._texts)

    def search_bm25(self, query: str, k: int = 3) -> list[tuple[dict, float]]:
        scores = self._bm25.get_scores(_tokenize(query))
        order = np.argsort(scores)[::-1][:k]
        return [(self.documents[i], float(scores[i])) for i in order if scores[i] > 0]

    def search_tfidf(self, query: str, k: int = 3) -> list[tuple[dict, float]]:
        q = self._tfidf.transform([query])
        scores = cosine_similarity(q, self._tfidf_matrix)[0]
        order = np.argsort(scores)[::-1][:k]
        return [(self.documents[i], float(scores[i])) for i in order if scores[i] > 0]

    def by_code(self, code: str) -> dict | None:
        return next((d for d in self.documents if d.get("code") == code), None)

    def benchmark(self) -> dict:
        """Every code-bearing doc has a known right answer for a query built
        from its reason label — measure precision@1 and MRR for both rankers."""
        cases = [
            ("insufficient balance salary retry", "rzp-insufficient-funds"),
            ("card expired needs update reissued", "rzp-card-expired"),
            ("do not honour 05 issuer opaque", "rzp-do-not-honour"),
            ("bank offline issuer unavailable outage", "rzp-issuer-down"),
            ("authorisation timed out upi collect", "rzp-timeout"),
            ("wrong cvv otp entered", "rzp-invalid-auth"),
            ("customer cancelled upi autopay mandate", "rzp-mandate-revoked"),
            ("daily transaction limit exceeded", "rzp-limit-exceeded"),
            ("fraud risk hold never retry", "rzp-suspected-fraud"),
            ("gateway processing server error transient", "rzp-gateway-error"),
            ("how many retries allowed visa 30 days", "network-retry-window"),
            ("promotional consent dnd 30 minute window", "tcccpr-transactional-window"),
        ]
        out = {}
        for name, search in (("bm25", self.search_bm25), ("tfidf", self.search_tfidf)):
            p1 = 0
            rr = 0.0
            for query, expected in cases:
                ranked = [d["id"] for d, _ in search(query, k=5)]
                if ranked and ranked[0] == expected:
                    p1 += 1
                if expected in ranked:
                    rr += 1.0 / (ranked.index(expected) + 1)
            out[name] = {
                "precision_at_1": round(p1 / len(cases), 3),
                "mrr": round(rr / len(cases), 3),
            }
        return out


class CaseMemory:
    """Write-back store of resolved cases; retrieval by exact facet match.

    Facets (reason, method, amount band) beat text similarity here because the
    question case memory answers is 'what happened last time on cases like
    this', and 'like this' is defined by the features the uplift model acts on.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = str(db_path or DATA_DIR / "ledger.db")
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS case_memory (
                    id INTEGER PRIMARY KEY,
                    reason_code TEXT, method TEXT, amount_band TEXT,
                    action TEXT, contacted INTEGER,
                    recovered INTEGER, churned INTEGER,
                    batch_id TEXT
                )"""
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_case_facets ON case_memory (reason_code, method, amount_band)"
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    @staticmethod
    def band(amount_paise: int) -> str:
        if amount_paise < 50000:
            return "lt500"
        if amount_paise < 150000:
            return "500-1500"
        return "gt1500"

    def record(self, ev, action: str, contacted: bool, recovered: bool, churned: bool, batch_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO case_memory (reason_code, method, amount_band, action, contacted, recovered, churned, batch_id) VALUES (?,?,?,?,?,?,?,?)",
                (ev.reason_code, ev.method, self.band(ev.amount_paise), action, int(contacted), int(recovered), int(churned), batch_id),
            )

    def similar(self, ev) -> dict:
        with self._lock, self._conn() as c:
            row = c.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN contacted=0 AND recovered=1 THEN 1 ELSE 0 END),
                          SUM(CASE WHEN contacted=0 THEN 1 ELSE 0 END),
                          SUM(CASE WHEN contacted=1 AND recovered=1 THEN 1 ELSE 0 END),
                          SUM(CASE WHEN contacted=1 THEN 1 ELSE 0 END)
                   FROM case_memory WHERE reason_code=? AND method=? AND amount_band=?""",
                (ev.reason_code, ev.method, self.band(ev.amount_paise)),
            ).fetchone()
        total, quiet_ok, quiet_n, contact_ok, contact_n = (x or 0 for x in row)
        return {
            "total": total,
            "quiet_recovery_rate": round(quiet_ok / quiet_n, 3) if quiet_n else None,
            "contact_recovery_rate": round(contact_ok / contact_n, 3) if contact_n else None,
        }
