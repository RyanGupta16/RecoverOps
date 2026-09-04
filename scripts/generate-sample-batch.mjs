/**
 * Generates the bundled demo-mode dataset for the RecoverOps site.
 *
 * This is a SIMULATION, not a measurement. It exists so the console still runs
 * when the FastAPI backend is unreachable. Everything it emits is labelled
 * `source: "sample"` and every screen that renders it shows a Demo Mode badge.
 *
 * Why a simulation can say anything useful at all: the four-segment causal
 * framework needs both potential outcomes (what happens if we contact, and what
 * happens if we don't) for the SAME event. No production system can observe
 * both. A synthetic batch with known ground truth can, which is exactly why the
 * evaluation batch is synthetic by design rather than by convenience.
 *
 * Run: npm run gen
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/* ------------------------------------------------------------------ *
 * Seeded PRNG — the batch must be byte-identical on every regeneration
 * ------------------------------------------------------------------ */

function mulberry32(seed) {
  let a = seed >>> 0;
  return function rand() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rand = mulberry32(20260903);
const pick = (arr) => arr[Math.floor(rand() * arr.length)];
const between = (lo, hi) => lo + rand() * (hi - lo);
const intBetween = (lo, hi) => Math.floor(between(lo, hi + 1));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Box-Muller, for estimator noise. */
function gauss(sd) {
  const u = Math.max(rand(), 1e-9);
  const v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v) * sd;
}

function id(prefix, len = 14) {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  for (let i = 0; i < len; i += 1) out += alphabet[Math.floor(rand() * alphabet.length)];
  return `${prefix}_${out}`;
}

/* ------------------------------------------------------------------ *
 * Simulation constants — every one of these is an ASSUMPTION, and the
 * site renders them as assumptions rather than as findings.
 * ------------------------------------------------------------------ */

const CONFIG = {
  eventCount: 500,
  /** Both agents get the same outreach budget. The only thing that differs is how they spend it. */
  contactBudget: 200,
  /** Direct marginal cost of one outbound message, in paise. */
  contactCostPaise: 120,
  /**
   * Residual value of a subscription, expressed as a multiple of one billing
   * cycle. Used only to put a rupee figure on churn caused by outreach.
   */
  churnResidualCycles: 3,
  /** Agent A contacts anything it thinks is at least this likely to pay after contact. */
  baselineProbabilityThreshold: 0.35,
  /** Agent B needs estimated uplift above this before it will spend a contact. */
  upliftThreshold: 0.05,
  /** Std. dev. of the simulated estimator's error on each potential outcome. */
  estimatorNoiseSd: 0.06,
};

/**
 * Normalised decline taxonomy. These are the simulator's own reason codes, not
 * verbatim Razorpay error codes — the backend maps `error.reason` /
 * `error.description` from the live API onto this taxonomy at ingest.
 *
 * `prior` is P(segment) given the reason code, ordered:
 *   [sure_thing, persuadable, lost_cause, sleeping_dog]
 */
const REASONS = [
  {
    code: 'INSUFFICIENT_FUNDS',
    label: 'Insufficient balance',
    side: 'customer',
    weight: 24,
    prior: [0.16, 0.44, 0.26, 0.14],
  },
  {
    code: 'CARD_EXPIRED',
    label: 'Card expired or reissued',
    side: 'customer',
    weight: 12,
    prior: [0.07, 0.55, 0.26, 0.12],
  },
  {
    code: 'DO_NOT_HONOUR',
    label: 'Declined by issuer (do not honour)',
    side: 'issuer',
    weight: 14,
    prior: [0.29, 0.24, 0.38, 0.09],
  },
  {
    code: 'ISSUER_DOWN',
    label: 'Issuer or gateway unavailable',
    side: 'issuer',
    weight: 11,
    prior: [0.63, 0.09, 0.22, 0.06],
  },
  {
    code: 'PAYMENT_TIMEOUT',
    label: 'Authorisation timed out',
    side: 'issuer',
    weight: 8,
    prior: [0.47, 0.16, 0.31, 0.06],
  },
  {
    code: 'INVALID_AUTH_DATA',
    label: 'Invalid CVV or authentication data',
    side: 'customer',
    weight: 7,
    prior: [0.1, 0.45, 0.35, 0.1],
  },
  {
    code: 'MANDATE_REVOKED',
    label: 'e-Mandate revoked by customer',
    side: 'customer',
    weight: 5,
    prior: [0.04, 0.13, 0.75, 0.08],
  },
  {
    code: 'AUTH_LIMIT_EXCEEDED',
    label: 'Per-transaction limit exceeded',
    side: 'customer',
    weight: 6,
    prior: [0.13, 0.47, 0.29, 0.11],
  },
  {
    code: 'SUSPECTED_FRAUD',
    label: 'Suspected fraud hold',
    side: 'risk',
    weight: 3,
    prior: [0.02, 0.03, 0.93, 0.02],
  },
  {
    code: 'GATEWAY_ERROR',
    label: 'Gateway-side error',
    side: 'issuer',
    weight: 10,
    prior: [0.56, 0.13, 0.25, 0.06],
  },
];

const SEGMENTS = ['sure_thing', 'persuadable', 'lost_cause', 'sleeping_dog'];

/**
 * Ground-truth potential outcomes per segment.
 *  pControl — P(recovers | no outreach)
 *  pTreat   — P(recovers | outreach)
 *  churnControl / churnTreat — P(subscription cancelled) under each branch
 *
 * The sleeping-dog row is the whole point: outreach LOWERS recovery and RAISES
 * cancellation, and a model that only predicts P(recovers | outreach) = 0.44
 * sees a number comfortably above a 0.35 contact threshold.
 */
const SEGMENT_TRUTH = {
  sure_thing: { pControl: 0.84, pTreat: 0.88, churnControl: 0.02, churnTreat: 0.03 },
  persuadable: { pControl: 0.11, pTreat: 0.58, churnControl: 0.19, churnTreat: 0.07 },
  lost_cause: { pControl: 0.03, pTreat: 0.05, churnControl: 0.55, churnTreat: 0.57 },
  sleeping_dog: { pControl: 0.62, pTreat: 0.44, churnControl: 0.06, churnTreat: 0.38 },
};

/**
 * Continuous per-event potential outcomes around the segment anchor — the same
 * heterogeneity the backend's `realize_truth` (backend/app/sim.py) applies, so
 * the demo batch and a live batch describe the same world. Persuadable uplift
 * scales with engagement; sleeping-dog damage scales with DISengagement; large
 * amounts dampen contact-driven recovery; tenure lifts quiet recovery; issuer-
 * side failures recover quietly more often.
 */
