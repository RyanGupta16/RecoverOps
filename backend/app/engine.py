"""Batch engine: runs the seven-layer pipeline over a fresh synthetic batch.

Output shape is byte-compatible with data/sample-batch.json (camelCase keys),
so the frontend renders a live batch and the bundled demo batch through the
same components — the only visible difference is the Demo Mode badge going
away.

What differs from the frontend generator: the estimates are real. Agent A
ranks by the trained treated-arm classifier P(recover | contact) — a genuine
model of the wrong quantity, not simulated noise. Agent B ranks by the CATE
estimator that won the offline benchmark. Diagnosis, retrieval precedents and
execution records come from the live layers, and every resolved case is
written back to case memory.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

import numpy as np

from .diagnosis import Diagnoser
from .executor import Executor
from .policy import ACTION_LABELS, CONTACT_ACTIONS, GateOutcome, evaluate_gate, preferred_contact_action
from .retrieval import CaseMemory, Corpus
from .sim import CONFIG, SEGMENTS, Event, featurize, generate_events
from .uplift import UpliftEngine

CANDIDATE_ACTIONS = ["silent_retry", "payment_link_sms", "payment_link_whatsapp", "card_update_request", "incentive_link"]

ESCALATE_BLOCK = {
    "ruleId": "ESCALATE_UNRESOLVED",
    "verdict": "BLOCK",
    "note": "Action ladder exhausted. Routed to the human queue with a structured reason.",
}
ESCALATE_NA = {"ruleId": "ESCALATE_UNRESOLVED", "verdict": "N/A", "note": "Action ladder has not been exhausted."}


class Decision(dict):
    """Per-event, per-agent decision record. Plain dict + attribute sugar."""

    __getattr__ = dict.__getitem__


def _run_policy(events: list[Event], agent: str, scores: dict[str, float], uplift_hats: dict[str, float], wants_contact) -> dict[str, Decision]:
    ordered = sorted(events, key=lambda e: -scores[e.event_id])
    budget_left = CONFIG["contactBudget"]
    decisions: dict[str, Decision] = {}

    for ev in ordered:
        score = scores[ev.event_id]
        tau = uplift_hats[ev.event_id]
        intended = preferred_contact_action(ev) if (wants_contact(ev, score) and budget_left > 0) else "silent_retry"
        result = evaluate_gate(ev, intended, agent, tau)
        action = intended

        # Blocked outreach falls down the ladder to a silent retry; if that is
        # blocked too, the case goes to a human rather than disappearing.
        if result.blocked and intended != "silent_retry":
            fallback = evaluate_gate(ev, "silent_retry", agent, tau)
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
        decisions[ev.event_id] = Decision(
            action=action,
            contacted=contacted,
            score=score,
            gate=result.gate,
            blockedBy=result.blocked_by,
            messageClass=result.message_class,
            deniedAction=result.denied_action,
            deniedBy=result.denied_by,
            outcome=None,
        )
    return decisions


def _realize(events: list[Event], decisions: dict[str, Decision]) -> dict:
    m = dict.fromkeys(
        (
            "contactsMade silentRetries escalations recoveredCount recoveredPaise sleepingDogsTouched "
            "wastedContacts outreachDrivenRecoveries outreachCausedCancellations outreachCausedChurnLossPaise "
            "churnedSubscriptions"
        ).split(),
        0,
    )
    for ev in events:
        d = decisions[ev.event_id]
        p0, p1, c0, c1 = ev.truth
        p_recover = p1 if d["contacted"] else p0
        p_churn = c1 if d["contacted"] else c0
        recovered = ev.u_recover < p_recover
        did_churn = (not recovered) and ev.u_churn < p_churn

        if d["contacted"]:
            m["contactsMade"] += 1
            if ev.segment == "sleeping_dog":
                m["sleepingDogsTouched"] += 1
            # Both branches are known — the counterfactual is directly observable.
            recovered_if_quiet = ev.u_recover < p0
            churned_if_quiet = (not recovered_if_quiet) and ev.u_churn < c0
            if recovered == recovered_if_quiet:
                m["wastedContacts"] += 1
            if recovered and not recovered_if_quiet:
                m["outreachDrivenRecoveries"] += 1
            if did_churn and not churned_if_quiet:
                m["outreachCausedCancellations"] += 1
                m["outreachCausedChurnLossPaise"] += ev.amount_paise * CONFIG["churnResidualCycles"]

        if d["action"] == "silent_retry":
            m["silentRetries"] += 1
        if d["action"] == "escalate":
            m["escalations"] += 1
        if recovered:
            m["recoveredCount"] += 1
            m["recoveredPaise"] += ev.amount_paise
        if did_churn:
            m["churnedSubscriptions"] += 1
        d["outcome"] = {"recovered": bool(recovered), "churned": bool(did_churn)}

    m["eventsProcessed"] = len(events)
    m["contactBudget"] = CONFIG["contactBudget"]
    m["recoveryRate"] = round(m["recoveredCount"] / len(events), 4)
    m["contactCostPaise"] = m["contactsMade"] * CONFIG["contactCostPaise"]
    m["netValuePaise"] = m["recoveredPaise"] - m["contactCostPaise"] - m["outreachCausedChurnLossPaise"]
    return m


def _uplift_curve(events: list[Event], scores: dict[str, float]) -> list[dict]:
    ordered = sorted(events, key=lambda e: -scores[e.event_id])
    points = [{"contacts": 0, "incrementalRecoveries": 0, "incrementalPaise": 0, "netPaise": 0}]
    recoveries = paise = cost = churn_cost = 0
    for i, ev in enumerate(ordered):
        p0, p1, c0, c1 = ev.truth
        y_treat = 1 if ev.u_recover < p1 else 0
        y_control = 1 if ev.u_recover < p0 else 0
        c_treat = 1 if (not y_treat and ev.u_churn < c1) else 0
        c_control = 1 if (not y_control and ev.u_churn < c0) else 0
        recoveries += y_treat - y_control
        paise += (y_treat - y_control) * ev.amount_paise
        cost += CONFIG["contactCostPaise"]
        churn_cost += (c_treat - c_control) * ev.amount_paise * CONFIG["churnResidualCycles"]
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


def _segment_breakdown(events: list[Event], decisions: dict[str, Decision], contacts_made: int) -> list[dict]:
    rows = []
    for segment in SEGMENTS:
        in_seg = [e for e in events if e.segment == segment]
        contacted = sum(1 for e in in_seg if decisions[e.event_id]["contacted"])
        true_uplift = sum(e.truth[1] - e.truth[0] for e in in_seg) / max(len(in_seg), 1)
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


def _precedents(ev: Event, corpus: Corpus, memory: CaseMemory) -> list[dict]:
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
    stats = memory.similar(ev)
    band = memory.band(ev.amount_paise)
    if stats["total"]:
        quiet = f"{stats['quiet_recovery_rate']:.0%} quiet-recovery" if stats["quiet_recovery_rate"] is not None else "no quiet branch yet"
        contact = f"{stats['contact_recovery_rate']:.0%} after outreach" if stats["contact_recovery_rate"] is not None else "no outreach branch yet"
        note = f"{stats['total']} similar prior cases in memory ({quiet}, {contact})."
    else:
        note = "No similar prior cases in memory yet — first batch touching this facet."
    out.append({"source": "case-memory", "ref": f"{ev.reason_code} · {ev.method} · {band} band", "note": note})
    if ev.minutes_since_failure > 30:
        out.append(
            {
                "source": "policy-corpus",
                "ref": "TCCCPR · transactional window",
                "note": f"{ev.minutes_since_failure} minutes elapsed. Outside the 30-minute transactional window, so the message is promotional-class.",
            }
        )
    return out


def run_batch(
    uplift: UpliftEngine,
    corpus: Corpus,
    memory: CaseMemory,
    executor: Executor,
    diagnoser: Diagnoser,
    seed: int | None = None,
) -> dict:
    seed = seed if seed is not None else secrets.randbelow(2**31)
    batch_id = f"bat_live_{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    t0 = time.perf_counter()
    events = generate_events(seed)
    executor.start_batch()

    # Estimates. Agent A: the treated-arm head alone — P(recover | contact),
    # the quantity every probability-threshold dunning system ranks by.
    X = np.array([featurize(e) for e in events])
    baseline_scores_arr = uplift.learners.t_mu1.predict_proba(X)[:, 1]
    estimates = dict(zip((ev.event_id for ev in events), uplift.estimate_batch(events)))
    baseline_scores = {ev.event_id: float(s) for ev, s in zip(events, baseline_scores_arr)}
    uplift_hats = {eid: est[2] for eid, est in estimates.items()}
    churn_taus = {eid: est[3] for eid, est in estimates.items()}

    # Agent B ranks by expected net VALUE of the contact, not recovery uplift
    # alone: recovery uplift prices what outreach wins, the churn-uplift term
    # prices what it can break. The sleeping dog barely registers on the first
    # (mildly negative) but lights up the second (contact raises cancellation
    # sharply), while a persuadable's churn uplift is negative — a well-timed
    # nudge retains. Value ranking separates the two where tau alone cannot.
    def contact_value(ev: Event) -> float:
        return (
            uplift_hats[ev.event_id] * ev.amount_paise
            - churn_taus[ev.event_id] * ev.amount_paise * CONFIG["churnResidualCycles"]
            - CONFIG["contactCostPaise"]
        )

    values = {ev.event_id: contact_value(ev) for ev in events}
    b_wants = {ev.event_id: uplift_hats[ev.event_id] > CONFIG["upliftThreshold"] and values[ev.event_id] > 0 for ev in events}

    dec_a = _run_policy(events, "A", baseline_scores, uplift_hats, lambda ev, s: s >= CONFIG["baselineProbabilityThreshold"])
    dec_b = _run_policy(events, "B", values, uplift_hats, lambda ev, s: b_wants[ev.event_id])
    metrics_a = _realize(events, dec_a)
    metrics_b = _realize(events, dec_b)

    # Traces: diagnosis (LLM where ambiguous), retrieval precedents, gate, execution.
    traces = {}
    for ev in events:
        da, db = dec_a[ev.event_id], dec_b[ev.event_id]
        p0_hat, p1_hat, tau_hat, churn_tau_hat = estimates[ev.event_id]
        per_action = []
        for action in CANDIDATE_ACTIONS:
            is_contact = action != "silent_retry"
            est_uplift = tau_hat if is_contact else max(0.0, p0_hat * 0.22)
            value = round(values[ev.event_id]) if is_contact else round(est_uplift * ev.amount_paise)
            per_action.append(
                {
                    "action": action,
                    "label": ACTION_LABELS[action],
                    "estimatedUplift": round(est_uplift, 4),
                    "expectedValuePaise": value,
                    "eligible": not (action == "incentive_link" and ev.reason_code != "MANDATE_REVOKED"),
                }
            )
        traces[ev.event_id] = {
            "eventId": ev.event_id,
            "diagnosis": diagnoser.diagnose(ev),
            "precedents": _precedents(ev, corpus, memory),
            "uplift": {
                "estimator": uplift.label,
                "pControlHat": round(p0_hat, 4),
                "pTreatHat": round(p1_hat, 4),
                "upliftHat": round(tau_hat, 4),
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
                "execution": executor.execute(ev, db["action"]),
                "outcome": db["outcome"],
            },
            "agentA": {
                "chosenAction": da["action"],
                "chosenLabel": ACTION_LABELS[da["action"]],
                "score": round(da["score"], 4),
                "outcome": da["outcome"],
            },
            "truth": {
                "segment": ev.segment,
                "pControl": ev.truth[0],
                "pTreat": ev.truth[1],
                "churnControl": ev.truth[2],
                "churnTreat": ev.truth[3],
            },
        }
        # Layer 06: write the resolved case back to memory.
        memory.record(ev, db["action"], db["contacted"], db["outcome"]["recovered"], db["outcome"]["churned"], batch_id)

    det = sum(1 for t in traces.values() if t["diagnosis"]["method"] == "deterministic_lookup")

    event_rows = []
    for ev in events:
        da, db = dec_a[ev.event_id], dec_b[ev.event_id]
        event_rows.append(
            {
                "eventId": ev.event_id,
                "paymentId": ev.payment_id,
                "subscriptionId": ev.subscription_id,
                "failedAt": ev.failed_at,
                "amountPaise": ev.amount_paise,
                "planName": ev.plan_name,
                "method": ev.method,
                "issuer": ev.issuer,
                "reasonCode": ev.reason_code,
                "reasonLabel": ev.reason_label,
                "failureSide": ev.failure_side,
                "minutesSinceFailure": ev.minutes_since_failure,
                "messageClass": db["messageClass"],
                "upliftHat": round(uplift_hats[ev.event_id], 4),
                "baselineScore": round(baseline_scores[ev.event_id], 4),
                "agentA": {
                    "action": da["action"],
                    "label": ACTION_LABELS[da["action"]],
                    "contacted": da["contacted"],
                    "recovered": da["outcome"]["recovered"],
                    "churned": da["outcome"]["churned"],
                },
                "agentB": {
                    "action": db["action"],
                    "label": ACTION_LABELS[db["action"]],
                    "contacted": db["contacted"],
                    "recovered": db["outcome"]["recovered"],
                    "churned": db["outcome"]["churned"],
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
        # estimate that priced the contact below zero. Budget-starved cases are
        # not "protected", so they stay out of this ledger.
        if db["contacted"] or b_wants[ev.event_id]:
            continue
        da = dec_a[ev.event_id]
        churn_delta = ev.truth[3] - ev.truth[2]
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
                "estimatedDamageAvoidedPaise": round(max(0.0, churn_delta) * ev.amount_paise * CONFIG["churnResidualCycles"]) if da["contacted"] else 0,
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
            }
        )
    exceptions.sort(key=lambda r: -r["amountPaise"])

    # Replay script for the console stream.
    stream = [
        {"kind": "system", "text": f"batch {batch_id} · {len(events)} failed payment events queued", "counters": None},
        {"kind": "system", "text": "shadow ledger armed — baseline policy runs on the same events in parallel", "counters": None},
    ]
    processed = recovered = contacts = dogs = escalated = 0
    for ev in events:
        db = dec_b[ev.event_id]
        processed += 1
        if db["outcome"]["recovered"]:
            recovered += ev.amount_paise
        if db["contacted"]:
            contacts += 1
        if not db["contacted"] and not b_wants[ev.event_id] and ev.segment == "sleeping_dog":
            dogs += 1
        if db["action"] == "escalate":
            escalated += 1
        if db["action"] == "escalate":
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

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    batch = {
        "source": "live",
        "batchId": batch_id,
        "label": f"Live batch · seed {seed} · {elapsed_ms} ms",
        "generatedBy": "backend/app/engine.py",
        "seed": seed,
        "honesty": {
            "whatIsSynthetic": (
                "The failed-payment events and their outcomes are simulated with both potential outcomes known — "
                "the four-segment evaluation requires the branch a live system never sees. This batch was drawn "
                f"fresh with seed {seed}, not replayed from a bundled file."
            ),
            "whatIsReal": (
                f"The estimates: Agent B ranks by a trained CATE model ({uplift.label}), Agent A by a trained "
                "P(recover | contact) classifier — neither sees ground truth. Also real: BM25 corpus retrieval, "
                "the SQLite case memory this batch wrote back to, the policy gate, and the Razorpay test-mode "
                "executor calls where API keys are configured."
            ),
            "curveNote": "The uplift curves are exact, not estimated — both branches are known. Against real data this measurement would require a randomised holdout and would carry confidence intervals.",
            "noiseNote": "At 500 events the realised rupee difference between the two policies is inside sampling noise, and should not be read as a headline. What is robust is where each agent spent its budget: that difference comes from the ranking objective.",
            "knownWeakness": (
                "Individual sleeping-dog identification has a hard ceiling: segment membership is latent given the "
                "observable features, so a dog and a persuadable with the same profile are indistinguishable to ANY "
                "estimator — the Bayes-optimal ranking on this world still touches roughly seven dogs per "
                "five-hundred-event batch (measured). What uplift ranking buys, robustly, is where the budget goes "
                "and a churn-priced value estimate that declines the clearly dangerous contacts."
            ),
        },
        "assumptions": [
            {"key": "contactBudget", "value": CONFIG["contactBudget"], "note": "Outreach budget for the batch. Both agents get the same one; only the ranking objective differs."},
            {"key": "contactCostPaise", "value": CONFIG["contactCostPaise"], "note": "Assumed direct marginal cost of one outbound message, in paise."},
            {"key": "churnResidualCycles", "value": CONFIG["churnResidualCycles"], "note": "Assumed residual subscription value, in billing cycles, used to price churn caused by outreach."},
            {"key": "baselineProbabilityThreshold", "value": CONFIG["baselineProbabilityThreshold"], "note": "Agent A contacts anything it scores at or above this probability of paying after contact."},
            {"key": "upliftThreshold", "value": CONFIG["upliftThreshold"], "note": "Agent B needs estimated uplift above this before it will spend a contact."},
        ],
        "currency": "INR",
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
                "description": "Ranks by a trained P(recovers | outreach) classifier and contacts everything above a fixed threshold, within the same budget and the same policy gate. Not a strawman — it runs identical compliance rules. It simply optimises the wrong quantity.",
                "metrics": metrics_a,
                "segments": _segment_breakdown(events, dec_a, metrics_a["contactsMade"]),
                "curve": _uplift_curve(events, baseline_scores),
            },
            "B": {
                "key": "B",
                "name": "RecoverOps",
                "objective": "Causal uplift",
                "description": f"Ranks by the expected net value of the contact: recovery uplift from the {uplift.label}, minus churn uplift from a paired cancellation model, minus message cost. Spends the same budget only where contact changes the outcome for the better; declined cases are logged as no-action, not dropped.",
                "metrics": metrics_b,
                "segments": _segment_breakdown(events, dec_b, metrics_b["contactsMade"]),
                "curve": _uplift_curve(events, uplift_hats),
            },
        },
        "events": event_rows,
        "sleepingDogs": sleeping_dogs,
        "exceptions": exceptions,
        "streamScript": stream,
    }
    return {"batch": batch, "traces": traces}
