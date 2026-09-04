import type {
  BatchResult,
  DecisionTrace,
  ExceptionRecord,
  SleepingDogRecord,
  Sourced,
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

export async function startBatchRun(): Promise<{ batchId: string; source: 'live' | 'sample' }> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    const res = await fetch(`${API_URL}/api/batch/run`, {
      method: 'POST',
      signal: controller.signal,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({}),
    });
    clearTimeout(timer);
    if (res.ok) {
      const json = (await res.json()) as { batchId?: string; batch_id?: string };
      const batchId = json.batchId ?? json.batch_id;
      if (batchId) return { batchId, source: 'live' };
    }
  } catch {
    /* falls through to the sample replay */
  }
  return { batchId: 'bat_sample_20260903', source: 'sample' };
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
