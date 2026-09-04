import { BatchRunner } from '@/components/console/BatchRunner';
import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { TerminalPanel } from '@/components/console/primitives';
import { DemoModeBadge } from '@/components/ui/primitives';
import { loadBatch } from '@/lib/batch.server';
import { getSampleBatch } from '@/lib/sample.server';

// The side panels describe the latest stored batch, so this page must render
// per request rather than be frozen at build time with the bundled sample.
export const dynamic = 'force-dynamic';

export default async function ConsolePage() {
  // The runner replays the bundled script only when the backend never opens a
  // stream; the panels beside it describe whichever batch actually answered.
  const sample = getSampleBatch();
  const { batch, source } = await loadBatch();

  return (
    <>
      <ConsoleHeading
        title="Batch console"
        sub="Runs against the backend at NEXT_PUBLIC_API_URL when it is reachable, and replays the bundled synthetic batch when it is not. Which one produced what you are looking at is stated on screen, never assumed."
        aside={<DemoModeBadge source={source} />}
      />

      <div className="grid gap-4 xl:grid-cols-[1fr_330px]">
        <BatchRunner script={sample.streamScript} />

        <div className="flex flex-col gap-4">
          <TerminalPanel title="Assumptions" meta={source === 'live' ? batch.batchId : 'simulation parameters'}>
            <dl className="flex flex-col gap-3">
              {batch.assumptions.map((assumption) => (
                <div
                  key={assumption.key}
                  className="border-b border-hairline pb-3 last:border-b-0 last:pb-0"
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="font-mono text-[10.5px] text-brass">{assumption.key}</dt>
                    <dd className="font-mono text-[12px] tabular-nums text-ink">
                      {assumption.value}
                    </dd>
                  </div>
                  <p className="mt-1 text-[11.5px] leading-snug text-ink-mute">{assumption.note}</p>
                </div>
              ))}
            </dl>
          </TerminalPanel>

          <TerminalPanel title="What this batch is" meta="read before quoting a number">
            <div className="flex flex-col gap-3 text-[11.5px] leading-[1.6] text-ink-dim">
              <p>{batch.honesty.whatIsSynthetic}</p>
              <p className="border-t border-hairline pt-3">{batch.honesty.whatIsReal}</p>
            </div>
          </TerminalPanel>
        </div>
      </div>
    </>
  );
}
