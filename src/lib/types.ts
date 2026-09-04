/** Shapes shared by the FastAPI backend and the bundled sample batch. */

export type DataSource = 'live' | 'sample';

export type Segment = 'sure_thing' | 'persuadable' | 'lost_cause' | 'sleeping_dog';

export type Verdict = 'PASS' | 'BLOCK' | 'N/A';

export type MessageClass = 'transactional' | 'promotional';

export interface GateResult {
  ruleId: string;
  verdict: Verdict;
  note: string;
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
  recovered: boolean;
  churned: boolean;
  /** Rule that stopped the action finally taken. */
  blockedBy?: string | null;
  /** Rule that stopped what the agent wanted to do first. */
  deniedBy?: string | null;
  deniedAction?: string | null;
}

export interface BatchEvent {
  eventId: string;
  paymentId: string;
  subscriptionId: string;
  failedAt: string;
  amountPaise: number;
  planName: string;
  method: string;
  issuer: string;
  reasonCode: string;
  reasonLabel: string;
  failureSide: 'customer' | 'issuer' | 'risk';
  minutesSinceFailure: number;
  messageClass: MessageClass;
  upliftHat: number;
  baselineScore: number;
  agentA: EventDecision;
  agentB: EventDecision;
  truthSegment: Segment;
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
  truthSegment: Segment;
  churnDelta: number;
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
}

export interface DecisionTrace {
  eventId: string;
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
    pControlHat: number;
    pTreatHat: number;
    upliftHat: number;
    perAction: ActionEstimate[];
  };
  agentB: {
    chosenAction: string;
    chosenLabel: string;
    messageClass: MessageClass;
    gate: GateResult[];
    blockedBy: string | null;
    deniedAction: string | null;
    deniedBy: string | null;
    execution: { mode: string; detail: string; mocked: boolean };
    outcome: { recovered: boolean; churned: boolean };
  };
  agentA: {
    chosenAction: string;
    chosenLabel: string;
    score: number;
    outcome: { recovered: boolean; churned: boolean };
  };
  truth: {
    segment: Segment;
    pControl: number;
    pTreat: number;
    churnControl: number;
    churnTreat: number;
  };
}

/** Every response carries where it came from, so the Demo Mode badge is driven by data. */
export interface Sourced<T> {
  data: T;
  source: DataSource;
}