function realizeTruth(segment, engagement, amountPaise, tenureDays, failureSide) {
  const t = SEGMENT_TRUTH[segment];
  let { pControl: p0, pTreat: p1, churnControl: c0, churnTreat: c1 } = t;
  const amountDamp = 1.0 - 0.3 * (amountPaise / 300000.0);
  const tenureLift = 0.08 * (tenureDays / 1200.0);

  if (segment === 'persuadable') {
    p1 = p0 + (p1 - p0) * (0.45 + 1.1 * engagement) * amountDamp;
    p0 = p0 + tenureLift * 0.5;
  } else if (segment === 'sleeping_dog') {
    const damage = (0.35 + 0.9 * (1.0 - engagement)) * amountDamp;
    p1 = p0 - (p0 - p1) * damage;
    c1 = c0 + (c1 - c0) * (0.4 + 1.2 * (1.0 - engagement));
  } else if (segment === 'sure_thing') {
    p0 = p0 + tenureLift;
    p1 = p0 + 0.04 * amountDamp;
  }
  if (failureSide === 'issuer') p0 += 0.05;

  const clip = (v) => Math.min(0.97, Math.max(0.01, v));
  return { pControl: clip(p0), pTreat: clip(p1), churnControl: clip(c0), churnTreat: clip(c1) };
}

const PLANS = [
  { name: 'Standard monthly', paise: 49900 },
  { name: 'Standard monthly', paise: 49900 },
  { name: 'Pro monthly', paise: 129900 },
  { name: 'Pro monthly', paise: 129900 },
  { name: 'Team monthly', paise: 299900 },
  { name: 'Lite monthly', paise: 19900 },
  { name: 'Annual (monthly instalment)', paise: 89900 },
];

const METHODS = ['card', 'card', 'card', 'upi_autopay', 'upi_autopay', 'emandate', 'netbanking'];
const ISSUERS = ['HDFC', 'ICICI', 'SBI', 'Axis', 'Kotak', 'IndusInd', 'Yes Bank', 'IDFC First'];

/* ------------------------------------------------------------------ *
 * Event generation
 * ------------------------------------------------------------------ */

function weightedReason() {
  const total = REASONS.reduce((s, r) => s + r.weight, 0);
  let roll = rand() * total;
  for (const r of REASONS) {
    roll -= r.weight;
    if (roll <= 0) return r;
  }
  return REASONS[0];
}

function drawSegment(reason, engagement) {
  // Low engagement shifts probability mass from persuadable toward sleeping dog:
  // a barely-active subscriber is the one most likely to cancel when reminded
  // they are paying for something.
  const p = [...reason.prior];
  const shift = clamp((0.5 - engagement) * 0.34, -0.14, 0.16);
  if (shift > 0) {
    const moved = Math.min(shift, p[1] * 0.6);
    p[1] -= moved;
    p[3] += moved;
  } else {
    const moved = Math.min(-shift, p[3] * 0.6);
    p[3] -= moved;
    p[1] += moved;
  }
  let roll = rand();
  for (let i = 0; i < p.length; i += 1) {
    roll -= p[i];
    if (roll <= 0) return SEGMENTS[i];
  }
  return SEGMENTS[SEGMENTS.length - 1];
}

const BASE_TS = Date.parse('2026-08-28T04:30:00.000Z'); // fixed: the generator must be deterministic

function makeEvent(index) {
  const reason = weightedReason();
  const plan = pick(PLANS);
  const engagement = clamp(between(0.05, 0.98), 0, 1);
  const segment = drawSegment(reason, engagement);
  const method = pick(METHODS);

  // Minutes since the failed authorisation. The 30-minute transactional window
  // under TCCCPR is the single most consequential field on this record.
  const minutesSinceFailure = rand() < 0.42 ? intBetween(2, 30) : intBetween(31, 2880);

  const localHourIst = intBetween(0, 23);

  // Events reach the agent AFTER the gateway's own scheduled attempt, so a
  // fresh zero-attempt case is the exception, not the rule. Getting this
  // distribution wrong starves SILENT_FIRST and leaves the batch with almost
  // no outreach to compare.
  const attemptRoll = rand();
  const attemptsThisCycle =
    attemptRoll < 0.18 ? 0 : attemptRoll < 0.64 ? 1 : attemptRoll < 0.9 ? 2 : 3;
  const contactsLast7d = rand() < 0.72 ? 0 : intBetween(1, 3);
  const retries30d = intBetween(0, 14);

  const failedAt = BASE_TS + index * 41_000 - minutesSinceFailure * 60_000;
  const tenureDays = intBetween(21, 1180);

  return {
    eventId: id('evt', 12),
    paymentId: id('pay'),
    subscriptionId: id('sub'),
    customerId: id('cust', 12),
    failedAt: new Date(failedAt).toISOString(),
    amountPaise: plan.paise,
    planName: plan.name,
    currency: 'INR',
    method,
    issuer: method === 'upi_autopay' ? 'UPI' : pick(ISSUERS),
    reasonCode: reason.code,
    reasonLabel: reason.label,
    failureSide: reason.side,
    minutesSinceFailure,
    localHourIst,
    attemptsThisCycle,
    contactsLast7d,
    retries30d,
    consentOnFile: rand() < 0.63,
    dndRegistered: rand() < 0.31,
    engagementScore: Number(engagement.toFixed(3)),
    tenureDays,
    /** Ground truth. Available because the batch is synthetic; never available in production. */
    truth: {
      segment,
      ...realizeTruth(segment, engagement, plan.paise, tenureDays, reason.side),
    },
    /** Shared uniform draws — the standard monotone coupling for potential outcomes. */
    _uRecover: rand(),
    _uChurn: rand(),
  };
}

const events = Array.from({ length: CONFIG.eventCount }, (_, i) => makeEvent(i));

/* ------------------------------------------------------------------ *
 * Estimators
 * ------------------------------------------------------------------ */

/**
 * Simulated estimators, calibrated to what a trained model can actually know.
 *
 * An earlier version simulated the estimator as ground truth plus small noise.
 * That quietly hands the agent an oracle: given the observable features, the
 * SEGMENT IS LATENT — a sleeping dog and a persuadable with the same reason
 * code and engagement are indistinguishable, and the best any estimator can
 * recover is the posterior-weighted mixture E[tau | x]. The backend's trained
 * models are benchmarked against exactly that ceiling, so the demo batch now
 * simulates estimates the same way: posterior mixture + noise. This is why the
 * demo numbers and a live batch tell the same story instead of the demo
 * flattering the agent.
 */
