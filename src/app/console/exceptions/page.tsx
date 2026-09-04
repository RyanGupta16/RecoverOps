import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { ExceptionTable } from '@/components/console/LedgerTables';
import { TerminalPanel } from '@/components/console/primitives';
import { DemoModeBadge } from '@/components/ui/primitives';
import { rupees } from '@/lib/format';
import { getSampleBatch } from '@/lib/sample.server';

export default function ExceptionsPage() {
  const batch = getSampleBatch();
  const records = batch.exceptions;

  const byRule = records.reduce<Record<string, number>>((acc, r) => {
    acc[r.blockedBy] = (acc[r.blockedBy] ?? 0) + 1;
    return acc;
  }, {});
  const ranked = Object.entries(byRule).sort((a, b) => b[1] - a[1]);
  const atStake = records.reduce((sum, r) => sum + r.amountPaise, 0);

  return (
    <>
      <ConsoleHeading
        title="Exception queue"
        sub="Cases where the action ladder ran out before the payment did. Each carries the rule that stopped it and the values that triggered that rule, so a human picking it up does not have to re-derive the decision."
        aside={<DemoModeBadge source={batch.source} />}
      />

      <div className="mb-4 grid gap-4 lg:grid-cols-[1fr_1fr_1.4fr]">
        <TerminalPanel title="Unresolved" meta="this batch">
          <p className="font-mono text-[28px] tabular-nums text-ink">{records.length}</p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            {((records.length / batch.eventCount) * 100).toFixed(1)}% of {batch.eventCount} events.
          </p>
        </TerminalPanel>

        <TerminalPanel title="Value at stake" meta="one billing cycle">
          <p className="font-mono text-[28px] tabular-nums text-amber">{rupees(atStake)}</p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            Sum of the failed amounts sitting in the queue.
          </p>
        </TerminalPanel>

        <TerminalPanel title="Why they stopped" meta="blocking rule, by frequency">
          <ul className="flex flex-col gap-2">
            {ranked.map(([rule, count]) => (
              <li key={rule} className="flex items-center gap-3">
                <span className="w-8 shrink-0 text-right font-mono text-[12px] tabular-nums text-ink">
                  {count}
                </span>
                <span className="font-mono text-[10.5px] text-brass">{rule}</span>
                <span
                  aria-hidden="true"
                  className="h-1 rounded-full bg-rust/60"
                  style={{ width: `${(count / ranked[0][1]) * 42}%` }}
                />
              </li>
            ))}
          </ul>
        </TerminalPanel>
      </div>

      <TerminalPanel title="Queue" meta={`${records.length} rows · sorted by amount`}>
        <ExceptionTable rows={records} />
      </TerminalPanel>
    </>
  );
}
