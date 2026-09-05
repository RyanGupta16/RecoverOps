"""Uplift engine: real CATE estimation, benchmarked.

The quantity being estimated is tau(x) = P(recover | contact, x) - P(recover |
no contact, x). Four estimators compete:

- T-learner: two independent GradientBoostingClassifiers, one per treatment
  arm; tau = mu1(x) - mu0(x). Simple, unbiased under randomised training data,
  but each head only sees half the data.
- S-learner: one model with treatment as a feature; tau = f(x,1) - f(x,0).
  Data-efficient but regularisation shrinks the treatment effect toward zero
  (the tree can ignore the treatment flag).
- X-learner: T-learner first stage, then imputed individual effects are
  regressed per arm and blended by propensity (0.5 here — randomised).
  Usually wins when arms are imbalanced; with a 50/50 split it mostly matches T.
- DR-learner: doubly-robust pseudo-outcome psi = mu1 - mu0
  + t(y - mu1)/e - (1-t)(y - mu0)/(1-e), regressed on x. Unbiased if EITHER
  the outcome heads or the propensity is right; with e = 0.5 known exactly,
  the propensity half is free, so this targets tau directly instead of
  differencing two noisy probability heads.
- Segment priors: no learning. Expected tau from the reason-code prior over
  segments, engagement-adjusted. This is the documented fallback and the
  honesty floor: if learners cannot beat it, ship the table.

Training data is a simulated randomised experiment (treatment assigned by coin
flip), because that is the only data uplift models are identified on. Metrics:

- PEHE: sqrt(mean((tau_hat - tau_true)^2)) — available only because the world
  is synthetic and tau_true is known.
- Qini coefficient: area between the cumulative-uplift curve of the model's
  ranking and random targeting, computed from realised outcomes on a held-out
  randomised split — the estimator you could still compute on live data.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from .sim import FEATURE_VERSION, REASONS, SEGMENT_TRUTH, SEGMENTS, Event, featurize, generate_events, true_uplift

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Keyed on the feature vector's shape: a new feature means a new cache, never a
# model silently scoring inputs it was not trained on.
MODEL_CACHE = DATA_DIR / f"uplift_models_v{FEATURE_VERSION}.pkl"
BENCH_PATH = DATA_DIR / "uplift_benchmark.json"

TRAIN_SEED = 777
TRAIN_N = 100_000
HOLDOUT_N = 20_000


# Histogram-based gradient boosting: bins features once, then grows trees on the
# bins — an order of magnitude faster than classic GradientBoosting at this row
# count, which is what makes a 100k-row experiment affordable. Early stopping on
# an internal validation split picks the effective tree count.
def _gbc() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=500, learning_rate=0.06, max_leaf_nodes=31, l2_regularization=1.0, early_stopping=True, random_state=7
    )


def _gbr() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_iter=500, learning_rate=0.06, max_leaf_nodes=31, l2_regularization=1.0, early_stopping=True, random_state=7
    )


def _simulate_experiment(events: list[Event], rng: np.random.Generator):
    """Randomised treatment + realised outcomes (recovery AND churn) per event."""
    X = np.array([featurize(e) for e in events])
    t = rng.integers(0, 2, len(events))
    p = np.array([e.truth[1] if ti else e.truth[0] for e, ti in zip(events, t)])
    y = (rng.random(len(events)) < p).astype(int)
    pc = np.array([e.truth[3] if ti else e.truth[2] for e, ti in zip(events, t)])
    churn = ((y == 0) & (rng.random(len(events)) < pc)).astype(int)
    tau = np.array([true_uplift(e) for e in events])
    return X, t, y, churn, tau


@dataclass
class Learners:
    t_mu0: HistGradientBoostingClassifier
    t_mu1: HistGradientBoostingClassifier
    s_model: HistGradientBoostingClassifier
    x_tau0: HistGradientBoostingRegressor
    x_tau1: HistGradientBoostingRegressor
    dr_model: HistGradientBoostingRegressor
    # Churn-uplift heads: contact's causal effect on cancellation. This is where
    # the sleeping dog actually shows up — its recovery uplift is only mildly
    # negative, but its churn uplift is large and positive, while a persuadable's
    # churn uplift is NEGATIVE (a well-timed nudge retains). Ranking on recovery
    # uplift alone cannot separate the two; the churn head can.
    ch_mu0: HistGradientBoostingClassifier
    ch_mu1: HistGradientBoostingClassifier

    def predict_churn_tau(self, X: np.ndarray) -> np.ndarray:
        return self.ch_mu1.predict_proba(X)[:, 1] - self.ch_mu0.predict_proba(X)[:, 1]

    def predict(self, kind: str, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Returns (p_control, p_treat, tau) for the requested estimator."""
        if kind == "t_learner":
            p0 = self.t_mu0.predict_proba(X)[:, 1]
            p1 = self.t_mu1.predict_proba(X)[:, 1]
            return p0, p1, p1 - p0
        if kind == "s_learner":
            p0 = self.s_model.predict_proba(np.c_[X, np.zeros(len(X))])[:, 1]
            p1 = self.s_model.predict_proba(np.c_[X, np.ones(len(X))])[:, 1]
            return p0, p1, p1 - p0
        if kind == "x_learner":
            # Propensity is 0.5 by construction (randomised assignment).
            p0 = self.t_mu0.predict_proba(X)[:, 1]
            p1 = self.t_mu1.predict_proba(X)[:, 1]
            tau = 0.5 * self.x_tau0.predict(X) + 0.5 * self.x_tau1.predict(X)
            return p0, p1, tau
        if kind == "dr_learner":
            p0 = self.t_mu0.predict_proba(X)[:, 1]
            p1 = self.t_mu1.predict_proba(X)[:, 1]
            return p0, p1, self.dr_model.predict(X)
        raise ValueError(kind)