function segmentPosterior(ev) {
  const reason = REASONS.find((r) => r.code === ev.reasonCode);
  const p = [...reason.prior];
  const shift = clamp((0.5 - ev.engagementScore) * 0.34, -0.14, 0.16);
  if (shift > 0) {
    const moved = Math.min(shift, p[1] * 0.6);
    p[1] -= moved;
    p[3] += moved;
  } else {
    const moved = Math.min(-shift, p[3] * 0.6);
    p[3] -= moved;
    p[1] += moved;
  }
  const total = p.reduce((s, x) => s + x, 0);
  return p.map((x) => x / total);
}

function mixedOutcomes(ev) {
  const post = segmentPosterior(ev);
  let p0 = 0;
  let p1 = 0;
  let ct = 0;
  SEGMENTS.forEach((seg, i) => {
    const t = realizeTruth(seg, ev.engagementScore, ev.amountPaise, ev.tenureDays, ev.failureSide);
    p0 += post[i] * t.pControl;
    p1 += post[i] * t.pTreat;
    ct += post[i] * (t.churnTreat - t.churnControl);
  });
  return { p0, p1, churnTau: ct };
}

/** Agent A's model: P(recovers | outreach) — the wrong quantity, estimated honestly. */
function baselineScore(mix) {
  return clamp(mix.p1 + gauss(CONFIG.estimatorNoiseSd), 0.01, 0.99);
}

/** Agent B's models: recovery uplift + churn uplift, both posterior-bounded. */
function upliftScores(mix) {
  const pTreatHat = clamp(mix.p1 + gauss(CONFIG.estimatorNoiseSd), 0.01, 0.99);
  const pControlHat = clamp(mix.p0 + gauss(CONFIG.estimatorNoiseSd), 0.01, 0.99);
  const churnTauHat = mix.churnTau + gauss(CONFIG.estimatorNoiseSd * 0.5);
  return { pTreatHat, pControlHat, upliftHat: pTreatHat - pControlHat, churnTauHat };
}

for (const ev of events) {
  const mix = mixedOutcomes(ev);
  ev._baselineScore = baselineScore(mix);
  ev._uplift = upliftScores(mix);
  // Expected net value of the contact: what outreach wins, minus what it can
  // break (churn priced at residual cycles), minus the message itself.
  ev._contactValue =
    ev._uplift.upliftHat * ev.amountPaise -
    ev._uplift.churnTauHat * ev.amountPaise * CONFIG.churnResidualCycles -
    CONFIG.contactCostPaise;
  ev._bWants = ev._uplift.upliftHat > CONFIG.upliftThreshold && ev._contactValue > 0;
}

/* ------------------------------------------------------------------ *
 * Policy gate — identical for both agents except STOP_ON_NEGATIVE_UPLIFT,
 * which only Agent B has. The baseline is deliberately NOT a strawman: it
 * runs the same compliance rules on the same events. The one thing that
 * differs is the objective it ranks by.
 * ------------------------------------------------------------------ */

const PASS = 'PASS';
const BLOCK = 'BLOCK';
const NA = 'N/A';

