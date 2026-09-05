/** Shapes shared by the FastAPI backend and the bundled sample batch. */

export type DataSource = 'live' | 'sample';

export type Segment = 'sure_thing' | 'persuadable' | 'lost_cause' | 'sleeping_dog';

export type Verdict = 'PASS' | 'BLOCK' | 'N/A';

/** TCCCPR 2025 three-class model. `null` when the action carries no message. */
export type MessageClass = 'transactional' | 'service' | 'promotional';

export type FailureSide = 'customer' | 'issuer' | 'risk' | 'merchant';

/** Where a batch's leaks came from. */
export type LeakSourceName = 'simulator' | 'razorpay' | 'file';

/** Synthetic batches know both branches; real ones know neither until outcomes arrive. */
export type DataMode = 'synthetic' | 'real';

export type LeakKind =
  | 'subscription_failure'
  | 'mandate_failure'
  | 'checkout_abandonment'
  | 'receivable_overdue'
  | 'degradation_cohort';

export interface GateResult {
  ruleId: string;
  verdict: Verdict;
  note: string;
  /** Regulation the rule enforces; null for product policy. */
  citation?: string | null;
}

export interface AgentMetrics {
  eventsProcessed: number;
  contactsMade: number;
  contactBudget: number;
  silentRetries: number;
  escalations: number;
  recoveredCount: number;
  recoveredPaise: number;
  recoveryRate: number;
  sleepingDogsTouched: number;
  /** Contacts where the outcome was identical to staying quiet — pure spend. */
  wastedContacts: number;
  /** Recoveries that happened only because of outreach. */
  outreachDrivenRecoveries: number;
  /** Cancellations the outreach itself caused. */
  outreachCausedCancellations: number;
  outreachCausedChurnLossPaise: number;
  /** Total cancellations, mostly unavoidable. Never a headline. */
  churnedSubscriptions: number;
  contactCostPaise: number;
  netValuePaise: number;
  /** Real data: events whose outcome is not yet known. Zero on synthetic batches. */
  outcomesPending?: number;
  /** Events assigned to the randomised control arm (silent path for both agents). */
  holdoutEvents?: number;
}

export interface CurvePoint {
  contacts: number;
  incrementalRecoveries: number;
  incrementalPaise: number;
  netPaise: number;
}

export interface SegmentRow {
  segment: Segment;
  population: number;
  contacted: number;
  shareOfBudget: number;
  trueUplift: number;
}

export interface AgentSummary {
  key: 'A' | 'B';
  name: string;
  objective: string;
  description: string;
  metrics: AgentMetrics;
  segments: SegmentRow[];
  curve: CurvePoint[];
}

export interface EventDecision {
  action: string;
  label: string;
  contacted: boolean;
  /** null while the outcome is unknown (real data, before attribution). */
  recovered: boolean | null;
  churned: boolean | null;
  /** Rule that stopped the action finally taken. */
  blockedBy?: string | null;
  /** Rule that stopped what the agent wanted to do first. */
  deniedBy?: string | null;
  deniedAction?: string | null;
}

export interface BatchEvent {
  eventId: string;
  kind?: LeakKind;
  source?: LeakSourceName;
  paymentId: string;
  subscriptionId: string;
  failedAt: string;
  amountPaise: number;
  planName: string;
  method: string;
  issuer: string;
  network?: string | null;
  reasonCode: string;
  reasonLabel: string;
  /** Razorpay's error_reason as received, when the leak came from real data. */
  rawReason?: string | null;
  failureSide: FailureSide;
  minutesSinceFailure: number;
  messageClass: MessageClass | null;
  upliftHat: number;
  baselineScore: number;
  holdout?: boolean;
  agentA: EventDecision;
  agentB: EventDecision;
  /** null on real data — segment membership is never observed. */
  truthSegment: Segment | null;
}

export interface SleepingDogRecord {
  eventId: string;
  subscriptionId: string;
  amountPaise: number;
  planName: string;
  reasonCode: string;
  upliftHat: number;
  decision: string;
  decisionLabel: string;
  blockedBy: string;
  baselineWouldContact: boolean;
  truthSegment: Segment | null;
  /** True churn effect on synthetic data; the model's churn-uplift estimate on real data. */
  churnDelta: number;
  churnDeltaIsEstimate?: boolean;
  estimatedDamageAvoidedPaise: number;
  engagementScore: number;
}

export interface ExceptionRecord {
  eventId: string;
  subscriptionId: string;
  paymentId: string;
  amountPaise: number;
  reasonCode: string;
  reasonLabel: string;
  raisedAt: string;
  blockedBy: string;
  deniedAction: string;
  structuredReason: string;
  attemptsThisCycle: number;
  contactsLast7d: number;
  /** Merchant-side failures go to merchant operations, not customer-facing humans. */
  queue?: 'human' | 'merchant_ops';
}

export interface StreamLine {
  kind: 'system' | 'decision' | 'gate' | 'warn';
  eventId?: string;
  text: string;
  counters: {
    processed: number;
    recoveredPaise: number;
    contacts: number;
    sleepingDogsAvoided: number;
    escalated: number;
  } | null;
}

export interface Assumption {
  key: string;
  value: number;
  note: string;
}

