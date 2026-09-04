import 'server-only';

import sampleBatch from '@data/sample-batch.json';
import type { BatchResult, DecisionTrace } from './types';

/**
 * Server-only access to the bundled synthetic batch.
 *
 * The batch file is 800 KB and the trace file larger still. Importing either
 * from a client component would ship the whole thing to the browser, so the
 * data is read here and exposed to the client through route handlers under
 * /api/sample/* instead. `server-only` makes that a build error rather than a
 * silently enormous bundle.
 */

export function getSampleBatch(): BatchResult {
  return sampleBatch as unknown as BatchResult;
}

let traceCache: Map<string, DecisionTrace> | null = null;

export async function getSampleTrace(eventId: string): Promise<DecisionTrace | null> {
  if (!traceCache) {
    const mod = (await import('@data/sample-traces.json')) as unknown as {
      default?: { traces: DecisionTrace[] };
      traces?: DecisionTrace[];
    };
    const traces = mod.traces ?? mod.default?.traces ?? [];
    traceCache = new Map(traces.map((t) => [t.eventId, t]));
  }
  return traceCache.get(eventId) ?? null;
}

/** Slim rows for the hero feed — six events with a spread of outcomes. */
export function getHeroFeedRows() {
  const batch = getSampleBatch();
  const wanted = [
    'sleeping_dog',
    'persuadable',
    'sure_thing',
    'persuadable',
    'lost_cause',
    'persuadable',
  ];
  const used = new Set<string>();

  return wanted
    .map((segment) => {
      const match = batch.events.find(
        (e) => e.truthSegment === segment && !used.has(e.eventId) && e.agentB.action !== 'escalate',
      );
      if (match) used.add(match.eventId);
      return match;
    })
    .filter((e): e is NonNullable<typeof e> => Boolean(e))
    .map((e) => ({
      eventId: e.eventId,
      paymentId: e.paymentId,
      amountPaise: e.amountPaise,
      reasonLabel: e.reasonLabel,
      action: e.agentB.label,
      contacted: e.agentB.contacted,
      blockedBy: e.agentB.blockedBy ?? null,
      upliftHat: e.upliftHat,
      issuer: e.issuer,
    }));
}