function evaluateGate(ev, intendedAction, agent) {
  const gate = [];
  const push = (ruleId, verdict, note) => gate.push({ ruleId, verdict, note });
  let blocked = false;
  const blockOnce = (ruleId, note) => {
    if (!blocked) {
      push(ruleId, BLOCK, note);
      blocked = true;
      return true;
    }
    push(ruleId, NA, 'Not evaluated — an earlier rule already blocked this action.');
    return false;
  };

  const isContact =
    intendedAction.startsWith('payment_link') ||
    intendedAction === 'card_update_request' ||
    intendedAction === 'incentive_link';
  const isRetry = intendedAction === 'silent_retry' || intendedAction === 'retry_scheduled';

  // 1. Fraud is a hard stop, ahead of everything else.
  if (ev.reasonCode === 'SUSPECTED_FRAUD') {
    blockOnce(
      'NO_RETRY_ON_FRAUD',
      'Reason code is a suspected-fraud hold. Never retried, never contacted.',
    );
  } else {
    push('NO_RETRY_ON_FRAUD', PASS, 'Reason code is not a fraud hold.');
  }

  // 2. Message classification decides which of the next two rules even apply.
  const transactional = ev.minutesSinceFailure <= 30;
  const messageClass = transactional ? 'transactional' : 'promotional';
  if (blocked) {
    push(
      'MSG_CLASS_TRANSACTIONAL_30MIN',
      NA,
      'Not evaluated — an earlier rule already blocked this action.',
    );
  } else if (!isContact) {
    push('MSG_CLASS_TRANSACTIONAL_30MIN', NA, 'No outbound message in this action.');
  } else {
    push(
      'MSG_CLASS_TRANSACTIONAL_30MIN',
      PASS,
      transactional
        ? `${ev.minutesSinceFailure} min since failure — inside the 30-minute window, classified transactional.`
        : `${ev.minutesSinceFailure} min since failure — outside the 30-minute window, reclassified promotional and re-gated.`,
    );
  }

  // 3. Quiet hours, promotional class only.
  if (blocked) {
    push(
      'QUIET_HOURS_2100_0900_IST',
      NA,
      'Not evaluated — an earlier rule already blocked this action.',
    );
  } else if (!isContact || transactional) {
    push(
      'QUIET_HOURS_2100_0900_IST',
      NA,
      isContact
        ? 'Transactional class — quiet hours do not apply.'
        : 'No outbound message in this action.',
    );
  } else if (ev.localHourIst >= 21 || ev.localHourIst < 9) {
    blockOnce(
      'QUIET_HOURS_2100_0900_IST',
      `Local time ${String(ev.localHourIst).padStart(2, '0')}:00 IST is outside 09:00–21:00 for promotional-class outreach.`,
    );
  } else {
    push(
      'QUIET_HOURS_2100_0900_IST',
      PASS,
      `Local time ${String(ev.localHourIst).padStart(2, '0')}:00 IST is inside 09:00–21:00.`,
    );
  }

  // 4. Consent + DND, promotional class only.
  if (blocked) {
    push(
      'DND_SCRUB_PROMOTIONAL',
      NA,
      'Not evaluated — an earlier rule already blocked this action.',
    );
  } else if (!isContact || transactional) {
    push(
      'DND_SCRUB_PROMOTIONAL',
      NA,
      isContact
        ? 'Transactional class — consent scrub does not apply.'
        : 'No outbound message in this action.',
    );
  } else if (!ev.consentOnFile || ev.dndRegistered) {
    blockOnce(
      'DND_SCRUB_PROMOTIONAL',
      !ev.consentOnFile
        ? 'No consent record on file for promotional-class messaging.'
        : 'Number is on the DND register and the message is promotional-class.',
    );
  } else {
    push('DND_SCRUB_PROMOTIONAL', PASS, 'Consent record present, not DND-registered.');
  }

  // 5. Charge-attempt ceilings.
  if (blocked) {
    push(
      'MAX_RETRY_3_PER_CYCLE',
      NA,
      'Not evaluated — an earlier rule already blocked this action.',
    );
  } else if (!isRetry) {
    push('MAX_RETRY_3_PER_CYCLE', NA, 'No charge attempt in this action.');
  } else if (ev.attemptsThisCycle >= 3) {
    blockOnce(
      'MAX_RETRY_3_PER_CYCLE',
      `${ev.attemptsThisCycle} attempts already made this billing cycle.`,
    );
  } else {
    push('MAX_RETRY_3_PER_CYCLE', PASS, `${ev.attemptsThisCycle} of 3 attempts used this cycle.`);
  }

  if (blocked) {
    push(
      'NETWORK_RETRY_CAP_30D',
      NA,
      'Not evaluated — an earlier rule already blocked this action.',
    );
  } else if (!isRetry) {
    push('NETWORK_RETRY_CAP_30D', NA, 'No charge attempt in this action.');
  } else if (ev.retries30d >= 14) {
    blockOnce(
      'NETWORK_RETRY_CAP_30D',
      `${ev.retries30d} attempts in the rolling 30-day window — network ceiling reached.`,
    );
  } else {
    push('NETWORK_RETRY_CAP_30D', PASS, `${ev.retries30d} attempts in the rolling 30-day window.`);
  }

  // 6. Silent-first ladder.
  if (blocked) {
    push('SILENT_FIRST', NA, 'Not evaluated — an earlier rule already blocked this action.');
  } else if (!isContact) {
    push('SILENT_FIRST', NA, 'Action is a silent retry — this rule gates outreach, not retries.');
  } else if (ev.attemptsThisCycle === 0) {
    blockOnce(
      'SILENT_FIRST',
      'No silent retry attempted yet this cycle. Outreach deferred until one has run.',
    );
  } else {
    push(
      'SILENT_FIRST',
      PASS,
      `${ev.attemptsThisCycle} silent attempt(s) already made this cycle.`,
    );
  }

  // 7. Contact frequency ceiling.
  if (blocked) {
    push(
      'MAX_CONTACTS_2_PER_7D',
      NA,
      'Not evaluated — an earlier rule already blocked this action.',
    );
  } else if (!isContact) {
    push('MAX_CONTACTS_2_PER_7D', NA, 'No outbound message in this action.');
  } else if (ev.contactsLast7d >= 2) {
    blockOnce(
      'MAX_CONTACTS_2_PER_7D',
      `${ev.contactsLast7d} contacts already made in the rolling 7-day window.`,
    );
  } else {
    push(
      'MAX_CONTACTS_2_PER_7D',
      PASS,
      `${ev.contactsLast7d} of 2 contacts used in the rolling 7-day window.`,
    );
  }

  // 8. Issuer-side failures are not the customer's problem.
  if (blocked) {
    push(
      'BACKOFF_ON_ISSUER_DOWN',
      NA,
      'Not evaluated — an earlier rule already blocked this action.',
    );
  } else if (ev.failureSide !== 'issuer') {
    push('BACKOFF_ON_ISSUER_DOWN', PASS, 'Failure originates customer-side, not issuer-side.');
  } else if (isContact) {
    blockOnce(
      'BACKOFF_ON_ISSUER_DOWN',
      'Bank, gateway or network-side failure. Exponential backoff only, zero customer contact.',
    );
  } else {
    push(
      'BACKOFF_ON_ISSUER_DOWN',
      PASS,
      'Issuer-side failure — backoff schedule applied to the retry.',
    );
  }

  // 9. Incentive ceiling.
  if (intendedAction !== 'incentive_link') {
    push('DISCOUNT_CAP_5PCT', NA, 'No incentive attached to this action.');
  } else if (blocked) {
    push('DISCOUNT_CAP_5PCT', NA, 'Not evaluated — an earlier rule already blocked this action.');
  } else {
    push(
      'DISCOUNT_CAP_5PCT',
      PASS,
      `Incentive held at 5% of ₹${(ev.amountPaise / 100).toFixed(2)}, inside the batch budget ceiling.`,
    );
  }

  // 10. Sleeping-dog protection. Agent A has no uplift estimate, so it cannot
  //     evaluate this rule at all — which is the entire finding.
  if (agent !== 'B') {
    push(
      'STOP_ON_NEGATIVE_UPLIFT',
      NA,
      'Baseline policy has no uplift estimate. Rule is unevaluable.',
    );
  } else if (blocked) {
    push(
      'STOP_ON_NEGATIVE_UPLIFT',
      NA,
      'Not evaluated — an earlier rule already blocked this action.',
    );
  } else if (!isContact) {
    push('STOP_ON_NEGATIVE_UPLIFT', NA, 'No outreach in this action.');
  } else if (ev._uplift.upliftHat <= CONFIG.upliftThreshold) {
    blockOnce(
      'STOP_ON_NEGATIVE_UPLIFT',
      `Estimated uplift ${ev._uplift.upliftHat >= 0 ? '+' : ''}${ev._uplift.upliftHat.toFixed(3)} is at or below the ${CONFIG.upliftThreshold} threshold.`,
    );
  } else {
    push(
      'STOP_ON_NEGATIVE_UPLIFT',
      PASS,
      `Estimated uplift +${ev._uplift.upliftHat.toFixed(3)} clears the ${CONFIG.upliftThreshold} threshold.`,
    );
  }

  return {
    gate,
    blocked,
    messageClass,
    blockedBy: gate.find((g) => g.verdict === BLOCK)?.ruleId ?? null,
  };
}

/* ------------------------------------------------------------------ *
 * Policies
 * ------------------------------------------------------------------ */

function preferredContactAction(ev) {
  if (ev.reasonCode === 'CARD_EXPIRED' || ev.reasonCode === 'INVALID_AUTH_DATA')
    return 'card_update_request';
  if (ev.reasonCode === 'MANDATE_REVOKED') return 'incentive_link';
  if (ev.method === 'upi_autopay') return 'payment_link_whatsapp';
  return 'payment_link_sms';
}