export interface BatchResult {
  source: DataSource;
  batchId: string;
  label: string;
  generatedBy?: string;
  seed?: number;
  /** Absent on batches stored before v2; treat as synthetic. */
  dataMode?: DataMode;
  sourceName?: LeakSourceName;
  sourceMeta?: Record<string, unknown>;
  merchant?: string;
  /** 'learned' — trained CATE estimator; 'priors' — reason-family priors (real data before the learning loop). */
  estimatorMode?: 'learned' | 'priors';
  honesty: {
    whatIsSynthetic: string;
    whatIsReal: string;
    curveNote: string;
    noiseNote: string;
    knownWeakness: string;
  };
  assumptions: Assumption[];
  currency: string;
  eventCount: number;
  pipelineStats: {
    deterministicLookups: number;
    llmFallbacks: number;
    deterministicShare: number;
  };
  agents: { A: AgentSummary; B: AgentSummary };
  events: BatchEvent[];
  sleepingDogs: SleepingDogRecord[];
  exceptions: ExceptionRecord[];
  streamScript: StreamLine[];
}

export interface Precedent {
  source: string;
  ref: string;
  note: string;
}

export interface ActionEstimate {
  action: string;
  label: string;
  estimatedUplift: number;
  expectedValuePaise: number;
  eligible: boolean;
  messageClass?: MessageClass | null;
  costPaise?: number;
}

/** The leak as the pipeline saw it — no raw contact details, only a hash. */
export interface LeakRow {
  eventId: string;
  kind: LeakKind;
  source: LeakSourceName;
  paymentId: string;
  subscriptionId: string;
  invoiceId: string | null;
  customerId: string;
  counterpartyType: 'consumer' | 'business';
  contactHash: string | null;
  failedAt: string;
  amountPaise: number;
  planName: string;
  method: string;
  issuer: string;
  network: string | null;
  psp: string | null;
  reasonCode: string;
  reasonLabel: string;
  failureSide: FailureSide;
  rawReason: string | null;
  reasonConfidence: 'high' | 'medium' | 'low';
  hardDecline: boolean;
  merchantSide: boolean;
  minutesSinceFailure: number;
  attemptsThisCycle: number;
  contactsLast7d: number;
  retries30d: number;
  featuresAreProxies: boolean;
  holdout: boolean;
  synthetic: boolean;
}

export type Outcome = { recovered: boolean; churned: boolean } | null;

export interface DecisionTrace {
  eventId: string;
  kind?: LeakKind;
  source?: LeakSourceName;
  dataMode?: DataMode;
  leak?: LeakRow;
  diagnosis: {
    method: 'deterministic_lookup' | 'llm_fallback';
    reasonCode: string;
    reasonLabel: string;
    failureSide: string;
    latencyMs: number;
    note: string;
  };
  precedents: Precedent[];
  uplift: {
    estimator: string;
    estimatorMode?: 'learned' | 'priors';
    pControlHat: number;
    pTreatHat: number;
    upliftHat: number;
    churnUpliftHat?: number;
    perAction: ActionEstimate[];
  };
  agentB: {
    chosenAction: string;
    chosenLabel: string;
    messageClass: MessageClass | null;
    gate: GateResult[];
    blockedBy: string | null;
    deniedAction: string | null;
    deniedBy: string | null;
    execution: { mode: string; detail: string; mocked: boolean };
    outcome: Outcome;
    costPaise?: number;
  };
  agentA: {
    chosenAction: string;
    chosenLabel: string;
    score: number;
    outcome: Outcome;
  };
  /** null on real data — the branch not taken is unobserved. */
  truth: {
    segment: Segment;
    pControl: number;
    pTreat: number;
    churnControl: number;
    churnTreat: number;
  } | null;
}

/** GET /api/sources */
export interface LeakSourceInfo {
  name: LeakSourceName;
  available: boolean;
  dataMode: DataMode;
  note: string;
  files?: FileIngestMeta[];
}

/** POST /api/ingest/file and the entries under a file source. */
export interface FileIngestMeta {
  fileId: string;
  filename: string;
  uploadedAt: string;
  rows: number;
  failedRows: number;
  warnings: string[];
  leaks: number;
  amountPaise: number;
  byFamily: Record<string, number>;
  byMethod: Record<string, number>;
  byKind: Record<string, number>;
  lowConfidence: number;
}

export interface RunBatchOptions {
  source?: LeakSourceName;
  seed?: number;
  count?: number;
  fileId?: string;
  days?: number;
  limit?: number;
}

/** The slim per-agent figures the history list shows. Mirrors Store.summarize in the backend. */
export interface AgentSummaryMetrics {
  contactsMade: number;
  recoveredPaise: number;
  netValuePaise: number;
  sleepingDogsTouched: number;
  wastedContacts: number;
  escalations: number;
  recoveryRate: number;
  outcomesPending?: number;
}

/** One row of batch history — GET /api/batches. */
export interface BatchSummary {
  batchId: string;
  label: string | null;
  source: DataSource;
  seed: number | null;
  eventCount: number;
  generatedBy?: string | null;
  createdAt: string;
  dataMode?: DataMode;
  sourceName?: LeakSourceName;
  agents: { A: AgentSummaryMetrics; B: AgentSummaryMetrics };
  sleepingDogs: number;
  exceptions: number;
  pipelineStats?: BatchResult['pipelineStats'] | null;
}

/** GET /api/audit/verify — the hash chain walked from genesis. */
export interface AuditVerification {
  ok: boolean;
  rows: number;
  firstBreak: number | null;
  head: string;
}

/** One append-only audit row. `hash` covers `prevHash` + the canonical body. */
export interface AuditEntry {
  seq: number;
  at: string;
  actor: string;
  kind: string;
  ref: string | null;
  payload: Record<string, unknown>;
  prevHash: string;
  hash: string;
}

/** Every response carries where it came from, so the Demo Mode badge is driven by data. */
export interface Sourced<T> {
  data: T;
  source: DataSource;
}
