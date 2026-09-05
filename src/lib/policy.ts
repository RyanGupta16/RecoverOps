/**
 * The named policy gate, v2. Rules are ordered: the first BLOCK stops
 * evaluation, and everything after it is recorded N/A rather than silently
 * skipped, so an audit trail always shows why a rule did not fire.
 *
 * `citation` is only set where the rule enforces an actual published rule. The
 * rest are product policy, and are not dressed up as regulation. This list
 * mirrors backend/app/policy.py (RULES) — the backend also serves it from
 * GET /api/policy/rules.
 */

export interface PolicyRule {
  id: string;
  description: string;
  citation?: string;
  category: 'compliance' | 'frequency' | 'risk' | 'economics';
}

export const POLICY_RULES: PolicyRule[] = [
  {
    id: 'NO_RETRY_ON_FRAUD',
    description:
      'Hard block on suspected-fraud and risk-hold reason codes. Never retried, never contacted.',
    category: 'risk',
  },
  {
    id: 'HARD_DECLINE_NO_RETRY',
    description:
      'A hard decline for the instrument — expired, blocked, closed, revoked — is never retried on the same instrument. Only an instrument change can fix it.',
    citation: 'Visa reattempt rules',
    category: 'risk',
  },
  {
    id: 'STOP_ON_DISPUTE',
    description: 'No action of any kind while a dispute or chargeback is open on the counterparty.',
    category: 'risk',
  },
  {
    id: 'PTP_ACTIVE_HOLD',
    description:
      'While a promise to pay is live, nothing happens on that counterparty — not outreach, not a silent retry. The agreed date is the agreement.',
    citation: 'RBI recovery-agent norms',
    category: 'risk',
  },
  {
    id: 'DEGRADATION_HOLD',
    description:
      'No customer-facing action on an instrument inside a live degradation cohort — declared by Razorpay’s downtime feed or detected in our own success rate. Auto-releases when it clears.',
    citation: 'Razorpay payment downtime feed',
    category: 'risk',
  },
  {
    id: 'MERCHANT_SIDE_NO_CONTACT',
    description:
      'Merchant-side failures (error_source = business) are not the customer’s problem: zero customer contact, routed to merchant operations.',
    category: 'risk',
  },
  {
    id: 'MSG_CLASS_TCCCPR_2025',
    description:
      'Classify the message: transactional only if the customer initiated the transaction and it is within 30 minutes; service if it informs about a product the customer holds; promotional otherwise.',
    citation: 'TCCCPR 2025 cl. 2(bt), 2(bh), 2(au)',
    category: 'compliance',
  },
  {
    id: 'MIXED_CONTENT_IS_PROMOTIONAL',
    description:
      'Any incentive, discount or win-back offer reclassifies the whole message as promotional and re-gates it.',
    citation: 'TCCCPR 2025 cl. 2(au) proviso',
    category: 'compliance',
  },
  {
    id: 'QUIET_HOURS_2100_0900_IST',
    description: 'No promotional-class outreach outside 09:00–21:00 IST.',
    citation: 'TCCCPR preference bands',
    category: 'compliance',
  },
  {
    id: 'DUES_CONTACT_WINDOW_0800_1900',
    description:
      'Anything that reads as dues collection — overdue receivables, broken-promise follow-ups — is contacted only 08:00–19:00 IST, on any channel.',
    citation: 'RBI recovery-agent norms',
    category: 'compliance',
  },
  {
    id: 'DND_SCRUB_PROMOTIONAL',
    description:
      'Promotional-class messages are scrubbed against the preference register and need a consent record; blocked otherwise.',
    citation: 'TCCCPR',
    category: 'compliance',
  },
  {
    id: 'CONSENT_PURPOSE_MATCH',
    description:
      'Promotional outreach needs a consent record whose purpose covers it; explicit consent given to complete a purchase expires after seven days.',
    citation: 'DPDP Rules 2025 · TCCCPR 2025 cl. 2(bh)',
    category: 'compliance',
  },
  {
    id: 'NO_THIRD_PARTY_CONTACT',
    description:
      'Only the counterparty or a guarantor may be contacted about dues — never a relative, colleague or reference.',
    citation: 'RBI recovery-agent norms',
    category: 'compliance',
  },
  {
    id: 'MANDATE_ATTEMPT_CAP_4',
    description:
      'A UPI Autopay or e-mandate cycle allows one execution plus three retries. Four attempts used means no more this cycle.',
    citation: 'NPCI UPI Autopay',
    category: 'frequency',
  },
  {
    id: 'MANDATE_EXECUTION_WINDOW',
    description:
      'UPI Autopay executions run in NPCI’s non-peak windows — before 10:00, 13:00–17:00, or after 21:30 IST — not whenever the scheduler feels like it.',
    citation: 'NPCI execution windows',
    category: 'frequency',
  },
  {
    id: 'PRE_DEBIT_NOTICE_24H',
    description:
      'A recurring debit needs a pre-debit notification to the customer at least 24 hours beforehand.',
    citation: 'RBI E-Mandate Framework 2026',
    category: 'compliance',
  },
  {
    id: 'AFA_THRESHOLD',
    description:
      'A recurring debit above the AFA-free ceiling (₹15,000; ₹1,00,000 for mutual funds, insurance and card bills) cannot be retried silently — it needs the customer’s authentication.',
    citation: 'RBI E-Mandate Framework 2026',
    category: 'compliance',
  },
  {
    id: 'MAX_RETRY_3_PER_CYCLE',
    description:
      'Max 3 charge attempts per billing cycle on cards, mirroring Razorpay’s own T+3 behaviour.',
    category: 'frequency',
  },
  {
    id: 'NETWORK_RETRY_CAP_30D',
    description:
      'Respect the card network’s rolling 30-day reattempt ceiling: 15 on Visa, 10 on Mastercard.',
    citation: 'Visa / Mastercard',
    category: 'frequency',
  },
  {
    id: 'SILENT_FIRST',
    description:
      'Attempt at least one silent retry before any customer contact — unless the decline is hard and a silent retry cannot succeed.',
    category: 'frequency',
  },
  {
    id: 'MAX_CONTACTS_2_PER_7D',
    description: 'No more than 2 customer contacts in any rolling 7 days.',
    category: 'frequency',
  },
  {
    id: 'VOICE_FREQ_3D_8W',
    description:
      'Voice calls are capped harder than messages: at most 3 in a day and 8 in a week to one subscriber.',
    citation: 'TRAI promotional-call guidance',
    category: 'frequency',
  },
  {
    id: 'BACKOFF_ON_ISSUER_DOWN',
    description:
      'For bank, gateway or network-side failures: exponential backoff, zero customer contact.',
    category: 'risk',
  },
  {
    id: 'VOICE_ELIGIBILITY',
    description:
      'A voice call needs the right number series for its class (140 promotional, 1600 service/transactional), a recording disclosure, text channels already tried, and a value that justifies the cost.',
    citation: 'TRAI auto-dialler / robocall series',
    category: 'risk',
  },
  {
    id: 'MSMED_LEVER_AFTER_STATUTORY_WINDOW',
    description:
      'A statutory interest notice may only be sent after the MSMED payment window has lapsed (15 days without a written agreement, 45 with one) and only when the supplier is a registered micro or small enterprise.',
    citation: 'MSMED Act 2006 s.15–16 · IT Act s.43B(h)',
    category: 'compliance',
  },
  {
    id: 'DISCOUNT_CAP_5PCT',
    description:
      'Any incentive capped at 5% of order value, with a cumulative batch budget ceiling.',
    category: 'economics',
  },
  {
    id: 'STOP_ON_NEGATIVE_UPLIFT',
    description:
      'If estimated uplift is at or below the threshold, or the contact’s expected net value is negative, take no action. Sleeping-dog protection.',
    category: 'economics',
  },
  {
    id: 'APPROVAL_ABOVE_THRESHOLD',
    description:
      'Outreach on a leak above the merchant’s approval threshold waits for a human; silent retries proceed.',
    category: 'economics',
  },
  {
    id: 'ESCALATE_UNRESOLVED',
    description: 'After exhausting the ladder, route to the human queue with a structured reason.',
    category: 'economics',
  },
];