def prior_tau(events: list[Event], outcome: str = "recover") -> np.ndarray:
    """Hand-specified fallback: E[tau | reason prior, engagement shift]."""
    idx = (1, 0) if outcome == "recover" else (3, 2)
    seg_tau = {s: SEGMENT_TRUTH[s][idx[0]] - SEGMENT_TRUTH[s][idx[1]] for s in SEGMENTS}
    by_code = {r[0]: r[4] for r in REASONS}
    out = []
    for ev in events:
        p = list(by_code[ev.reason_code])
        shift = float(np.clip((0.5 - ev.engagement) * 0.34, -0.14, 0.16))
        if shift > 0:
            moved = min(shift, p[1] * 0.6)
            p[1] -= moved
            p[3] += moved
        else:
            moved = min(-shift, p[3] * 0.6)
            p[3] -= moved
            p[1] += moved
        out.append(sum(pi * seg_tau[s] for pi, s in zip(p, SEGMENTS)))
    return np.array(out)


def qini_coefficient(tau_hat: np.ndarray, t: np.ndarray, y: np.ndarray) -> float:
    """Area between the model's cumulative uplift curve and random targeting,
    normalised by the area the perfect ranking would achieve on this split."""
    order = np.argsort(-tau_hat)
    t_o, y_o = t[order], y[order]
    n = len(y_o)
    ct = np.cumsum(t_o)
    cc = np.cumsum(1 - t_o)
    rt = np.cumsum(y_o * t_o)
    rc = np.cumsum(y_o * (1 - t_o))
    with np.errstate(divide="ignore", invalid="ignore"):
        uplift_curve = np.where(ct > 0, rt / ct, 0) - np.where(cc > 0, rc / cc, 0)
    x = np.arange(1, n + 1) / n
    overall = uplift_curve[-1]
    auuc_model = float(np.trapezoid(uplift_curve, x))
    auuc_random = overall * 0.5
    return round(auuc_model - auuc_random, 5)