/**
 * Runs one policy over the whole batch under a fixed outreach budget.
 * `rank` returns the score the policy sorts by, descending.
 */
function runPolicy({ agent, rank, wantsContact }) {
  const ordered = [...events]
    .map((ev) => ({ ev, score: rank(ev) }))
    .sort((a, b) => b.score - a.score);

  let budgetLeft = CONFIG.contactBudget;
  const decisions = new Map();

  for (const { ev, score } of ordered) {
    const intended =
      wantsContact(ev, score) && budgetLeft > 0 ? preferredContactAction(ev) : 'silent_retry';
    let result = evaluateGate(ev, intended, agent);
    let action = intended;

    // Blocked outreach falls back down the ladder to a silent retry, and if
    // that is blocked too the case goes to a human rather than disappearing.
    if (result.blocked && intended !== 'silent_retry') {
      const fallback = evaluateGate(ev, 'silent_retry', agent);
      if (fallback.blocked) {
        action = 'escalate';
        result = {
          ...fallback,
          gate: [
            ...fallback.gate,
            {
              ruleId: 'ESCALATE_UNRESOLVED',
              verdict: 'BLOCK',
              note: 'Action ladder exhausted. Routed to the human queue with a structured reason.',
            },
          ],
          escalated: true,
          deniedAction: intended,
          deniedBy: result.blockedBy,
        };
      } else {
        action = 'silent_retry';
        result = { ...fallback, deniedAction: intended, deniedBy: result.blockedBy };
      }
    } else if (result.blocked && intended === 'silent_retry') {
      action = 'escalate';
      result = {
        ...result,
        gate: [
          ...result.gate,
          {
            ruleId: 'ESCALATE_UNRESOLVED',
            verdict: 'BLOCK',
            note: 'Action ladder exhausted. Routed to the human queue with a structured reason.',
          },
        ],
        escalated: true,
        deniedAction: intended,
        deniedBy: result.blockedBy,
      };
    } else {
      result = {
        ...result,
        gate: [
          ...result.gate,
          {
            ruleId: 'ESCALATE_UNRESOLVED',
            verdict: 'N/A',
            note: 'Action ladder has not been exhausted.',
          },
        ],
      };
    }

    const contacted =
      action === 'card_update_request' ||
      action === 'incentive_link' ||
      action.startsWith('payment_link');
    if (contacted) budgetLeft -= 1;

    decisions.set(ev.eventId, { action, contacted, score, ...result });
  }

  // Realise outcomes from the ground truth using the pre-drawn uniforms.
  let recoveredCount = 0;
  let recoveredPaise = 0;
  let contacts = 0;
  let silentRetries = 0;
  let escalations = 0;
  let churned = 0;
  let sleepingDogsTouched = 0;
  let wastedContacts = 0;
  let outreachCausedCancellations = 0;
  let outreachCausedChurnLossPaise = 0;
  let outreachDrivenRecoveries = 0;

  for (const ev of events) {
    const d = decisions.get(ev.eventId);
    const t = ev.truth;
    const pRecover = d.contacted ? t.pTreat : t.pControl;
    const pChurn = d.contacted ? t.churnTreat : t.churnControl;
    const recovered = ev._uRecover < pRecover;
    const didChurn = !recovered && ev._uChurn < pChurn;

    if (d.contacted) {
      contacts += 1;
      if (t.segment === 'sleeping_dog') sleepingDogsTouched += 1;

      // Both branches are known, so the counterfactual for a contacted event is
      // directly observable: what WOULD have happened had we stayed quiet.
      const recoveredIfQuiet = ev._uRecover < t.pControl;
      const churnedIfQuiet = !recoveredIfQuiet && ev._uChurn < t.churnControl;

      if (recovered === recoveredIfQuiet) wastedContacts += 1;
      if (recovered && !recoveredIfQuiet) outreachDrivenRecoveries += 1;
      if (didChurn && !churnedIfQuiet) {
        outreachCausedCancellations += 1;
        outreachCausedChurnLossPaise += ev.amountPaise * CONFIG.churnResidualCycles;
      }
    }

    if (d.action === 'silent_retry') silentRetries += 1;
    if (d.action === 'escalate') escalations += 1;
    if (recovered) {
      recoveredCount += 1;
      recoveredPaise += ev.amountPaise;
    }
    if (didChurn) churned += 1;

    d.outcome = { recovered, churned: didChurn };
  }

  const contactCostPaise = contacts * CONFIG.contactCostPaise;
  return {
    agent,
    decisions,
    metrics: {
      eventsProcessed: events.length,
      contactsMade: contacts,
      contactBudget: CONFIG.contactBudget,
      silentRetries,
      escalations,
      recoveredCount,
      recoveredPaise,
      recoveryRate: Number((recoveredCount / events.length).toFixed(4)),
      sleepingDogsTouched,
      /** Contacts where the outcome was identical to staying quiet — pure spend. */
      wastedContacts,
      /** Recoveries that happened only because of outreach. The thing worth buying. */
      outreachDrivenRecoveries,
      /** Cancellations the outreach itself caused. The thing worth avoiding. */
      outreachCausedCancellations,
      outreachCausedChurnLossPaise,
      /**
       * Total cancellations across the batch, most of which would have happened
       * under any policy. Reported for completeness, never as a headline: it is
       * dominated by lost causes and barely moves between agents.
       */
      churnedSubscriptions: churned,
      contactCostPaise,
      /**
       * Incremental, not absolute. Charging an agent for churn it did not cause
       * makes every policy look catastrophic and hides the difference between
       * them, which is the only thing this comparison exists to show.
       */
      netValuePaise: recoveredPaise - contactCostPaise - outreachCausedChurnLossPaise,
    },
  };
}

const agentA = runPolicy({
  agent: 'A',
  rank: (ev) => ev._baselineScore,
  wantsContact: (ev, score) => score >= CONFIG.baselineProbabilityThreshold,
});

const agentB = runPolicy({
  agent: 'B',
  rank: (ev) => ev._contactValue,
  wantsContact: (ev) => ev._bWants,
});

/* ------------------------------------------------------------------ *
 * Cumulative uplift curves
 *
 * This is the exact incremental effect of each agent's contact ordering, not
 * an estimate: with both potential outcomes known, walking down the ranking and
 * summing (Y_treated − Y_control) gives the true curve. Against real data this
 * would have to be a Qini estimate off a randomised holdout.
 * ------------------------------------------------------------------ */

