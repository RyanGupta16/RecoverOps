"""Batch engine: runs the seven-layer pipeline over a batch of LeakEvents.

Output shape is the contract with src/lib/types.ts (camelCase). The same
engine runs synthetic leaks — both branches known, so the comparison is exact —
and real leaks pulled from Razorpay or a file, where no outcome is known at
decision time. The two modes differ in exactly the places ground truth is
needed, and the batch says which mode it is in (``dataMode``), so every number
on screen can be read correctly.

Estimates on synthetic leaks come from the trained CATE estimator that won the
offline benchmark. On real leaks the ranking runs on reason-family priors — the
honesty floor — until the learning loop (phase 2) has enough real (features,
treated, outcome) rows to retrain. A model trained on a simulator does not get
to claim it knows real customers.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

import numpy as np

from .diagnosis import Diagnoser
from .executor import Executor
from .leaks import LeakEvent
from .merchant import MerchantConfig
from .policy import ACTION_LABELS, CONTACT_ACTIONS, classify_message, evaluate_gate, preferred_contact_action
from .retrieval import CaseMemory, Corpus
from .sim import FEATURE_VERSION, SEGMENTS, featurize
from .store import now_iso
from .uplift import UpliftEngine, prior_tau

CANDIDATE_ACTIONS = ["silent_retry", "payment_link_sms", "payment_link_whatsapp", "card_update_request", "incentive_link"]

ESCALATE_BLOCK = {
    "ruleId": "ESCALATE_UNRESOLVED",
    "verdict": "BLOCK",
    "note": "Action ladder exhausted. Routed to the human queue with a structured reason.",
    "citation": None,
}
ESCALATE_NA = {"ruleId": "ESCALATE_UNRESOLVED", "verdict": "N/A", "note": "Action ladder has not been exhausted.", "citation": None}


class Decision(dict):
    """Per-event, per-agent decision record. Plain dict + attribute sugar."""

    __getattr__ = dict.__getitem__


# ------------------------------------------------------------------ estimates


def _estimates(events: list[LeakEvent], uplift: UpliftEngine, synthetic: bool, real_learner=None) -> tuple[dict, dict, dict, dict, str, str]:
    """(estimates, baseline_scores, uplift_hats, churn_taus, estimator_label, estimator_mode)."""
    if not synthetic and real_learner is not None and real_learner.ready:
        # Real rows with real outcomes, fitted with known propensities: the only
        # model allowed to rank real customers.
        ests = dict(zip((e.event_id for e in events), real_learner.estimate_batch(events)))
        return (
            ests,
            {eid: est[1] for eid, est in ests.items()},
            {eid: est[2] for eid, est in ests.items()},
            {eid: est[3] for eid, est in ests.items()},
            real_learner.label,
            "learned-real",
        )
    if synthetic:
        X = np.array([featurize(e) for e in events])
        baseline_arr = uplift.learners.t_mu1.predict_proba(X)[:, 1]
        ests = dict(zip((e.event_id for e in events), uplift.estimate_batch(events)))
        baseline = {e.event_id: float(s) for e, s in zip(events, baseline_arr)}
        return (
            ests,
            baseline,
            {eid: est[2] for eid, est in ests.items()},
            {eid: est[3] for eid, est in ests.items()},
            uplift.label,
            "learned",
        )

    # Real data: priors only, anchored on a reason-blended base rate.
    taus = prior_tau(events)
    churn_taus = prior_tau(events, outcome="churn")
    ests: dict[str, tuple[float, float, float, float]] = {}
    baseline: dict[str, float] = {}
    for ev, tau, ct in zip(events, taus, churn_taus):
        p0 = float(np.clip(0.35 + tau * -0.1, 0.01, 0.99))
        p1 = float(np.clip(p0 + tau, 0.01, 0.99))
        ests[ev.event_id] = (p0, p1, float(tau), float(ct))
        baseline[ev.event_id] = p1
    return (
        ests,
        baseline,
        {eid: est[2] for eid, est in ests.items()},
        {eid: est[3] for eid, est in ests.items()},
        "reason-family priors (real data — no outcomes learned yet)",
        "priors",
    )


# --------------------------------------------------------------------- policy


def _run_policy(
    events: list[LeakEvent],
    agent: str,
    scores: dict[str, float],
    uplift_hats: dict[str, float],
    wants_contact,
    merchant: MerchantConfig,
    net_values: dict[str, float] | None = None,
    explore_rng: np.random.Generator | None = None,
) -> dict[str, Decision]:
    """Rank, decide, gate. With ``explore_rng`` (Agent B on real data) a share
    ε of contact decisions is flipped at random inside the treatment arm, and
    every contactable leak records its propensity of contact — 1−ε where the
    policy wanted to contact, ε where it did not, 0 where the gate or the
    budget made contact impossible. Known propensities are what let the
    learning loop fit uplift on real outcomes without confounding."""
    ordered = sorted(events, key=lambda e: -scores[e.event_id])
    budget_left = merchant.contact_budget_per_batch
    eps = merchant.exploration_share if explore_rng is not None else 0.0
    decisions: dict[str, Decision] = {}

    for ev in ordered:
        score = scores[ev.event_id]
        tau = uplift_hats[ev.event_id]
        net_value = net_values.get(ev.event_id) if (net_values is not None and agent == "B") else None
        wanted = bool(wants_contact(ev, score)) and not ev.holdout
        explored = False
        propensity: float | None = None

        # Control-arm leaks take the silent path for both agents, so the A/B
        # comparison stays fair and the holdout stays a clean counterfactual.
        if ev.holdout:
            intended = "silent_retry"
            propensity = 0.0
        elif budget_left <= 0:
            intended = "silent_retry"
            propensity = 0.0
        else:
            preferred = preferred_contact_action(ev)
            if eps > 0:
                # Is contact even possible for this leak? The gate is pure, so
                # ask it once; a deterministic block means propensity 0.
                allowed = not evaluate_gate(ev, preferred, agent, tau, merchant, net_value_paise=None).blocked
                if not allowed:
                    propensity = 0.0
                    do_contact = False
                else:
                    propensity = (1.0 - eps) if wanted else eps
                    explored = bool(explore_rng.random() < eps)
                    do_contact = wanted != explored
            else:
                propensity = 1.0 if wanted else 0.0
                do_contact = wanted
            intended = preferred if do_contact else "silent_retry"
        result = evaluate_gate(ev, intended, agent, tau, merchant, net_value_paise=net_value)
        action = intended

        # Blocked outreach falls down the ladder to a silent retry; if that is
        # blocked too, the case goes to a human rather than disappearing.
        if result.blocked and intended != "silent_retry":
            fallback = evaluate_gate(ev, "silent_retry", agent, tau, merchant)
            fallback.denied_action, fallback.denied_by = intended, result.blocked_by
            if fallback.blocked:
                action = "escalate"
                fallback.gate.append(dict(ESCALATE_BLOCK))
                fallback.escalated = True
            else:
                action = "silent_retry"
            result = fallback
        elif result.blocked:
            action = "escalate"
            result.gate.append(dict(ESCALATE_BLOCK))
            result.escalated = True
            result.denied_action, result.denied_by = intended, result.blocked_by
        else:
            result.gate.append(dict(ESCALATE_NA))

        contacted = action in CONTACT_ACTIONS
        if contacted:
            budget_left -= 1
        elif propensity not in (None, 0.0) and intended in CONTACT_ACTIONS:
            # Contact was attempted and the gate stopped it on the intended
            # action's specific terms: no chance of contact for this leak.
            propensity = 0.0
        decisions[ev.event_id] = Decision(
            action=action,
            contacted=contacted,
            score=score,
            gate=result.gate,
            blockedBy=result.blocked_by,
            messageClass=result.message_class if contacted else classify_message(ev, action)[0],
            deniedAction=result.denied_action,
            deniedBy=result.denied_by,
            costPaise=merchant.cost_for(action, result.message_class),
            wanted=wanted,
            explored=explored,
            propensity=propensity,
            outcome=None,
        )
    return decisions


# ------------------------------------------------------------------- outcomes


def _realize(events: list[LeakEvent], decisions: dict[str, Decision], merchant: MerchantConfig, synthetic: bool) -> dict:
    m = dict.fromkeys(
        (
            "contactsMade silentRetries escalations recoveredCount recoveredPaise sleepingDogsTouched "
            "wastedContacts outreachDrivenRecoveries outreachCausedCancellations outreachCausedChurnLossPaise "
            "churnedSubscriptions contactCostPaise outcomesPending holdoutEvents exploredDecisions"
        ).split(),
        0,
    )
    for ev in events:
        d = decisions[ev.event_id]
        if ev.holdout:
            m["holdoutEvents"] += 1
        if d.get("explored"):
            m["exploredDecisions"] += 1
        if d["contacted"]:
            m["contactsMade"] += 1
            m["contactCostPaise"] += d["costPaise"]
        if d["action"] == "silent_retry":
            m["silentRetries"] += 1
        if d["action"] == "escalate":
            m["escalations"] += 1

        if not synthetic:
            # Nothing is known yet. The learning loop attributes outcomes as
            # they arrive; until then the row says so instead of guessing.
            d["outcome"] = None
            m["outcomesPending"] += 1
            continue

        p0, p1, c0, c1 = ev.truth  # type: ignore[misc]
        p_recover = p1 if d["contacted"] else p0
        p_churn = c1 if d["contacted"] else c0
        recovered = ev.u_recover < p_recover  # type: ignore[operator]
        did_churn = (not recovered) and ev.u_churn < p_churn  # type: ignore[operator]

        if d["contacted"]:
            if ev.segment == "sleeping_dog":
                m["sleepingDogsTouched"] += 1
            # Both branches are known — the counterfactual is directly observable.
            recovered_if_quiet = ev.u_recover < p0  # type: ignore[operator]
            churned_if_quiet = (not recovered_if_quiet) and ev.u_churn < c0  # type: ignore[operator]
            if recovered == recovered_if_quiet:
                m["wastedContacts"] += 1
            if recovered and not recovered_if_quiet:
                m["outreachDrivenRecoveries"] += 1
            if did_churn and not churned_if_quiet:
                m["outreachCausedCancellations"] += 1
                m["outreachCausedChurnLossPaise"] += ev.amount_paise * merchant.churn_residual_cycles

        if recovered:
            m["recoveredCount"] += 1
            m["recoveredPaise"] += ev.amount_paise
        if did_churn:
            m["churnedSubscriptions"] += 1
        d["outcome"] = {"recovered": bool(recovered), "churned": bool(did_churn)}

    m["eventsProcessed"] = len(events)
    m["contactBudget"] = merchant.contact_budget_per_batch
    m["recoveryRate"] = round(m["recoveredCount"] / len(events), 4) if events else 0.0
    m["netValuePaise"] = m["recoveredPaise"] - m["contactCostPaise"] - m["outreachCausedChurnLossPaise"]
    return m


def _uplift_curve(events: list[LeakEvent], scores: dict[str, float], merchant: MerchantConfig, cost_of: dict[str, int]) -> list[dict]:
    ordered = sorted(events, key=lambda e: -scores[e.event_id])
    points = [{"contacts": 0, "incrementalRecoveries": 0, "incrementalPaise": 0, "netPaise": 0}]
    recoveries = paise = cost = churn_cost = 0
    for i, ev in enumerate(ordered):
        p0, p1, c0, c1 = ev.truth  # type: ignore[misc]
        y_treat = 1 if ev.u_recover < p1 else 0  # type: ignore[operator]
        y_control = 1 if ev.u_recover < p0 else 0  # type: ignore[operator]
        c_treat = 1 if (not y_treat and ev.u_churn < c1) else 0  # type: ignore[operator]
        c_control = 1 if (not y_control and ev.u_churn < c0) else 0  # type: ignore[operator]
        recoveries += y_treat - y_control
        paise += (y_treat - y_control) * ev.amount_paise
        cost += cost_of[ev.event_id]
        churn_cost += (c_treat - c_control) * ev.amount_paise * merchant.churn_residual_cycles
        if (i + 1) % 10 == 0 or i == len(ordered) - 1:
            points.append(
                {
                    "contacts": i + 1,
                    "incrementalRecoveries": recoveries,
                    "incrementalPaise": paise,
                    "netPaise": paise - cost - churn_cost,
                }
            )
    return points


def _segment_breakdown(events: list[LeakEvent], decisions: dict[str, Decision], contacts_made: int) -> list[dict]:
    rows = []
    for segment in SEGMENTS:
        in_seg = [e for e in events if e.segment == segment]
        contacted = sum(1 for e in in_seg if decisions[e.event_id]["contacted"])
        true_uplift = sum(e.truth[1] - e.truth[0] for e in in_seg) / max(len(in_seg), 1)  # type: ignore[index]
        rows.append(
            {
                "segment": segment,
                "population": len(in_seg),
                "contacted": contacted,
                "shareOfBudget": round(contacted / contacts_made, 4) if contacts_made else 0,
                "trueUplift": round(true_uplift, 4),
            }
        )
    return rows


# ------------------------------------------------------------------ retrieval


def _precedents(ev: LeakEvent, corpus: Corpus, memory: CaseMemory) -> list[dict]:
    out = []
    doc = corpus.by_code(ev.reason_code)
    if doc:
        out.append(
            {
                "source": "razorpay-error-corpus",
                "ref": f"{doc['id']} · error.reason → {ev.reason_code}",
                "note": f"{doc['title']}. Failure attributed {ev.failure_side}-side. Documented actions: {', '.join(doc.get('actions', [])) or 'none'}.",
            }
        )
    elif ev.raw_reason:
        out.append(
            {
                "source": "razorpay-error-taxonomy",
                "ref": f"error_reason {ev.raw_reason} → {ev.reason_code}",
                "note": f"{ev.reason_label}. Attributed {ev.failure_side}-side with {ev.reason_confidence} confidence from Razorpay's error fields.",
            }
        )
    stats = memory.similar(ev)
    band = memory.band(ev.amount_paise)
    if stats["total"]:
        quiet = f"{stats['quiet_recovery_rate']:.0%} quiet-recovery" if stats["quiet_recovery_rate"] is not None else "no quiet branch yet"
        contact = f"{stats['contact_recovery_rate']:.0%} after outreach" if stats["contact_recovery_rate"] is not None else "no outreach branch yet"
        note = f"{stats['total']} similar prior cases in memory ({quiet}, {contact})."
    else:
        note = "No similar prior cases in memory yet — first batch touching this facet."
    out.append({"source": "case-memory", "ref": f"{ev.reason_code} · {ev.method} · {band} band", "note": note})
    if ev.features_are_proxies:
        out.append(
            {
                "source": "feature-provenance",
                "ref": "engagement · tenure · history",
                "note": "Real data: engagement and tenure are estimated from the payment history in the pulled window, and contact history comes from this ledger only. The ranking treats them as proxies.",
            }
        )
    return out


# ------------------------------------------------------------------- run batch


def run_batch(
    uplift: UpliftEngine,
    corpus: Corpus,
    memory: CaseMemory,
    executor: Executor,
    diagnoser: Diagnoser,
    merchant: MerchantConfig,
    events: list[LeakEvent],
    *,
    source_name: str = "simulator",
    seed: int | None = None,
    batch_id: str | None = None,
    source_meta: dict | None = None,
    real_learner=None,
) -> dict:
    if not events:
        raise ValueError("run_batch needs at least one leak event")
    synthetic = all(ev.is_synthetic for ev in events)
    if not synthetic and any(ev.is_synthetic for ev in events):
        raise ValueError("a batch must be all-synthetic or all-real; mixing would make the comparison meaningless")

    seed = seed if seed is not None else secrets.randbelow(2**31)
    # Second-resolution timestamps collide when two runs land in the same
    # second (a simulator run and a file run, say) and the second silently
    # replaces the first in the ledger. The suffix makes the id unique.
    batch_id = batch_id or f"bat_live_{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{secrets.token_hex(2)}"
    t0 = time.perf_counter()
    executor.start_batch()

    estimates, baseline_scores, uplift_hats, churn_taus, estimator_label, estimator_mode = _estimates(events, uplift, synthetic, real_learner)
    # Exploration only on real data: on synthetic leaks both branches are known,
    # so there is nothing to learn from randomising and it would only dilute
    # the exact comparison.
    explore_rng = np.random.default_rng(seed ^ 0x5EED) if (not synthetic and merchant.exploration_share > 0) else None

    # Agent B ranks by expected net VALUE of the contact: recovery uplift prices
    # what outreach wins, the churn-uplift term prices what it can break, and
    # the channel cost is the real price of the message at the class the gate
    # will assign it — a service-class WhatsApp is 7× cheaper than a marketing one.
    def contact_value(ev: LeakEvent) -> float:
        action = preferred_contact_action(ev)
        msg_class, _ = classify_message(ev, action)
        return (
            uplift_hats[ev.event_id] * ev.amount_paise
            - churn_taus[ev.event_id] * ev.amount_paise * merchant.churn_residual_cycles
            - merchant.cost_for(action, msg_class)
        )

    values = {ev.event_id: contact_value(ev) for ev in events}
    b_wants = {ev.event_id: uplift_hats[ev.event_id] > merchant.uplift_threshold and values[ev.event_id] > 0 for ev in events}

    dec_a = _run_policy(events, "A", baseline_scores, uplift_hats, lambda ev, s: s >= merchant.baseline_probability, merchant)
    dec_b = _run_policy(events, "B", values, uplift_hats, lambda ev, s: b_wants[ev.event_id], merchant, net_values=values, explore_rng=explore_rng)
    metrics_a = _realize(events, dec_a, merchant, synthetic)
    metrics_b = _realize(events, dec_b, merchant, synthetic)

    # Traces: diagnosis (LLM where ambiguous), retrieval precedents, gate, execution.
    traces = {}
    executions: dict[str, dict] = {}
    for ev in events:
        da, db = dec_a[ev.event_id], dec_b[ev.event_id]
        p0_hat, p1_hat, tau_hat, churn_tau_hat = estimates[ev.event_id]
        executions[ev.event_id] = executor.execute(ev, db["action"])
        per_action = []
        for action in CANDIDATE_ACTIONS:
            is_contact = action != "silent_retry"
            msg_class, _ = classify_message(ev, action)
            est_uplift = tau_hat if is_contact else max(0.0, p0_hat * 0.22)
            value = round(values[ev.event_id]) if is_contact else round(est_uplift * ev.amount_paise)
            per_action.append(
                {
                    "action": action,
                    "label": ACTION_LABELS[action],
                    "estimatedUplift": round(est_uplift, 4),
                    "expectedValuePaise": value,
                    "eligible": not (action == "incentive_link" and ev.reason_code != "MANDATE_REVOKED"),
                    "messageClass": msg_class,
                    "costPaise": merchant.cost_for(action, msg_class),
                }
            )
        traces[ev.event_id] = {
            "eventId": ev.event_id,
            "kind": ev.kind,
            "source": ev.source,
            "dataMode": "synthetic" if synthetic else "real",
            "leak": ev.public_row(),
            "diagnosis": diagnoser.diagnose(ev),
            "precedents": _precedents(ev, corpus, memory),
            "uplift": {
                "estimator": estimator_label,
                "estimatorMode": estimator_mode,
                "pControlHat": round(p0_hat, 4),
                "pTreatHat": round(p1_hat, 4),
                "upliftHat": round(tau_hat, 4),
                "churnUpliftHat": round(churn_tau_hat, 4),
                "perAction": per_action,
            },
            "agentB": {
                "chosenAction": db["action"],
                "chosenLabel": ACTION_LABELS[db["action"]],
                "messageClass": db["messageClass"],
                "gate": db["gate"],
                "blockedBy": db["blockedBy"],
                "deniedAction": ACTION_LABELS[db["deniedAction"]] if db["deniedAction"] else None,
                "deniedBy": db["deniedBy"],
                "execution": executions[ev.event_id],
                "outcome": db["outcome"],
                "costPaise": db["costPaise"],
                "arm": "control" if ev.holdout else "treatment",
                "wanted": db["wanted"],
                "explored": db["explored"],
                "propensity": db["propensity"],
            },
            "agentA": {
                "chosenAction": da["action"],
                "chosenLabel": ACTION_LABELS[da["action"]],
                "score": round(da["score"], 4),
                "outcome": da["outcome"],
            },
            "truth": (
                {
                    "segment": ev.segment,
                    "pControl": ev.truth[0],  # type: ignore[index]
                    "pTreat": ev.truth[1],  # type: ignore[index]
                    "churnControl": ev.truth[2],  # type: ignore[index]
                    "churnTreat": ev.truth[3],  # type: ignore[index]
                }
                if synthetic
                else None
            ),
        }
        # Layer 06: write the resolved case back to memory. On real data the
        # outcome is unknown; the row is written when it arrives (phase 2).
        if synthetic:
            memory.record(ev, db["action"], db["contacted"], db["outcome"]["recovered"], db["outcome"]["churned"], batch_id, kind=ev.kind)

    det = sum(1 for t in traces.values() if t["diagnosis"]["method"] == "deterministic_lookup")

    def outcome_row(d: Decision) -> dict:
        o = d["outcome"]
        return {"recovered": None if o is None else o["recovered"], "churned": None if o is None else o["churned"]}

    event_rows = []
    for ev in events:
        da, db = dec_a[ev.event_id], dec_b[ev.event_id]
        event_rows.append(
            {
                "eventId": ev.event_id,
                "kind": ev.kind,
                "source": ev.source,
                "paymentId": ev.payment_id,
                "subscriptionId": ev.subscription_id,
                "failedAt": ev.failed_at,
                "amountPaise": ev.amount_paise,
                "planName": ev.plan_name,
                "method": ev.method,
                "issuer": ev.issuer,
                "network": ev.network,
                "reasonCode": ev.reason_code,
                "reasonLabel": ev.reason_label,
                "rawReason": ev.raw_reason,
                "failureSide": ev.failure_side,
                "minutesSinceFailure": ev.minutes_since_failure,
                "messageClass": db["messageClass"],
                "upliftHat": round(uplift_hats[ev.event_id], 4),
                "baselineScore": round(baseline_scores[ev.event_id], 4),
                "holdout": ev.holdout,
                "agentA": {
                    "action": da["action"],
                    "label": ACTION_LABELS[da["action"]],
                    "contacted": da["contacted"],
                    **outcome_row(da),
                },
                "agentB": {
                    "action": db["action"],
                    "label": ACTION_LABELS[db["action"]],
                    "contacted": db["contacted"],
                    **outcome_row(db),
                    "blockedBy": db["blockedBy"],
                    "deniedBy": db["deniedBy"],
                    "deniedAction": ACTION_LABELS[db["deniedAction"]] if db["deniedAction"] else None,
                },
                "truthSegment": ev.segment,
            }
        )

    sleeping_dogs = []
    for ev in events:
        db = dec_b[ev.event_id]
        # Every case B deliberately declined: low recovery uplift, or a churn
        # estimate that priced the contact below zero. Budget-starved and
        # holdout cases are not "protected", so they stay out of this ledger.
        if db["contacted"] or b_wants[ev.event_id] or ev.holdout:
            continue
        da = dec_a[ev.event_id]
        if synthetic:
            churn_delta = ev.truth[3] - ev.truth[2]  # type: ignore[index]
        else:
            churn_delta = churn_taus[ev.event_id]
        sleeping_dogs.append(
            {
                "eventId": ev.event_id,
                "subscriptionId": ev.subscription_id,
                "amountPaise": ev.amount_paise,
                "planName": ev.plan_name,
                "reasonCode": ev.reason_code,
                "upliftHat": round(uplift_hats[ev.event_id], 4),
                "decision": db["action"],
                "decisionLabel": ACTION_LABELS[db["action"]],
                "blockedBy": db["blockedBy"] or "STOP_ON_NEGATIVE_UPLIFT",
                "baselineWouldContact": da["contacted"],
                "truthSegment": ev.segment,
                "churnDelta": round(churn_delta, 4),
                "churnDeltaIsEstimate": not synthetic,
                "estimatedDamageAvoidedPaise": round(max(0.0, churn_delta) * ev.amount_paise * merchant.churn_residual_cycles) if da["contacted"] else 0,
                "engagementScore": ev.engagement,
            }
        )
    sleeping_dogs.sort(key=lambda r: -r["estimatedDamageAvoidedPaise"])

    exceptions = []
    for ev in events:
        db = dec_b[ev.event_id]
        if db["action"] != "escalate":
            continue
        exceptions.append(
            {
                "eventId": ev.event_id,
                "subscriptionId": ev.subscription_id,
                "paymentId": ev.payment_id,
                "amountPaise": ev.amount_paise,
                "reasonCode": ev.reason_code,
                "reasonLabel": ev.reason_label,
                "raisedAt": ev.failed_at,
                "blockedBy": db["blockedBy"] or "ESCALATE_UNRESOLVED",
                "deniedAction": ACTION_LABELS[db["deniedAction"]] if db["deniedAction"] else ACTION_LABELS["silent_retry"],
                "structuredReason": next((g["note"] for g in db["gate"] if g["verdict"] == "BLOCK"), "Action ladder exhausted."),
                "attemptsThisCycle": ev.attempts_this_cycle,
                "contactsLast7d": ev.contacts_last_7d,
                "queue": "merchant_ops" if ev.merchant_side else "human",
            }
        )
    exceptions.sort(key=lambda r: -r["amountPaise"])

    # Replay script for the console stream.
    stream = [
        {"kind": "system", "text": f"batch {batch_id} · {len(events)} leak events from {source_name} queued ({'synthetic, both branches known' if synthetic else 'real data, outcomes pending'})", "counters": None},
        {"kind": "system", "text": "shadow ledger armed — baseline policy runs on the same events in parallel", "counters": None},
    ]
    processed = recovered = contacts = dogs = escalated = 0
    for ev in events:
        db = dec_b[ev.event_id]
        processed += 1
        if db["outcome"] and db["outcome"]["recovered"]:
            recovered += ev.amount_paise
        if db["contacted"]:
            contacts += 1
        if not db["contacted"] and not b_wants[ev.event_id] and ev.segment == "sleeping_dog":
            dogs += 1
        if db["action"] == "escalate":
            escalated += 1
            kind, text = "warn", f"{ev.event_id} {ev.reason_code} → escalated · {db['blockedBy']}"
        elif db["blockedBy"]:
            kind, text = "gate", f"{ev.event_id} {ev.reason_code} → {db['action']} · gated by {db['blockedBy']}"
        else:
            kind, text = "decision", f"{ev.event_id} {ev.reason_code} → {db['action']}"
        stream.append(
            {
                "kind": kind,
                "eventId": ev.event_id,
                "text": text,
                "counters": {
                    "processed": processed,
                    "recoveredPaise": recovered,
                    "contacts": contacts,
                    "sleepingDogsAvoided": dogs,
                    "escalated": escalated,
                },
            }
        )
    stream.append({"kind": "system", "text": "batch complete — comparison written to the shadow ledger", "counters": None})

    # Rows for the leaks table: what the pipeline saw, which arm, what B did
    # and with what propensity, and — on synthetic data — the outcome.
    stamp = now_iso()
    leak_rows = []
    for ev in events:
        db = dec_b[ev.event_id]
        ex = executions[ev.event_id]
        row = {
            "eventId": ev.event_id,
            "synthetic": synthetic,
            "kind": ev.kind,
            "source": ev.source,
            "counterpartyId": ev.customer_id,
            "contactHash": ev.contact_hash(),
            "amountPaise": ev.amount_paise,
            "method": ev.method,
            "network": ev.network,
            "reasonCode": ev.reason_code,
            "rawReason": ev.raw_reason,
            "failedAt": ev.failed_at,
            "arm": "control" if ev.holdout else "treatment",
            "wanted": db["wanted"],
            "explored": db["explored"],
            "propensity": db["propensity"],
            "contacted": db["contacted"],
            "action": db["action"],
            "messageClass": db["messageClass"],
            "paymentId": ev.payment_id or None,
            "subscriptionId": ev.subscription_id or None,
            "invoiceId": ev.invoice_id,
            "orderId": ev.order_id,
            "externalKind": ex.get("externalKind"),
            "externalId": ex.get("externalId"),
            "featureVersion": FEATURE_VERSION,
            "features": featurize(ev),
        }
        if synthetic and db["outcome"] is not None:
            row.update(
                outcomeState="resolved",
                outcomeRecovered=int(db["outcome"]["recovered"]),
                outcomeChurned=int(db["outcome"]["churned"]),
                outcomeSource="sim",
                outcomeAt=stamp,
            )
        leak_rows.append(row)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    honesty = _honesty(synthetic, seed, estimator_label, source_name, len(events))
    batch = {
        "source": "live",
        "batchId": batch_id,
        "label": f"{'Live batch' if synthetic else 'Real-data batch'} · {source_name} · seed {seed} · {elapsed_ms} ms",
        "generatedBy": "backend/app/engine.py",
        "seed": seed,
        "dataMode": "synthetic" if synthetic else "real",
        "sourceName": source_name,
        "sourceMeta": source_meta or {},
        "merchant": merchant.name,
        "estimatorMode": estimator_mode,
        "honesty": honesty,
        "assumptions": [
            {"key": "contactBudget", "value": merchant.contact_budget_per_batch, "note": "Outreach budget for the batch. Both agents get the same one; only the ranking objective differs."},
            {"key": "channelCostPaise", "value": merchant.costs_paise["whatsapp_utility"], "note": f"Message cost by channel and class, from merchant.toml: SMS ₹{merchant.costs_paise['sms_transactional'] / 100:.2f}, WhatsApp utility ₹{merchant.costs_paise['whatsapp_utility'] / 100:.2f}, WhatsApp marketing ₹{merchant.costs_paise['whatsapp_marketing'] / 100:.2f}."},
            {"key": "churnResidualCycles", "value": merchant.churn_residual_cycles, "note": "Assumed residual subscription value, in billing cycles, used to price churn caused by outreach."},
            {"key": "baselineProbabilityThreshold", "value": merchant.baseline_probability, "note": "Agent A contacts anything it scores at or above this probability of paying after contact."},
            {"key": "upliftThreshold", "value": merchant.uplift_threshold, "note": "Agent B needs estimated uplift above this before it will spend a contact."},
            {"key": "approvalThresholdPaise", "value": merchant.approval_threshold_paise, "note": "Outreach on leaks above this amount waits for a human approval."},
            {"key": "holdoutShare", "value": merchant.holdout_share, "note": "Randomised control share, hashed per counterparty, held out of contact by both agents so the policy's real incremental recovery can be measured."},
            {"key": "explorationShare", "value": merchant.exploration_share, "note": "Share of Agent B's contact decisions flipped at random inside the treatment arm on real data, so contact has a known propensity and uplift can be learned without confounding."},
        ],
        "currency": merchant.currency,
        "eventCount": len(events),
        "pipelineStats": {
            "deterministicLookups": det,
            "llmFallbacks": len(events) - det,
            "deterministicShare": round(det / len(events), 4),
        },
        "agents": {
            "A": {
                "key": "A",
                "name": "Baseline",
                "objective": "Recovery probability",
                "description": "Ranks by P(recovers | outreach) and contacts everything above a fixed threshold, within the same budget and the same policy gate. Not a strawman — it runs identical compliance rules. It simply optimises the wrong quantity.",
                "metrics": metrics_a,
                "segments": _segment_breakdown(events, dec_a, metrics_a["contactsMade"]) if synthetic else [],
                "curve": _uplift_curve(events, baseline_scores, merchant, {e.event_id: dec_a[e.event_id]["costPaise"] or merchant.costs_paise["sms_transactional"] for e in events}) if synthetic else [],
            },
            "B": {
                "key": "B",
                "name": "RecoverOps",
                "objective": "Causal uplift",
                "description": f"Ranks by the expected net value of the contact: recovery uplift from {estimator_label}, minus churn uplift priced at residual value, minus the real channel cost at the message class the gate assigns. Spends the same budget only where contact changes the outcome for the better; declined cases are logged as no-action, not dropped.",
                "metrics": metrics_b,
                "segments": _segment_breakdown(events, dec_b, metrics_b["contactsMade"]) if synthetic else [],
                "curve": _uplift_curve(events, uplift_hats, merchant, {e.event_id: dec_b[e.event_id]["costPaise"] or merchant.costs_paise["sms_transactional"] for e in events}) if synthetic else [],
            },
        },
        "events": event_rows,
        "sleepingDogs": sleeping_dogs,
        "exceptions": exceptions,
        "streamScript": stream,
    }
    return {"batch": batch, "traces": traces, "leakRows": leak_rows}


def _honesty(synthetic: bool, seed: int, estimator_label: str, source_name: str, n: int) -> dict:
    if synthetic:
        return {
            "whatIsSynthetic": (
                "The failed-payment events and their outcomes are simulated with both potential outcomes known — "
                "the four-segment evaluation requires the branch a live system never sees. This batch was drawn "
                f"fresh with seed {seed}, not replayed from a bundled file."
            ),
            "whatIsReal": (
                f"The estimates: Agent B ranks by a trained CATE model ({estimator_label}), Agent A by a trained "
                "P(recover | contact) classifier — neither sees ground truth. Also real: the Razorpay error taxonomy the "
                "reason families are mapped from, BM25 corpus retrieval, the SQLite case memory this batch wrote back to, "
                "the policy gate with its regulatory citations, and the Razorpay test-mode executor calls where API keys are configured."
            ),
            "curveNote": "The uplift curves are exact, not estimated — both branches are known. Against real data this measurement would require a randomised holdout and would carry confidence intervals.",
            "noiseNote": "At 500 events the realised rupee difference between the two policies is inside sampling noise, and should not be read as a headline. What is robust is where each agent spent its budget: that difference comes from the ranking objective.",
            "knownWeakness": (
                "Individual sleeping-dog identification has a hard ceiling: segment membership is latent given the "
                "observable features, so a dog and a persuadable with the same profile are indistinguishable to ANY "
                "estimator. The one feature that separates them here is the customer's response the last time they "
                "were chased — the signal a merchant's own dunning history carries, which this world simulates as "
                "informative but noisy and which real data only has after the learning loop has seen outcomes. "
                "Without it, both agents see pooled estimates and the comparison collapses to parity. What uplift "
                "ranking buys, robustly, is where the budget goes and a churn-priced value estimate that declines "
                "the clearly dangerous contacts."
            ),
        }
    return {
        "whatIsSynthetic": (
            f"Nothing. These {n} leaks were pulled from {source_name}. No outcome is known at decision time: every "
            "recovered/churned figure reads as pending until the learning loop attributes real outcomes to them."
        ),
        "whatIsReal": (
            "The events, amounts, instruments and Razorpay error reasons; the reason-family mapping; the policy gate "
            "with its citations, which is deterministic and therefore correct on real data from the first event; the "
            "Razorpay test-mode executor calls where keys are configured. Engagement and tenure are proxies estimated "
            "from the pulled history and are labelled as such in each trace."
        ),
        "curveNote": "No uplift curve on real data: the branch not taken is unobserved. A randomised holdout and accumulated outcomes produce a measured curve with an interval — that is what the learning loop exists for.",
        "noiseNote": (
            f"Agent B ranks on reason-family priors ({estimator_label}), not on a model that has seen these customers. "
            "The comparison with Agent A is about where each would spend the budget, not about money recovered — that number does not exist yet."
        ),
        "knownWeakness": (
            "Contact history comes only from this ledger, so a customer messaged by another system last week looks "
            "untouched here. DND status is unknown without a preference-register scrub, so promotional outreach is "
            "conservatively blocked when no consent record exists. Both are stated on the trace rather than assumed away."
        ),
    }