export const RULES_BY_ID = new Map(POLICY_RULES.map((r) => [r.id, r]));

export const PIPELINE_LAYERS = [
  {
    n: '01',
    title: 'Diagnosis',
    body: 'Razorpay’s error_reason maps deterministically onto thirteen reason families — including the merchant-side and blocked-instrument families a probability-ranked system gets wrong. The language model is only called for codes that are ambiguous, unmapped, or arrive as free text.',
    // Filled from the batch on screen rather than written into the copy, so the
    // coverage claim cannot drift away from the data it describes.
    stat: 'diagnosisShare' as const,
  },
  {
    n: '02',
    title: 'Retrieval',
    body: 'Grounded in Razorpay’s published error-code corpus, plus a case memory of similar past failures and what actually happened when each intervention was tried on them.',
  },
  {
    n: '03',
    title: 'Uplift engine',
    body: 'For each candidate action, estimates the causal effect of taking it versus doing nothing, prices the message at the class the gate will assign, and picks the highest expected-value action. If no action has positive net value, it does nothing — and logs that it did nothing.',
  },
  {
    n: '04',
    title: 'Policy gate',
    body: 'A named, ordered rule set that can block any action regardless of what the uplift engine wants. Each rule carries the regulation it enforces — TCCCPR 2025, the RBI e-mandate framework, NPCI mandate caps, card-network reattempt rules — or says plainly that it is product policy.',
  },
  {
    n: '05',
    title: 'Executor',
    body: 'Real Razorpay test-mode API calls for orders, payment links and invoice notifications. Outbound SMS and WhatsApp delivery is mocked, and labelled as mocked wherever it appears.',
  },
  {
    n: '06',
    title: 'Audit ledger',
    body: 'Every decision, every gate result and every rupee is written to an append-only, hash-chained log. A blocked action leaves the same trail as an executed one, and the chain can be verified from genesis on request.',
  },
  {
    n: '07',
    title: 'Shadow ledger',
    body: 'A baseline policy runs on the same events in parallel, so every batch produces its own comparison. There is no opportunity to pick a favourable batch after the fact.',
  },
] as const;
