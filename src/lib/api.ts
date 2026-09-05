import type {
  AuditEntry,
  AuditVerification,
  BatchResult,
  BatchSummary,
  DecisionTrace,
  ExceptionRecord,
  FileIngestMeta,
  LeakSourceInfo,
  LearningRun,
  LearningStatus,
  RunBatchOptions,
  SleepingDogRecord,
  Sourced,
  SyncReport,
} from './types';

/**
 * Typed client for the RecoverOps backend, with one hard requirement: the site
 * must stay usable when the backend is unreachable. Every call falls back to
 * the bundled synthetic batch — served from /api/sample/* so it is never
 * bundled into client JavaScript — and reports `source: 'sample'`, which is
 * what drives the Demo Mode badge. Nothing silently substitutes sample data for
 * a real number without saying so on screen.
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

/**
 * Whether a backend has actually been configured. Without this the site fires a
 * request at a default localhost URL that is usually not listening, and the
 * browser logs a red failed-request line for something that is expected and
 * already handled. Demo mode should look deliberate, not broken.
 */
export const HAS_BACKEND = Boolean(process.env.NEXT_PUBLIC_API_URL);

const TIMEOUT_MS = 4000;

async function getJson<T>(url: string, timeout = TIMEOUT_MS): Promise<T | null> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const res = await fetch(url, {
      signal: controller.signal,
      cache: 'no-store',
      headers: { accept: 'application/json' },
    });
    clearTimeout(timer);
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    // Offline, slow, or blocked by CORS — all resolve the same way.
    return null;
  }
}

const backend = <T>(path: string): Promise<T | null> =>
  HAS_BACKEND ? getJson<T>(`${API_URL}${path}`) : Promise.resolve(null);
const sample = <T>(path: string) => getJson<T>(path, 10_000);

export async function fetchLatestBatch(): Promise<Sourced<BatchResult>> {
  const live = await backend<BatchResult>('/api/batch/latest');
  if (live) return { data: { ...live, source: 'live' }, source: 'live' };

  const fallback = await sample<BatchResult>('/api/sample/batch');
  if (!fallback) throw new Error('Neither the backend nor the bundled sample batch could be read.');
  return { data: fallback, source: 'sample' };
}

/** One stored batch by id. Null when the backend is unreachable or has no such batch. */
export function fetchBatch(batchId: string): Promise<BatchResult | null> {
  return backend<BatchResult>(`/api/batch/${encodeURIComponent(batchId)}/results`);
}

/** Batch history, newest first. Empty when there is no backend — history lives in its ledger. */
export async function fetchBatches(limit = 25): Promise<BatchSummary[]> {
  return (await backend<BatchSummary[]>(`/api/batches?limit=${limit}`)) ?? [];
}

export function fetchAuditVerify(): Promise<AuditVerification | null> {
  return backend<AuditVerification>('/api/audit/verify');
}

export async function fetchAuditTail(limit = 50): Promise<{ rows: AuditEntry[]; total: number } | null> {
  return backend<{ rows: AuditEntry[]; total: number }>(`/api/audit?limit=${limit}`);
}

/** Real pulls can take a while (paginated API calls); the simulator is ~300 ms. */
const RUN_TIMEOUT_MS = 90_000;

export type RunOutcome =
  | { ok: true; batchId: string; source: 'live'; eventCount?: number; dataMode?: string }
  | { ok: false; source: 'sample'; batchId: string; error?: string };

/**
 * Starts a batch on the backend. Falls back to the sample replay only when the
 * backend is unreachable; a backend that answers with an error (no keys, empty
 * pull, bad file) is reported as an error, not silently replaced with demo data.
 */