function upliftCurve(rank) {
  const ordered = [...events].sort((a, b) => rank(b) - rank(a));
  const points = [{ contacts: 0, incrementalRecoveries: 0, incrementalPaise: 0, netPaise: 0 }];
  let recoveries = 0;
  let paise = 0;
  let cost = 0;
  let churnCost = 0;

  ordered.forEach((ev, i) => {
    const t = ev.truth;
    const yTreat = ev._uRecover < t.pTreat ? 1 : 0;
    const yControl = ev._uRecover < t.pControl ? 1 : 0;
    const cTreat = !yTreat && ev._uChurn < t.churnTreat ? 1 : 0;
    const cControl = !yControl && ev._uChurn < t.churnControl ? 1 : 0;

    recoveries += yTreat - yControl;
    paise += (yTreat - yControl) * ev.amountPaise;
    cost += CONFIG.contactCostPaise;
    churnCost += (cTreat - cControl) * ev.amountPaise * CONFIG.churnResidualCycles;

    if ((i + 1) % 10 === 0 || i === ordered.length - 1) {
      points.push({
        contacts: i + 1,
        incrementalRecoveries: recoveries,
        incrementalPaise: paise,
        netPaise: paise - cost - churnCost,
      });
    }
  });

  return points;
}

const curveA = upliftCurve((ev) => ev._baselineScore);
const curveB = upliftCurve((ev) => ev._contactValue);

/* ------------------------------------------------------------------ *
 * Segment breakdown — where each agent actually spent its budget
 * ------------------------------------------------------------------ */

function segmentBreakdown(run) {
  const rows = SEGMENTS.map((segment) => {
    const inSegment = events.filter((e) => e.truth.segment === segment);
    const contacted = inSegment.filter((e) => run.decisions.get(e.eventId).contacted);
    return {
      segment,
      population: inSegment.length,
      contacted: contacted.length,
      shareOfBudget: run.metrics.contactsMade
        ? Number((contacted.length / run.metrics.contactsMade).toFixed(4))
        : 0,
      trueUplift: Number(
        (
          inSegment.reduce((s, e) => s + (e.truth.pTreat - e.truth.pControl), 0) /
          Math.max(inSegment.length, 1)
        ).toFixed(4),
      ),
    };
  });
  return rows;
}

/* ------------------------------------------------------------------ *
 * Per-event rows, traces, ledgers
 * ------------------------------------------------------------------ */

const ACTION_LABELS = {
  silent_retry: 'Silent retry',
  retry_scheduled: 'Retry scheduled',
  payment_link_sms: 'Payment link · SMS',
  payment_link_whatsapp: 'Payment link · WhatsApp',
  card_update_request: 'Card update request',
  incentive_link: 'Incentive link',
  escalate: 'Escalated to human queue',
  no_action: 'No action',
};

const CANDIDATE_ACTIONS = [
  'silent_retry',
  'payment_link_sms',
  'payment_link_whatsapp',
  'card_update_request',
  'incentive_link',
];

/** Retrieval precedents. Deterministic, derived from the event's own features. */
function precedentsFor(ev) {
  const base = [
    {
      source: 'razorpay-error-corpus',
      ref: `error.reason → ${ev.reasonCode}`,
      note: `${ev.reasonLabel}. Failure attributed ${ev.failureSide}-side.`,
    },
    {
      source: 'case-memory',
      ref: `${ev.reasonCode} · ${ev.method} · ₹${(ev.amountPaise / 100).toFixed(0)} band`,
      note: `${intBetween(12, 240)} similar prior cases in memory. Silent retry cleared ${intBetween(18, 61)}% of them without outreach.`,
    },
    {
      source: 'case-memory',
      ref: `${ev.reasonCode} · outreach branch`,
      note: `Outreach on comparable cases moved recovery by ${ev.truth.pTreat - ev.truth.pControl >= 0 ? '+' : ''}${((ev.truth.pTreat - ev.truth.pControl) * 100).toFixed(0)} points and cancellation by ${((ev.truth.churnTreat - ev.truth.churnControl) * 100).toFixed(0)} points.`,
    },
  ];
  if (ev.minutesSinceFailure > 30) {
    base.push({
      source: 'policy-corpus',
      ref: 'TCCCPR · transactional window',
      note: `${ev.minutesSinceFailure} minutes elapsed. Outside the 30-minute transactional window, so the message is promotional-class.`,
    });
  }
  return base;
}

function buildTrace(ev, runA, runB) {
  const dA = runA.decisions.get(ev.eventId);
  const dB = runB.decisions.get(ev.eventId);
  const u = ev._uplift;

  const perAction = CANDIDATE_ACTIONS.map((action) => {
    const isContact = action !== 'silent_retry';
    // Silent retry moves the control branch; outreach moves the treated branch.
    const uplift = isContact ? u.upliftHat : Math.max(0, u.pControlHat * 0.22);
    const expectedValuePaise = Math.round(isContact ? ev._contactValue : uplift * ev.amountPaise);
    return {
      action,
      label: ACTION_LABELS[action],
      estimatedUplift: Number(uplift.toFixed(4)),
      expectedValuePaise,
      eligible: !(action === 'incentive_link' && ev.reasonCode !== 'MANDATE_REVOKED'),
    };
  });

  return {
    eventId: ev.eventId,
    diagnosis: {
      method:
        ev.reasonCode === 'GATEWAY_ERROR' || ev.reasonCode === 'DO_NOT_HONOUR'
          ? 'llm_fallback'
          : 'deterministic_lookup',
      reasonCode: ev.reasonCode,
      reasonLabel: ev.reasonLabel,
      failureSide: ev.failureSide,
      latencyMs:
        ev.reasonCode === 'GATEWAY_ERROR' || ev.reasonCode === 'DO_NOT_HONOUR'
          ? intBetween(380, 1400)
          : 0,
      note:
        ev.reasonCode === 'GATEWAY_ERROR' || ev.reasonCode === 'DO_NOT_HONOUR'
          ? 'Reason code is ambiguous or unmapped. Escalated to the language model for classification.'
          : 'Reason code resolved from the deterministic lookup table. No model call, no latency, no cost.',
    },
    precedents: precedentsFor(ev),
    uplift: {
      estimator: 'segment-posterior simulation (sample data)',
      pControlHat: Number(u.pControlHat.toFixed(4)),
      pTreatHat: Number(u.pTreatHat.toFixed(4)),
      upliftHat: Number(u.upliftHat.toFixed(4)),
      perAction,
    },
    agentB: {
      chosenAction: dB.action,
      chosenLabel: ACTION_LABELS[dB.action],
      messageClass: dB.messageClass,
      gate: dB.gate,
      blockedBy: dB.blockedBy,
      deniedAction: dB.deniedAction ? ACTION_LABELS[dB.deniedAction] : null,
      deniedBy: dB.deniedBy ?? null,
      execution: executionFor(ev, dB),
      outcome: dB.outcome,
    },
    agentA: {
      chosenAction: dA.action,
      chosenLabel: ACTION_LABELS[dA.action],
      score: Number(dA.score.toFixed(4)),
      outcome: dA.outcome,
    },
    truth: ev.truth,
  };
}

