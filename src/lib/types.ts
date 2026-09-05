/** Shapes shared by the FastAPI backend and the bundled sample batch. */

export type DataSource = 'live' | 'sample';

export type Segment = 'sure_thing' | 'persuadable' | 'lost_cause' | 'sleeping_dog';

export type Verdict = 'PASS' | 'BLOCK' | 'N/A';

/** TCCCPR 2025 three-class model. `null` when the action carries no message. */
export type MessageClass = 'transactional' | 'service' | 'promotional';

export type FailureSide = 'customer' | 'issuer' | 'risk' | 'merchant';

/** Where a batch's leaks came from. */
export type LeakSourceName = 'simulator' | 'razorpay' | 'file' | 'receivables' | 'checkout';

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
  /** Agent B decisions flipped at random for exploration (real data only). */
  exploredDecisions?: number;
}

/** A live degradation cohort — declared by Razorpay or found by our detector. */
export interface Cohort {
  key: string;
  source: 'razorpay' | 'detector';
  method: string;
  instrument: Record<string, string>;
  severity: 'high' | 'medium' | 'low';
  beganAt: string;
  endedAt: string | null;
  status: string;
  detail: string;
  externalId: string | null;
  successRate: number | null;
  baselineRate: number | null;
  attempts: number;
  eventsHeld?: number;
  lastSeen?: string;
  sightings?: number;
}

export interface DegradationView {
  cohorts: Cohort[];
  live: number;
  feedAvailable: boolean;
  feedError: string | null;
  sources: string[];
  eventsHeld?: Record<string, number>;
  history?: Cohort[];
  detectorNote?: string;
}

export type PromiseState =
  | 'open' | 'reminded' | 'kept' | 'partially_kept' | 'broken'
  | 'recontacted' | 'second_broken' | 'risk_escalated' | 'cancelled';

export interface PromiseRecord {
  promiseId: number;
  counterpartyId: string;
  eventId: string | null;
  amountPaise: number;
  dueAt: string;
  state: PromiseState;
  capturedVia: string;
  verbatim: string;
  createdAt: string;
  remindedAt: string | null;
  resolvedAt: string | null;
  verifiedBy: string | null;
  amountPaidPaise: number;
  brokenCount: number;
  open: boolean;
}

export interface PromiseStats {
  total: number;
  byState: Record<string, number>;
  byChannel: Record<string, { kept: number; broken: number; total: number }>;
  keptRate: number | null;
  open: number;
  promisedPaise: number;
  paidPaise: number;
  brokenAfterDays: number;
  recontactWithinHours: number;
}

export interface PromisesView {
  stats: PromiseStats;
  promises: PromiseRecord[];
}

/** Per-leak-kind allocation of one shared contact budget. */
export interface KindRow {
  kind: LeakKind;
  leaks: number;
  atRiskPaise: number;
  contacted: number;
  costPaise: number;
  expectedValuePaise: number;
  escalated: number;
  heldByDegradation: number;
  heldByPromise: number;
  valuePerRupeeSpent: number | null;
}

export interface LadderRow {
  ageing: string;
  invoices: number;
  amountPaise: number;
  contacted: number;
  disputes: number;
  statutoryInterestPaise: number;
  actions: Record<string, number>;
}

export interface CartArmSummary {
  carts: number;
  chosePlain: number;
  choseIncentive: number;
  marginProtectedPaise: number;
  note: string;
}

export interface ScheduleSummary {
  mandates: number;
  meanPSufficientLift: number;
  expectedRecoveryPaise: number;
  fixedClockRecoveryPaise: number;
  deltaPaise: number;
  note: string;
}

export interface VoiceTurn {
  speaker: 'agent' | 'customer';
  text: string;
  intent: string | null;
  audioB64: string | null;
  audioMocked: boolean;
  latencyMs: number;
}

export interface VoiceCallResult {
  eventId: string;
  outcome: string;
  state: string;
  promise: { amountPaise: number; dueAt: string; verbatim: string; confidence: number } | null;
  audioLive: boolean;
  note: string;
  durationSeconds: number;
  turns: VoiceTurn[];
  recordedPromise?: PromiseRecord;
}

export interface VoiceStatus {
  provider: string;
  ttsModel: string;
  sttModel: string;
  live: boolean;
  note: string;
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
  kind?: LeakKind;
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
  /** 'learned' — trained on the simulator; 'learned-real' — on real outcomes; 'priors' — reason-family priors. */
  estimatorMode?: 'learned' | 'learned-real' | 'priors';
  kinds?: KindRow[];
  degradation?: DegradationView | null;
  ladder?: LadderRow[];
  cartArms?: CartArmSummary | null;
  schedules?: ScheduleSummary | null;
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
  customerName?: string | null;
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
    /** The side the gate actually ran on — always the deterministic mapping. */
    failureSide: string;
    /** What the model read, kept separate: its classification never gates. */
    modelFailureSide?: string | null;
    modelAdvisory?: boolean;
    disagreesWithGate?: boolean;
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
    execution: {
      mode: string;
      detail: string;
      mocked: boolean;
      externalKind?: 'order' | 'payment_link' | null;
      externalId?: string | null;
    };
    outcome: Outcome;
    costPaise?: number;
    /** Which randomised arm the counterparty is in. */
    arm?: 'control' | 'treatment';
    /** Whether the policy wanted to contact before exploration. */
    wanted?: boolean;
    /** Whether exploration flipped the decision. */
    explored?: boolean;
    /** P(contact | features) under the policy — 1−ε, ε, or 0 where contact was impossible. */
    propensity?: number | null;
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
  /** Present once a real leak's outcome has been attributed (overlaid at read time). */
  outcomeAttribution?: {
    state: 'resolved' | 'unresolved';
    source: string | null;
    at: string | null;
  };
}

/** GET /api/learning/status */
export interface PolicyEffect {
  treatmentRows: number;
  controlRows: number;
  rateTreatment: number | null;
  rateControl: number | null;
  ateRate: number | null;
  ateRateCi: [number, number] | null;
  incrementalPaise: number | null;
  incrementalPaiseCi: [number, number] | null;
  measurable: boolean;
  note: string | null;
}

export interface LearningRun {
  at?: string;
  rowsUsed: number;
  treatedRows: number;
  controlRows: number;
  resolvedRows?: number;
  estimator: string;
  featureVersion: number;
  ready: boolean;
  qiniReal: number | null;
  note: string | null;
}

export interface LearningStatus {
  counts: {
    real: number;
    pending: number;
    resolved: number;
    control: number;
    explored: number;
    synthetic: number;
  };
  estimatorMode: 'priors' | 'learned-real';
  estimator: string;
  lastRun: LearningRun | null;
  policyEffect: PolicyEffect;
  thresholds: { minRows: number; minPerArm: number };
  featureVersion: number;
}

export interface SyncReport {
  checked: number;
  recovered: number;
  churned: number;
  stale: number;
  stillPending: number;
  errors: string[];
  live: boolean;
  retrain?: LearningRun;
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
  kinds?: KindRow[];
  degradationHeld?: number;
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
