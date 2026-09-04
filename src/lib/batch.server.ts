import 'server-only';

import { fetchAuditTail, fetchAuditVerify, fetchBatch, fetchBatches, fetchLatestBatch } from './api';
import { getSampleBatch } from './sample.server';
import type {
  AuditEntry,
  AuditVerification,
  BatchResult,
  BatchSummary,
  DataSource,
} from './types';

/**
 * Server-side loaders for the console pages.
 *
 * Every console page used to render the bundled sample and only the bundled
 * sample — a judge could run a live batch, open Compare, and be shown demo
 * data under a Demo Mode badge. These loaders try the backend first (by batch
 * id when the page was given one, latest otherwise) and fall back to the
 * bundled batch, returning the source so the badge is driven by what actually
 * answered.
 */

export const SAMPLE_BATCH_ID = 'bat_sample_20260903';

export async function loadBatch(
  batchId?: string | null,
): Promise<{ batch: BatchResult; source: DataSource }> {
  if (batchId === SAMPLE_BATCH_ID) return { batch: getSampleBatch(), source: 'sample' };
  if (batchId) {
    const live = await fetchBatch(batchId);
    if (live) return { batch: { ...live, source: 'live' }, source: 'live' };
    // An unknown id falls through to the latest rather than a 404: the link
    // may be from a ledger that has since been reset, and the page should
    // still show something with a badge that says what it is.
  }
  try {
    const { data, source } = await fetchLatestBatch();
    if (source === 'live') return { batch: data, source };
  } catch {
    /* falls through */
  }
  return { batch: getSampleBatch(), source: 'sample' };
}

/** Mirrors `Store.summarize` so demo mode can show the bundled batch as one history row. */
export function summarizeBatch(batch: BatchResult, createdAt: string): BatchSummary {
  const pick = (key: 'A' | 'B') => {
    const m = batch.agents[key].metrics;
    return {
      contactsMade: m.contactsMade,
      recoveredPaise: m.recoveredPaise,
      netValuePaise: m.netValuePaise,
      sleepingDogsTouched: m.sleepingDogsTouched,
      wastedContacts: m.wastedContacts,
      escalations: m.escalations,
      recoveryRate: m.recoveryRate,
    };
  };
  return {
    batchId: batch.batchId,
    label: batch.label,
    source: batch.source,
    seed: batch.seed ?? null,
    eventCount: batch.eventCount,
    generatedBy: batch.generatedBy ?? null,
    createdAt,
    agents: { A: pick('A'), B: pick('B') },
    sleepingDogs: batch.sleepingDogs.length,
    exceptions: batch.exceptions.length,
    pipelineStats: batch.pipelineStats,
  };
}

export async function loadHistory(): Promise<{ rows: BatchSummary[]; source: DataSource }> {
  const live = await fetchBatches(50);
  if (live.length > 0) return { rows: live, source: 'live' };
  // Demo mode: one row, the bundled batch, dated to its generation.
  const sample = getSampleBatch();
  return { rows: [summarizeBatch(sample, '2026-09-03T00:00:00.000Z')], source: 'sample' };
}

export async function loadAudit(): Promise<{
  verify: AuditVerification;
  tail: AuditEntry[];
  total: number;
} | null> {
  const [verify, tail] = await Promise.all([fetchAuditVerify(), fetchAuditTail(40)]);
  if (!verify || !tail) return null;
  return { verify, tail: tail.rows, total: tail.total };
}