function executionFor(ev, d) {
  if (d.action === 'escalate') {
    return {
      mode: 'none',
      detail: 'No call made. Case routed to the exception queue.',
      mocked: false,
    };
  }
  if (d.action === 'silent_retry') {
    return {
      mode: 'razorpay_test_mode',
      detail: `POST /v1/subscriptions/${ev.subscriptionId}/retry — test mode`,
      mocked: false,
    };
  }
  if (d.action === 'card_update_request') {
    return {
      mode: 'razorpay_test_mode',
      detail: `POST /v1/payment_links — card update, ₹${(ev.amountPaise / 100).toFixed(2)}, test mode. Delivery mocked.`,
      mocked: true,
    };
  }
  return {
    mode: 'razorpay_test_mode',
    detail: `POST /v1/payment_links — ₹${(ev.amountPaise / 100).toFixed(2)}, test mode. ${d.action === 'payment_link_whatsapp' ? 'WhatsApp' : 'SMS'} delivery mocked.`,
    mocked: true,
  };
}

const traces = events.map((ev) => buildTrace(ev, agentA, agentB));

/**
 * Measured, not asserted. The site renders this rather than a hand-written
 * "roughly 85%", so the claim about deterministic coverage can never drift away
 * from the batch it is describing.
 */
const deterministicLookups = traces.filter(
  (t) => t.diagnosis.method === 'deterministic_lookup',
).length;
const pipelineStats = {
  deterministicLookups,
  llmFallbacks: traces.length - deterministicLookups,
  deterministicShare: Number((deterministicLookups / traces.length).toFixed(4)),
};

const eventRows = events.map((ev) => {
  const dA = agentA.decisions.get(ev.eventId);
  const dB = agentB.decisions.get(ev.eventId);
  return {
    eventId: ev.eventId,
    paymentId: ev.paymentId,
    subscriptionId: ev.subscriptionId,
    failedAt: ev.failedAt,
    amountPaise: ev.amountPaise,
    planName: ev.planName,
    method: ev.method,
    issuer: ev.issuer,
    reasonCode: ev.reasonCode,
    reasonLabel: ev.reasonLabel,
    failureSide: ev.failureSide,
    minutesSinceFailure: ev.minutesSinceFailure,
    messageClass: dB.messageClass,
    upliftHat: Number(ev._uplift.upliftHat.toFixed(4)),
    baselineScore: Number(ev._baselineScore.toFixed(4)),
    agentA: {
      action: dA.action,
      label: ACTION_LABELS[dA.action],
      contacted: dA.contacted,
      recovered: dA.outcome.recovered,
      churned: dA.outcome.churned,
    },
    agentB: {
      action: dB.action,
      label: ACTION_LABELS[dB.action],
      contacted: dB.contacted,
      recovered: dB.outcome.recovered,
      churned: dB.outcome.churned,
      // `blockedBy` is the rule that stopped the action finally taken;
      // `deniedBy` is the rule that stopped what the agent wanted to do first.
      // Reporting only the former loses the interesting half of the story.
      blockedBy: dB.blockedBy,
      deniedBy: dB.deniedBy ?? null,
      deniedAction: dB.deniedAction ? ACTION_LABELS[dB.deniedAction] : null,
    },
    truthSegment: ev.truth.segment,
  };
});

/** Every case Agent B declined to touch, and the reason it declined. */
const sleepingDogs = events
  .filter((ev) => {
    const dB = agentB.decisions.get(ev.eventId);
    return !dB.contacted && !ev._bWants;
  })
  .map((ev) => {
    const dA = agentA.decisions.get(ev.eventId);
    const dB = agentB.decisions.get(ev.eventId);
    const churnDelta = ev.truth.churnTreat - ev.truth.churnControl;
    return {
      eventId: ev.eventId,
      subscriptionId: ev.subscriptionId,
      amountPaise: ev.amountPaise,
      planName: ev.planName,
      reasonCode: ev.reasonCode,
      upliftHat: Number(ev._uplift.upliftHat.toFixed(4)),
      decision: dB.action,
      decisionLabel: ACTION_LABELS[dB.action],
      blockedBy: dB.blockedBy ?? 'STOP_ON_NEGATIVE_UPLIFT',
      baselineWouldContact: dA.contacted,
      truthSegment: ev.truth.segment,
      churnDelta: Number(churnDelta.toFixed(4)),
      estimatedDamageAvoidedPaise: dA.contacted
        ? Math.round(Math.max(0, churnDelta) * ev.amountPaise * CONFIG.churnResidualCycles)
        : 0,
      engagementScore: ev.engagementScore,
    };
  })
  .sort((a, b) => b.estimatedDamageAvoidedPaise - a.estimatedDamageAvoidedPaise);

/** Cases Agent B could not resolve without a human. */
const exceptions = events
  .filter((ev) => agentB.decisions.get(ev.eventId).action === 'escalate')
  .map((ev) => {
    const dB = agentB.decisions.get(ev.eventId);
    return {
      eventId: ev.eventId,
      subscriptionId: ev.subscriptionId,
      paymentId: ev.paymentId,
      amountPaise: ev.amountPaise,
      reasonCode: ev.reasonCode,
      reasonLabel: ev.reasonLabel,
      raisedAt: ev.failedAt,
      blockedBy: dB.blockedBy ?? 'ESCALATE_UNRESOLVED',
      deniedAction: dB.deniedAction ? ACTION_LABELS[dB.deniedAction] : ACTION_LABELS.silent_retry,
      structuredReason:
        dB.gate.find((g) => g.verdict === 'BLOCK')?.note ?? 'Action ladder exhausted.',
      attemptsThisCycle: ev.attemptsThisCycle,
      contactsLast7d: ev.contactsLast7d,
    };
  })
  .sort((a, b) => b.amountPaise - a.amountPaise);

/* ------------------------------------------------------------------ *
 * Replay script for the console's stream, so Demo Mode looks like the
 * live SSE feed rather than a table that appears all at once.
 * ------------------------------------------------------------------ */