def train_and_benchmark(force: bool = False) -> tuple[Learners, dict]:
    if MODEL_CACHE.exists() and BENCH_PATH.exists() and not force:
        with open(MODEL_CACHE, "rb") as f:
            learners = pickle.load(f)
        bench = json.loads(BENCH_PATH.read_text())
        if bench.get("feature_version") == FEATURE_VERSION:
            return learners, bench

    rng = np.random.default_rng(TRAIN_SEED)
    events = generate_events(TRAIN_SEED, TRAIN_N + HOLDOUT_N)
    X, t, y, churn, tau_true = _simulate_experiment(events, rng)
    Xtr, ttr, ytr, chtr = X[:TRAIN_N], t[:TRAIN_N], y[:TRAIN_N], churn[:TRAIN_N]
    Xho, tho, yho, tauho = X[TRAIN_N:], t[TRAIN_N:], y[TRAIN_N:], tau_true[TRAIN_N:]
    holdout_events = events[TRAIN_N:]

    t_mu0 = _gbc().fit(Xtr[ttr == 0], ytr[ttr == 0])
    t_mu1 = _gbc().fit(Xtr[ttr == 1], ytr[ttr == 1])
    s_model = _gbc().fit(np.c_[Xtr, ttr], ytr)

    # X-learner second stage: impute individual effects, regress per arm.
    d1 = ytr[ttr == 1] - t_mu0.predict_proba(Xtr[ttr == 1])[:, 1]
    d0 = t_mu1.predict_proba(Xtr[ttr == 0])[:, 1] - ytr[ttr == 0]
    x_tau1 = _gbr().fit(Xtr[ttr == 1], d1)
    x_tau0 = _gbr().fit(Xtr[ttr == 0], d0)

    # DR-learner: pseudo-outcome with the known propensity e = 0.5, nuisances
    # cross-fitted (2-fold) so the pseudo-outcome never uses a mu-hat that was
    # fitted on the same rows — the standard guard against nuisance overfit
    # leaking into the effect regression.
    psi = np.zeros(TRAIN_N)
    half = TRAIN_N // 2
    for fit_idx, pred_idx in ((np.arange(half), np.arange(half, TRAIN_N)), (np.arange(half, TRAIN_N), np.arange(half))):
        m0 = _gbc().fit(Xtr[fit_idx][ttr[fit_idx] == 0], ytr[fit_idx][ttr[fit_idx] == 0])
        m1 = _gbc().fit(Xtr[fit_idx][ttr[fit_idx] == 1], ytr[fit_idx][ttr[fit_idx] == 1])
        mu0p = m0.predict_proba(Xtr[pred_idx])[:, 1]
        mu1p = m1.predict_proba(Xtr[pred_idx])[:, 1]
        tp, yp = ttr[pred_idx], ytr[pred_idx]
        psi[pred_idx] = mu1p - mu0p + tp * (yp - mu1p) / 0.5 - (1 - tp) * (yp - mu0p) / 0.5
    dr_model = _gbr().fit(Xtr, psi)

    ch_mu0 = _gbc().fit(Xtr[ttr == 0], chtr[ttr == 0])
    ch_mu1 = _gbc().fit(Xtr[ttr == 1], chtr[ttr == 1])

    learners = Learners(t_mu0, t_mu1, s_model, x_tau0, x_tau1, dr_model, ch_mu0, ch_mu1)

    bench: dict = {"train_n": TRAIN_N, "holdout_n": HOLDOUT_N, "feature_version": FEATURE_VERSION, "estimators": {}}
    candidates = {
        "t_learner": learners.predict("t_learner", Xho)[2],
        "s_learner": learners.predict("s_learner", Xho)[2],
        "x_learner": learners.predict("x_learner", Xho)[2],
        "dr_learner": learners.predict("dr_learner", Xho)[2],
        "segment_priors": prior_tau(holdout_events),
    }
    for name, tau_hat in candidates.items():
        bench["estimators"][name] = {
            "pehe": round(float(np.sqrt(np.mean((tau_hat - tauho) ** 2))), 5),
            "qini": qini_coefficient(tau_hat, tho, yho),
            "corr_with_truth": round(float(np.corrcoef(tau_hat, tauho)[0, 1]), 4),
        }

    ranked = sorted(bench["estimators"].items(), key=lambda kv: (-kv[1]["qini"], kv[1]["pehe"]))
    bench["winner"] = ranked[0][0]
    bench["ranking"] = [name for name, _ in ranked]

    with open(MODEL_CACHE, "wb") as f:
        pickle.dump(learners, f)
    BENCH_PATH.write_text(json.dumps(bench, indent=2))
    return learners, bench


class UpliftEngine:
    def __init__(self, estimator: str | None = None, force_retrain: bool = False) -> None:
        self.learners, self.benchmark = train_and_benchmark(force=force_retrain)
        self.estimator = estimator or self.benchmark["winner"]
        self.label = {
            "t_learner": "T-learner (two GradientBoosting heads)",
            "s_learner": "S-learner (single model, treatment feature)",
            "x_learner": "X-learner (imputed-effect second stage)",
            "dr_learner": "DR-learner (doubly-robust pseudo-outcome)",
            "segment_priors": "hand-specified segment priors (fallback)",
        }[self.estimator]

    def estimate(self, ev: Event) -> tuple[float, float, float, float]:
        """(p_control_hat, p_treat_hat, tau_hat, churn_tau_hat) for one event."""
        return self.estimate_batch([ev])[0]

    def estimate_batch(self, events: list[Event]) -> list[tuple[float, float, float, float]]:
        """One vectorised pass over the whole batch — per-event predict calls
        pay sklearn's dispatch overhead 2000 times and turn a 50 ms batch into
        seconds, which is the difference between the frontend's run-request
        timeout holding or tripping."""
        if self.estimator == "segment_priors":
            taus = prior_tau(events)
            churn_taus = prior_tau(events, outcome="churn")
            out = []
            for tau, ct in zip(taus, churn_taus):
                # Priors carry no absolute levels; anchor on the reason-blended base rate.
                p0 = float(np.clip(0.35 + tau * -0.1, 0.01, 0.99))
                out.append((p0, float(np.clip(p0 + tau, 0.01, 0.99)), float(tau), float(ct)))
            return out
        X = np.array([featurize(ev) for ev in events])
        p0, p1, tau = self.learners.predict(self.estimator, X)
        churn_tau = self.learners.predict_churn_tau(X)
        return [(float(a), float(b), float(c), float(d)) for a, b, c, d in zip(p0, p1, tau, churn_tau)]
