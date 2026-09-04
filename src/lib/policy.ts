/**
 * The named policy gate. Rules are ordered: the first BLOCK stops evaluation,
 * and everything after it is recorded N/A rather than silently skipped, so an
 * audit trail always shows why a rule did not fire.
 *
 * `citation` is only set where the rule enforces an actual published rule. The
 * rest are product policy, and are not dressed up as regulation.
 */

export interface PolicyRule {
  id: string;
  description: string;
  citation?: string;
  category: 'compliance' | 'frequency' | 'risk' | 'economics';
}

export const POLICY_RULES: PolicyRule[] = [
  {
    id: 'MSG_CLASS_TRANSACTIONAL_30MIN',
    description:
      'Payment-retry messages qualify as transactional only within 30 minutes of the failed attempt; after that, reclassify and re-gate.',
    citation: 'TCCCPR',
    category: 'compliance',
  },
  {
    id: 'QUIET_HOURS_2100_0900_IST',
    description: 'No promotional-class outreach outside 09:00–21:00 IST.',
    category: 'compliance',
  },
  {
    id: 'DND_SCRUB_PROMOTIONAL',
    description: 'Promotional-class messages require a consent record; blocked otherwise.',
    citation: 'TCCCPR',
    category: 'compliance',
  },
  {
    id: 'MAX_RETRY_3_PER_CYCLE',
    description: 'Max 3 charge attempts per billing cycle, mirroring Razorpay’s own T+3 behaviour.',
    category: 'frequency',
  },
  {
    id: 'NETWORK_RETRY_CAP_30D',
    description: 'Respect the card network’s rolling 30-day retry ceiling.',
    category: 'frequency',
  },
  {
    id: 'NO_RETRY_ON_FRAUD',
    description: 'Hard block on suspected-fraud reason codes. Never retried.',
    category: 'risk',
  },
  {
    id: 'SILENT_FIRST',
    description: 'Attempt at least one silent retry before any customer contact.',
    category: 'frequency',
  },
  {
    id: 'MAX_CONTACTS_2_PER_7D',
    description: 'No more than 2 customer contacts in any rolling 7 days.',
    category: 'frequency',
  },
  {
    id: 'BACKOFF_ON_ISSUER_DOWN',
    description:
      'For bank, gateway or network-side failures: exponential backoff, zero customer contact.',
    category: 'risk',
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
      'If estimated uplift is at or below zero, take no action. Sleeping-dog protection.',
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
    body: 'A deterministic reason-code lookup resolves most events at zero cost and zero added latency. The language model is only called for codes that are ambiguous, unmapped, or arrive as free text.',
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
    body: 'For each candidate action, estimates the causal effect of taking it versus doing nothing, then picks the highest expected-value action. If no action has positive uplift, it does nothing — and logs that it did nothing.',
  },
  {
    n: '04',
    title: 'Policy gate',
    body: 'A named, ordered rule set that can block any action regardless of what the uplift engine wants. Compliance is not a scoring input the model can trade away.',
  },
  {
    n: '05',
    title: 'Executor',
    body: 'Real Razorpay test-mode API calls for orders, payment links and subscription retries. Outbound SMS and WhatsApp delivery is mocked, and labelled as mocked wherever it appears.',
  },
  {
    n: '06',
    title: 'Audit ledger',
    body: 'Every decision, every gate result and every rupee is written down. A blocked action leaves the same trail as an executed one.',
  },
  {
    n: '07',
    title: 'Shadow ledger',
    body: 'A baseline policy runs on the same events in parallel, so every batch produces its own comparison. There is no opportunity to pick a favourable batch after the fact.',
  },
] as const;