const streamScript = (() => {
  const lines = [];
  let processed = 0;
  let recovered = 0;
  let contacts = 0;
  let dogs = 0;
  let escalated = 0;

  lines.push({
    kind: 'system',
    text: `batch ${'bat_sample_20260903'} · ${events.length} failed payment events queued`,
    counters: null,
  });
  lines.push({
    kind: 'system',
    text: 'shadow ledger armed — baseline policy runs on the same events in parallel',
    counters: null,
  });

  for (const ev of events) {
    const dB = agentB.decisions.get(ev.eventId);
    processed += 1;
    if (dB.outcome.recovered) recovered += ev.amountPaise;
    if (dB.contacted) contacts += 1;
    if (!dB.contacted && !ev._bWants && ev.truth.segment === 'sleeping_dog') dogs += 1;
    if (dB.action === 'escalate') escalated += 1;

    lines.push({
      kind: dB.action === 'escalate' ? 'warn' : dB.blockedBy ? 'gate' : 'decision',
      eventId: ev.eventId,
      text:
        dB.action === 'escalate'
          ? `${ev.eventId} ${ev.reasonCode} → escalated · ${dB.blockedBy}`
          : dB.blockedBy
            ? `${ev.eventId} ${ev.reasonCode} → ${dB.action} · gated by ${dB.blockedBy}`
            : `${ev.eventId} ${ev.reasonCode} → ${dB.action}`,
      counters: {
        processed,
        recoveredPaise: recovered,
        contacts,
        sleepingDogsAvoided: dogs,
        escalated,
      },
    });
  }

  lines.push({
    kind: 'system',
    text: 'batch complete — comparison written to the shadow ledger',
    counters: null,
  });
  return lines;
})();

/* ------------------------------------------------------------------ *
 * Emit
 * ------------------------------------------------------------------ */

const batch = {
  source: 'sample',
  batchId: 'bat_sample_20260903',
  label: 'Bundled synthetic batch',
  generatedBy: 'scripts/generate-sample-batch.mjs',
  seed: 20260903,
  honesty: {
    whatIsSynthetic:
      'Every event, every reason code, every outcome and every rupee figure in this file is simulated. The batch is synthetic by design: the four-segment framework requires both potential outcomes for the same event, which no production system can observe.',
    whatIsReal:
      'The policy rules and the regulation they cite, the seven-layer pipeline they gate, and the Razorpay test-mode API calls made by the backend executor.',
    curveNote:
      'The uplift curves are exact, not estimated — both branches are known. Against real data this measurement would require a randomised holdout and would carry confidence intervals.',
    noiseNote:
      'At 500 events and roughly 60 contacts per agent, the realised rupee difference between the two policies is inside sampling noise, and should not be read as a headline. What is robust is where each agent spent its budget: that difference comes from the ranking objective, not from which way a coin landed.',
    knownWeakness:
      'Individual sleeping-dog identification has a hard ceiling: given the observable features, segment membership is latent, so a dog and a persuadable with the same profile are indistinguishable to ANY estimator — the Bayes-optimal ranking on this world still touches roughly seven dogs per five-hundred-event batch. What uplift ranking buys, robustly, is where the budget goes and a churn-priced value estimate that declines the clearly dangerous contacts; it does not buy immunity from latent segments or a noisy estimate.',
  },
  assumptions: [
    {
      key: 'contactBudget',
      value: CONFIG.contactBudget,
      note: 'Outreach budget for the batch. Both agents get the same one; only the ranking objective differs.',
    },
    {
      key: 'contactCostPaise',
      value: CONFIG.contactCostPaise,
      note: 'Assumed direct marginal cost of one outbound message, in paise.',
    },
    {
      key: 'churnResidualCycles',
      value: CONFIG.churnResidualCycles,
      note: 'Assumed residual subscription value, in billing cycles, used to price churn caused by outreach.',
    },
    {
      key: 'baselineProbabilityThreshold',
      value: CONFIG.baselineProbabilityThreshold,
      note: 'Agent A contacts anything it scores at or above this probability of paying after contact.',
    },
    {
      key: 'upliftThreshold',
      value: CONFIG.upliftThreshold,
      note: 'Agent B needs estimated uplift above this before it will spend a contact.',
    },
    {
      key: 'estimatorNoiseSd',
      value: CONFIG.estimatorNoiseSd,
      note: 'Std. dev. of simulated estimator error on each potential outcome. Neither agent sees ground truth.',
    },
  ],
  currency: 'INR',
  eventCount: events.length,
  pipelineStats,
  agents: {
    A: {
      key: 'A',
      name: 'Baseline',
      objective: 'Recovery probability',
      description:
        'Ranks by P(recovers | outreach) and contacts everything above a fixed threshold, within the same budget and the same policy gate. This is not a strawman — it runs identical compliance rules. It simply optimises the wrong quantity.',
      metrics: agentA.metrics,
      segments: segmentBreakdown(agentA),
      curve: curveA,
    },
    B: {
      key: 'B',
      name: 'RecoverOps',
      objective: 'Causal uplift',
      description:
        'Ranks by the expected net value of the contact: recovery uplift, minus churn uplift priced at residual subscription value, minus message cost. Spends the same budget only where contact changes the outcome for the better; declined cases are logged as no-action, not dropped.',
      metrics: agentB.metrics,
      segments: segmentBreakdown(agentB),
      curve: curveB,
    },
  },
  events: eventRows,
  sleepingDogs,
  exceptions,
  streamScript,
};

mkdirSync(resolve(ROOT, 'data'), { recursive: true });
writeFileSync(resolve(ROOT, 'data/sample-batch.json'), `${JSON.stringify(batch, null, 2)}\n`);
writeFileSync(
  resolve(ROOT, 'data/sample-traces.json'),
  `${JSON.stringify({ source: 'sample', batchId: batch.batchId, traces }, null, 2)}\n`,
);

console.log('data/sample-batch.json + data/sample-traces.json written\n');
const row = (m) => ({
  contacts: m.contactsMade,
  wasted: m.wastedContacts,
  'outreach-driven recoveries': m.outreachDrivenRecoveries,
  'sleeping dogs touched': m.sleepingDogsTouched,
  'cancellations caused': m.outreachCausedCancellations,
  recovered: `₹${(m.recoveredPaise / 100).toLocaleString('en-IN')}`,
  net: `₹${(m.netValuePaise / 100).toLocaleString('en-IN')}`,
});

console.table({
  'Agent A (probability)': row(agentA.metrics),
  'Agent B (uplift)': row(agentB.metrics),
});
