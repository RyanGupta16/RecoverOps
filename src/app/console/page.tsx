import { BatchRunner } from '@/components/console/BatchRunner';
import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { TerminalPanel } from '@/components/console/primitives';
import { getSampleBatch } from '@/lib/sample.server';

export default function ConsolePage() {
  const batch = getSampleBatch();

  return (
    <>
      <ConsoleHeading
        title="Batch console"
        sub="Runs against the backend at NEXT_PUBLIC_API_URL when it is reachable, and replays the bundled synthetic batch when it is not. Which one produced what you are looking at is stated on screen, never assumed."
      />

      <div className="grid gap-4 xl:grid-cols-[1fr_330px]">
        <BatchRunner script={batch.streamScript} />

        <div className="flex flex-col gap-4">
          <TerminalPanel title="Assumptions" meta="simulation parameters">
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
