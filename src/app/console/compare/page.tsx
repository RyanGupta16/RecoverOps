import { MetricTable, SegmentTable, type MetricRow } from '@/components/console/CompareTables';
import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { TerminalPanel } from '@/components/console/primitives';
import { ComparisonChartLazy } from '@/components/marketing/ComparisonChartLazy';
import { DemoModeBadge } from '@/components/ui/primitives';
import { percent, rupees } from '@/lib/format';
import { getSampleBatch } from '@/lib/sample.server';
import type { AgentMetrics } from '@/lib/types';

function buildRows(a: AgentMetrics, b: AgentMetrics): MetricRow[] {
  const cmp = (av: number, bv: number, lowerIsBetter: boolean): 'a' | 'b' | null => {
    if (av === bv) return null;
    const bWins = lowerIsBetter ? bv < av : bv > av;
    return bWins ? 'b' : 'a';
  };

  return [
    {
      key: 'contacts',
      metric: 'Contacts spent',
      a: String(a.contactsMade),
      b: String(b.contactsMade),
      winner: cmp(a.contactsMade, b.contactsMade, true),
      note: `Both policies were given the same ${a.contactBudget}-contact budget.`,
    },
    {
      key: 'wasted',
      metric: 'Contacts that changed nothing',
      a: String(a.wastedContacts),
      b: String(b.wastedContacts),
      winner: cmp(a.wastedContacts, b.wastedContacts, true),
      note: 'Outcome identical to staying quiet. Visible only because both branches are known.',
    },
    {
      key: 'driven',
      metric: 'Recoveries caused by outreach',
      a: String(a.outreachDrivenRecoveries),
      b: String(b.outreachDrivenRecoveries),
      winner: cmp(a.outreachDrivenRecoveries, b.outreachDrivenRecoveries, false),
      note: 'Payments that would not have cleared had we stayed quiet.',
    },
    {
      key: 'dogs',
      metric: 'Sleeping dogs contacted',
      a: String(a.sleepingDogsTouched),
      b: String(b.sleepingDogsTouched),
      winner: cmp(a.sleepingDogsTouched, b.sleepingDogsTouched, true),
      note: 'No compliance rule catches these. Only a negative uplift estimate does.',
    },
    {
      key: 'caused',
      metric: 'Cancellations caused by outreach',
      a: String(a.outreachCausedCancellations),
      b: String(b.outreachCausedCancellations),
      winner: cmp(a.outreachCausedCancellations, b.outreachCausedCancellations, true),
      note: 'Realised, not expected. At this sample size the count is noisy — read the row above it instead.',
    },
    {
      key: 'recovered',
      metric: 'Total recovered',
      a: rupees(a.recoveredPaise),
      b: rupees(b.recoveredPaise),
      winner: cmp(a.recoveredPaise, b.recoveredPaise, false),
      note: 'Dominated by payments that silent retries collect under either policy.',
    },
    {
      key: 'rate',
      metric: 'Recovery rate',
      a: percent(a.recoveryRate),
      b: percent(b.recoveryRate),
      winner: cmp(a.recoveryRate, b.recoveryRate, false),
      note: 'Share of all events in the batch that ended in a cleared payment.',
    },
    {
      key: 'escalated',
      metric: 'Escalated to a human',
      a: String(a.escalations),
      b: String(b.escalations),
      winner: null,
      note: 'Cases the action ladder could not resolve.',
    },
    {
      key: 'net',
      metric: 'Net value (incremental)',
      a: rupees(a.netValuePaise),
      b: rupees(b.netValuePaise),
      winner: cmp(a.netValuePaise, b.netValuePaise, false),
      note: 'Recovered, less contact cost, less the churn the outreach itself caused. Churn that would have happened anyway is not charged to either policy.',
    },
  ];
}

export default function ComparePage() {
  const batch = getSampleBatch();
  const a = batch.agents.A;
  const b = batch.agents.B;

  const segmentRows = b.segments.map((row) => ({
    ...row,
    baselineContacted: a.segments.find((s) => s.segment === row.segment)?.contacted ?? 0,
  }));

  return (
    <>
      <ConsoleHeading
        title="Comparison panel"
        sub="Two policies, one batch, one policy gate, one contact budget. The only thing that differs is what each ranks by."
        aside={<DemoModeBadge source={batch.source} />}
      />

      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <TerminalPanel
          title="Cumulative net value"
          meta={`${batch.batchId} · ${batch.eventCount} events`}
        >
          <ComparisonChartLazy baseline={a.curve} uplift={b.curve} height={340} compact />
          <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
            {batch.honesty.curveNote}
          </p>
        </TerminalPanel>

        <div className="flex flex-col gap-4">
          <TerminalPanel title="Objectives" meta="what each policy maximises">
            <div className="flex flex-col gap-3.5">
              {[a, b].map((agent) => (
                <div
                  key={agent.key}
                  className="border-b border-hairline pb-3.5 last:border-b-0 last:pb-0"
                >
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span
                      className={`font-mono text-[10px] uppercase tracking-[0.14em] ${
                        agent.key === 'B' ? 'text-amber' : 'text-ink-mute'
                      }`}
                    >
                      Agent {agent.key}
                    </span>
                    <span className="text-[13px] text-ink">{agent.name}</span>
                    <span className="font-mono text-[10.5px] text-brass">· {agent.objective}</span>
                  </div>
                  <p className="mt-1.5 text-[11.5px] leading-[1.6] text-ink-dim">
                    {agent.description}
                  </p>
                </div>
              ))}
            </div>
          </TerminalPanel>

          <TerminalPanel title="Where the uplift agent loses" meta="not a footnote">
            <p className="text-[11.5px] leading-[1.65] text-ink-dim">
              {batch.honesty.knownWeakness}
            </p>
          </TerminalPanel>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <TerminalPanel title="Metric by metric" meta="green marks the baseline winning">
          <MetricTable rows={buildRows(a.metrics, b.metrics)} />
          <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
            {batch.honesty.noiseNote}
          </p>
        </TerminalPanel>

        <TerminalPanel
          title="Where each policy spent its budget"
          meta="graded against ground truth"
        >
          <SegmentTable rows={segmentRows} />
          <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
            This table is the comparison. Both policies recover roughly the same money, because
            silent retries do most of the work under either. The difference is that one spent its
            budget on customers whose outcome it changed, and the other spent part of it on
            customers who were going to pay anyway — and on some who cancelled because they were
            contacted.
          </p>
        </TerminalPanel>
      </div>
    </>
  );
}
