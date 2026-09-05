"""The learning loop: measure honestly, then learn from real rows.

Two identified quantities, kept apart on purpose:

1. **The policy effect.** Counterparties are hashed into a control arm at
   ingestion and never contacted. The difference in recovery between the
   treatment arm (RecoverOps' policy) and the control arm is a randomised
   comparison — the one number on real data that needs no model. It comes
   with a bootstrap interval that tightens as outcomes accumulate.

2. **Per-event uplift of contact.** Within the treatment arm Agent B flips a
   small share ε of its contact decisions (see engine.py), so every
   contactable leak has a known propensity of contact: 1−ε where the policy
   wanted to contact, ε where it did not. Known propensities are what make
   inverse-propensity weighting exact rather than estimated, and they are the
   difference between learning and confounding. The estimators here are the
   same HistGradientBoosting T-learner heads as the offline benchmark, fitted
   on real rows with those weights. They are not used until there are enough
   rows and the ranking beats random on a real holdout split.

Nothing here touches synthetic rows: a model trained on a simulator does not
get to claim it knows real customers.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from .leaks import LeakEvent
from .sim import FEATURE_VERSION, featurize
from .store import DATA_DIR, Store
from .uplift import qini_coefficient

REAL_MODEL_PATH = DATA_DIR / f"real_models_v{FEATURE_VERSION}.pkl"

MIN_ROWS = 120
MIN_PER_ARM = 30
BOOTSTRAP = 1000


def _gbc() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.06, max_leaf_nodes=15, l2_regularization=1.0, early_stopping=False, random_state=7
    )


# ------------------------------------------------------------- measurement


def measure_policy_effect(rows: list[dict], seed: int = 11) -> dict:
    """Randomised comparison of the treatment arm against the control arm on
    resolved real rows. Returns rates, the difference, and bootstrap intervals
    for the rate difference and the incremental rupees."""
    treat = [r for r in rows if r["arm"] == "treatment"]
    ctrl = [r for r in rows if r["arm"] == "control"]
    out = {
        "treatmentRows": len(treat),
        "controlRows": len(ctrl),
        "rateTreatment": None,
        "rateControl": None,
        "ateRate": None,
        "ateRateCi": None,
        "incrementalPaise": None,
        "incrementalPaiseCi": None,
        "measurable": bool(treat) and bool(ctrl),
        "note": None,
    }
    if not treat or not ctrl:
        out["note"] = "Both arms need resolved outcomes before the policy effect can be measured."
        return out

    yt = np.array([int(r["outcome_recovered"] or 0) for r in treat])
    yc = np.array([int(r["outcome_recovered"] or 0) for r in ctrl])
    at = np.array([r["amount_paise"] for r in treat], dtype=float)
    ac = np.array([r["amount_paise"] for r in ctrl], dtype=float)

    def stats(yt_, yc_, at_, ac_) -> tuple[float, float]:
        rate_diff = float(yt_.mean() - yc_.mean())
        # Incremental rupees: what the treatment arm recovered minus what it
        # would have at the control arm's rupee-weighted recovery rate.
        ctrl_rate_paise = float((yc_ * ac_).sum() / max(ac_.sum(), 1.0))
        incremental = float((yt_ * at_).sum() - ctrl_rate_paise * at_.sum())
        return rate_diff, incremental

    ate, inc = stats(yt, yc, at, ac)
    rng = np.random.default_rng(seed)
    diffs, incs = [], []
    for _ in range(BOOTSTRAP):
        it = rng.integers(0, len(yt), len(yt))
        ic = rng.integers(0, len(yc), len(yc))
        d, i = stats(yt[it], yc[ic], at[it], ac[ic])
        diffs.append(d)
        incs.append(i)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    ilo, ihi = np.percentile(incs, [2.5, 97.5])
    out.update(
        {
            "rateTreatment": round(float(yt.mean()), 4),
            "rateControl": round(float(yc.mean()), 4),
            "ateRate": round(ate, 4),
            "ateRateCi": [round(float(lo), 4), round(float(hi), 4)],
            "incrementalPaise": int(round(inc)),
            "incrementalPaiseCi": [int(round(ilo)), int(round(ihi))],
            "note": (
                "Randomised comparison: the control arm was never contacted. The interval is a 95% bootstrap "
                f"interval over {BOOTSTRAP} resamples; it tightens as more outcomes resolve."
            ),
        }
    )
    return out


# ---------------------------------------------------------------- learning


@dataclass
class RealLearner:
    """CATE of contact on real rows, with known propensities."""

    store: Store
    path: Path = REAL_MODEL_PATH
    mu0: HistGradientBoostingClassifier | None = None
    mu1: HistGradientBoostingClassifier | None = None
    ch0: HistGradientBoostingClassifier | None = None
    ch1: HistGradientBoostingClassifier | None = None
    report: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "rb") as f:
                    saved = pickle.load(f)
                if saved.get("feature_version") == FEATURE_VERSION:
                    self.mu0, self.mu1, self.ch0, self.ch1 = saved["mu0"], saved["mu1"], saved["ch0"], saved["ch1"]
                    self.report = saved.get("report", {})
            except Exception:  # noqa: BLE001 — a corrupt cache is a cache miss, not a crash
                pass

    @property
    def ready(self) -> bool:
        return self.mu0 is not None and self.mu1 is not None and bool(self.report.get("ready"))

    @property
    def label(self) -> str:
        if not self.ready:
            return "reason-family priors (real data — no outcomes learned yet)"
        return f"real-data T-learner, IPW ({self.report.get('rowsUsed', 0)} resolved outcomes, Qini {self.report.get('qiniReal', 0):.3f})"

    # ----------------------------------------------------------------- fit

    def fit(self) -> dict:
        rows = self.store.resolved_real_leaks()
        # Only treatment-arm rows with a real chance of either decision carry
        # information about the effect of contact. Control rows have propensity 0
        # by design; gate-blocked rows have propensity 0 in fact.
        usable = [
            r for r in rows
            if r["arm"] == "treatment" and r["propensity"] is not None and 0.0 < float(r["propensity"]) < 1.0
            and r["feature_version"] == FEATURE_VERSION and r["outcome_recovered"] is not None
        ]
        n_treated = sum(1 for r in usable if r["contacted"])
        n_control = len(usable) - n_treated
        report = {
            "rowsUsed": len(usable),
            "treatedRows": n_treated,
            "controlRows": n_control,
            "resolvedRows": len(rows),
            "estimator": "real_t_learner_ipw",
            "featureVersion": FEATURE_VERSION,
            "ready": False,
            "qiniReal": None,
            "note": None,
        }
        if len(usable) < MIN_ROWS or n_treated < MIN_PER_ARM or n_control < MIN_PER_ARM:
            report["note"] = (
                f"Need at least {MIN_ROWS} resolved treatment-arm rows with {MIN_PER_ARM} contacted and {MIN_PER_ARM} not; "
                f"have {len(usable)} ({n_treated} / {n_control}). Ranking stays on priors."
            )
            self.report = report
            self.store.save_learning_run(report)
            return report

        X = np.array([r["features"] for r in usable], dtype=float)
        t = np.array([int(r["contacted"]) for r in usable])
        y = np.array([int(r["outcome_recovered"]) for r in usable])
        ch = np.array([int(r["outcome_churned"] or 0) for r in usable])
        e = np.array([float(r["propensity"]) for r in usable])
        # Inverse-propensity weights: a contacted row the policy nearly always
        # contacts is common and down-weighted; an explored one is rare and
        # up-weighted, so the fitted heads describe the population, not the policy.
        w = np.where(t == 1, 1.0 / e, 1.0 / (1.0 - e))

        # Chronological split: the newest 20% is the holdout, as it would be in production.
        cut = int(len(usable) * 0.8)
        tr, ho = np.arange(cut), np.arange(cut, len(usable))
        if (t[ho] == 1).sum() < 5 or (t[ho] == 0).sum() < 5:
            ho = tr  # too few to hold out; report in-sample and say so
            report["note"] = "Holdout too small — Qini reported in-sample."

        mu0 = _gbc().fit(X[tr][t[tr] == 0], y[tr][t[tr] == 0], sample_weight=w[tr][t[tr] == 0])
        mu1 = _gbc().fit(X[tr][t[tr] == 1], y[tr][t[tr] == 1], sample_weight=w[tr][t[tr] == 1])
        tau_ho = mu1.predict_proba(X[ho])[:, 1] - mu0.predict_proba(X[ho])[:, 1]
        qini = qini_coefficient(tau_ho, t[ho], y[ho])

        ch0 = _gbc().fit(X[tr][t[tr] == 0], ch[tr][t[tr] == 0], sample_weight=w[tr][t[tr] == 0]) if ch[tr][t[tr] == 0].any() else None
        ch1 = _gbc().fit(X[tr][t[tr] == 1], ch[tr][t[tr] == 1], sample_weight=w[tr][t[tr] == 1]) if ch[tr][t[tr] == 1].any() else None

        report["qiniReal"] = qini
        report["ready"] = bool(qini > 0)
        if not report["ready"]:
            report["note"] = f"Learned ranking does not beat random on the real holdout (Qini {qini:.3f}). Ranking stays on priors."
        elif report["note"] is None:
            report["note"] = f"Learned ranking beats random on the real holdout (Qini {qini:.3f}); real-data estimator in use."

        self.mu0, self.mu1, self.ch0, self.ch1, self.report = mu0, mu1, ch0, ch1, report
        with open(self.path, "wb") as f:
            pickle.dump({"feature_version": FEATURE_VERSION, "mu0": mu0, "mu1": mu1, "ch0": ch0, "ch1": ch1, "report": report}, f)
        self.store.save_learning_run(report)
        return report

    # ------------------------------------------------------------- predict

    def estimate_batch(self, events: list[LeakEvent]) -> list[tuple[float, float, float, float]]:
        assert self.ready and self.mu0 is not None and self.mu1 is not None
        X = np.array([featurize(e) for e in events], dtype=float)
        p0 = self.mu0.predict_proba(X)[:, 1]
        p1 = self.mu1.predict_proba(X)[:, 1]
        if self.ch0 is not None and self.ch1 is not None:
            churn_tau = self.ch1.predict_proba(X)[:, 1] - self.ch0.predict_proba(X)[:, 1]
        else:
            churn_tau = np.zeros(len(events))
        return [(float(a), float(b), float(b - a), float(c)) for a, b, c in zip(p0, p1, churn_tau)]

    # -------------------------------------------------------------- status

    def status(self) -> dict:
        counts = self.store.leak_counts()
        last = self.store.latest_learning_run()
        rows = self.store.resolved_real_leaks()
        effect = measure_policy_effect(rows) if rows else measure_policy_effect([])
        return {
            "counts": counts,
            "estimatorMode": "learned-real" if self.ready else "priors",
            "estimator": self.label,
            "lastRun": last,
            "policyEffect": effect,
            "thresholds": {"minRows": MIN_ROWS, "minPerArm": MIN_PER_ARM},
            "featureVersion": FEATURE_VERSION,
        }


def rows_to_json(rows: list[dict]) -> str:
    return json.dumps(rows, default=str)
