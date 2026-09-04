import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { DecisionTraceView } from '@/components/console/DecisionTraceView';
import { API_URL } from '@/lib/api';
import { getSampleTrace } from '@/lib/sample.server';
import type { DataSource, DecisionTrace } from '@/lib/types';

async function loadTrace(
  eventId: string,
): Promise<{ trace: DecisionTrace; source: DataSource } | null> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const res = await fetch(`${API_URL}/api/events/${encodeURIComponent(eventId)}/trace`, {
      signal: controller.signal,
      cache: 'no-store',
    });
    clearTimeout(timer);
    if (res.ok) return { trace: (await res.json()) as DecisionTrace, source: 'live' };
  } catch {
    /* falls through to the bundled batch */
  }

  const sample = await getSampleTrace(eventId);
  return sample ? { trace: sample, source: 'sample' } : null;
}

export default async function TracePage({ params }: { params: Promise<{ eventId: string }> }) {
  const { eventId } = await params;
  const result = await loadTrace(eventId);
  if (!result) notFound();

  return (
    <>
      <ConsoleHeading
        title="Decision trace"
        sub={
          <>
            Every stage that produced this decision, in order, including the rules that did not fire
            and why they were not reached.
          </>
        }
        aside={
          <div className="text-right">
            <p className="font-mono text-[12px] text-amber">{eventId}</p>
            <Link
              href="/console"
              className="font-mono text-[11px] text-ink-mute underline-offset-4 hover:text-ink hover:underline"
            >
              ← back to the batch
            </Link>
          </div>
        }
      />

      <DecisionTraceView trace={result.trace} source={result.source} />
    </>
  );
}