export async function startBatchRun(opts: RunBatchOptions = {}): Promise<RunOutcome> {
  if (!HAS_BACKEND) return { ok: false, source: 'sample', batchId: 'bat_sample_20260903' };
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), RUN_TIMEOUT_MS);
    const res = await fetch(`${API_URL}/api/batch/run`, {
      method: 'POST',
      signal: controller.signal,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(opts),
    });
    clearTimeout(timer);
    const json = (await res.json().catch(() => ({}))) as {
      batchId?: string;
      eventCount?: number;
      dataMode?: string;
      detail?: string;
    };
    if (res.ok && json.batchId) {
      return { ok: true, batchId: json.batchId, source: 'live', eventCount: json.eventCount, dataMode: json.dataMode };
    }
    return {
      ok: false,
      source: 'sample',
      batchId: 'bat_sample_20260903',
      error: json.detail ?? `Backend answered ${res.status}.`,
    };
  } catch {
    return { ok: false, source: 'sample', batchId: 'bat_sample_20260903' };
  }
}

export async function fetchSources(): Promise<LeakSourceInfo[]> {
  return (await backend<LeakSourceInfo[]>('/api/sources')) ?? [];
}

export function fetchLearningStatus(): Promise<LearningStatus | null> {
  return backend<LearningStatus>('/api/learning/status');
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json = (await res.json().catch(() => ({}))) as T & { detail?: string };
  if (!res.ok) throw new Error(json.detail ?? `Backend answered ${res.status}.`);
  return json;
}

/** Polls Razorpay for every pending real leak and attributes what it finds. */
export function syncOutcomes(): Promise<SyncReport> {
  return postJson<SyncReport>('/api/outcomes/sync');
}

/** Records an outcome by hand — labelled as manual in the ledger, never as a webhook. */
export function markOutcome(eventId: string, recovered: boolean, churned = false, note = '') {
  return postJson<{ eventId: string; recovered: boolean; churned: boolean; source: string }>(
    '/api/outcomes/mark',
    { eventId, recovered, churned, note },
  );
}

export function retrainLearner(): Promise<LearningRun> {
  return postJson<LearningRun>('/api/learning/retrain');
}

/** Uploads a Razorpay payments export; the backend answers with what it found in it. */
export async function uploadIngestFile(file: File): Promise<FileIngestMeta> {
  const body = new FormData();
  body.append('file', file);
  const res = await fetch(`${API_URL}/api/ingest/file`, { method: 'POST', body });
  const json = (await res.json().catch(() => ({}))) as FileIngestMeta & { detail?: string };
  if (!res.ok) throw new Error(json.detail ?? `Upload failed (${res.status}).`);
  return json;
}

export async function fetchTrace(eventId: string): Promise<Sourced<DecisionTrace> | null> {
  const live = await backend<DecisionTrace>(`/api/events/${encodeURIComponent(eventId)}/trace`);
  if (live) return { data: live, source: 'live' };

  const fallback = await sample<DecisionTrace>(`/api/sample/trace/${encodeURIComponent(eventId)}`);
  return fallback ? { data: fallback, source: 'sample' } : null;
}

export async function fetchSleepingDogs(): Promise<Sourced<SleepingDogRecord[]>> {
  const live = await backend<SleepingDogRecord[]>('/api/sleeping-dogs');
  if (live) return { data: live, source: 'live' };
  const fallback = (await sample<SleepingDogRecord[]>('/api/sample/sleeping-dogs')) ?? [];
  return { data: fallback, source: 'sample' };
}

export async function fetchExceptions(): Promise<Sourced<ExceptionRecord[]>> {
  const live = await backend<ExceptionRecord[]>('/api/exceptions');
  if (live) return { data: live, source: 'live' };
  const fallback = (await sample<ExceptionRecord[]>('/api/sample/exceptions')) ?? [];
  return { data: fallback, source: 'sample' };
}

/** SSE endpoint for a running batch. The console replays the sample script if this never opens. */
export function batchStreamUrl(batchId: string): string {
  return `${API_URL}/api/batch/stream?batch_id=${encodeURIComponent(batchId)}`;
}
