import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { SleepingDogTable } from '@/components/console/LedgerTables';
import { TerminalPanel } from '@/components/console/primitives';
import { DemoModeBadge } from '@/components/ui/primitives';
import { rupees } from '@/lib/format';
import { getSampleBatch } from '@/lib/sample.server';

export default function SleepingDogsPage() {
  const batch = getSampleBatch();
  const records = batch.sleepingDogs;

  const trueDogs = records.filter((r) => r.truthSegment === 'sleeping_dog');
  const wouldHaveBeenContacted = records.filter((r) => r.baselineWouldContact);
  const damageAvoided = records.reduce((sum, r) => sum + r.estimatedDamageAvoidedPaise, 0);

  return (
    <>
      <ConsoleHeading
        title="Sleeping dog ledger"
        sub="Every case the agent decided not to touch, and the reason. Doing nothing is a decision with a record, not an absence of one."
        aside={<DemoModeBadge source={batch.source} />}
      />

      <div className="mb-4 grid gap-4 lg:grid-cols-3">
        <TerminalPanel title="No-action decisions" meta="this batch">
          <p className="font-mono text-[28px] tabular-nums text-ink">{records.length}</p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            Cases where estimated uplift sat at or below the acting threshold.
          </p>
        </TerminalPanel>

        <TerminalPanel title="Of which truly sleeping dogs" meta="graded against ground truth">
          <p className="font-mono text-[28px] tabular-nums text-amber">{trueDogs.length}</p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            The rest are lost causes and sure things — also correct to leave alone, for different
            reasons. A no-action decision is not a claim that every case is a sleeping dog.
          </p>
        </TerminalPanel>

        <TerminalPanel title="Baseline would have contacted" meta="same events, same gate">
          <p className="font-mono text-[28px] tabular-nums text-[var(--color-verdict-block)]">
            {wouldHaveBeenContacted.length}
          </p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            Estimated damage avoided: {rupees(damageAvoided)} — priced at{' '}
            {batch.assumptions.find((x) => x.key === 'churnResidualCycles')?.value ?? 3} billing
            cycles of residual value, which is an assumption, not a measurement.
          </p>
        </TerminalPanel>
      </div>

      <TerminalPanel
        title="Ledger"
        meta={`${records.length} rows · sorted by estimated damage avoided`}
      >
        <SleepingDogTable rows={records} />
      </TerminalPanel>
    </>
  );
}
