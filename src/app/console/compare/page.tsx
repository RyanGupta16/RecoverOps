import { MetricTable, SegmentTable, type MetricRow } from '@/components/console/CompareTables';
import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { TerminalPanel } from '@/components/console/primitives';
import { ComparisonChartLazy } from '@/components/marketing/ComparisonChartLazy';
import { DemoModeBadge } from '@/components/ui/primitives';
import { loadBatch } from '@/lib/batch.server';
import { percent, rupees } from '@/lib/format';
import type { AgentMetrics } from '@/lib/types';

export const dynamic = 'force-dynamic';

function buildRows(a: AgentMetrics, b: AgentMetrics, real: boolean): MetricRow[] {
  const cmp = (av: number, bv: number, lowerIsBetter: boolean): 'a' | 'b' | null => {
    if (real || av === bv) return null;
    const bWins = lowerIsBetter ? bv < av : bv > av;
    return bWins ? 'b' : 'a';
  };
  // On real data the outcome-dependent cells are not zero; they are unknown.
  const pending = (v: string) => (real ? 'pending' : v);

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
      key: 'cost',
      metric: 'Channel cost',
      a: rupees(a.contactCostPaise),
      b: rupees(b.contactCostPaise),
      winner: cmp(a.contactCostPaise, b.contactCostPaise, true),
      note: 'Priced per message at the class the gate assigned — a service-class WhatsApp costs a seventh of a marketing one.',
    },
    {
      key: 'wasted',
      metric: 'Contacts that changed nothing',
      a: pending(String(a.wastedContacts)),
      b: pending(String(b.wastedContacts)),
      winner: cmp(a.wastedContacts, b.wastedContacts, true),
      note: 'Outcome identical to staying quiet. Visible only because both branches are known.',
    },
    {
      key: 'driven',
      metric: 'Recoveries caused by outreach',
      a: pending(String(a.outreachDrivenRecoveries)),
      b: pending(String(b.outreachDrivenRecoveries)),
      winner: cmp(a.outreachDrivenRecoveries, b.outreachDrivenRecoveries, false),
      note: 'Payments that would not have cleared had we stayed quiet.',
    },
    {
      key: 'dogs',
      metric: 'Sleeping dogs contacted',
      a: pending(String(a.sleepingDogsTouched)),
      b: pending(String(b.sleepingDogsTouched)),
      winner: cmp(a.sleepingDogsTouched, b.sleepingDogsTouched, true),
      note: 'No compliance rule catches these. Only a negative uplift estimate does.',
    },
    {
      key: 'caused',
      metric: 'Cancellations caused by outreach',
      a: pending(String(a.outreachCausedCancellations)),
      b: pending(String(b.outreachCausedCancellations)),
      winner: cmp(a.outreachCausedCancellations, b.outreachCausedCancellations, true),
      note: 'Realised, not expected. At this sample size the count is noisy — read the row above it instead.',
    },
    {
      key: 'recovered',
      metric: 'Total recovered',
      a: pending(rupees(a.recoveredPaise)),
      b: pending(rupees(b.recoveredPaise)),
      winner: cmp(a.recoveredPaise, b.recoveredPaise, false),
      note: 'Dominated by payments that silent retries collect under either policy.',
    },
    {
      key: 'rate',
      metric: 'Recovery rate',
      a: pending(percent(a.recoveryRate)),
      b: pending(percent(b.recoveryRate)),
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
      a: pending(rupees(a.netValuePaise)),
      b: pending(rupees(b.netValuePaise)),
      winner: cmp(a.netValuePaise, b.netValuePaise, false),
      note: 'Recovered, less channel cost, less the churn the outreach itself caused. Churn that would have happened anyway is not charged to either policy.',
    },
  ];
}

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<{ batch?: string }>;
}) {
  const { batch: requested } = await searchParams;
  const { batch, source } = await loadBatch(requested);
  const a = batch.agents.A;
  const b = batch.agents.B;
  const real = batch.dataMode === 'real';

  const segmentRows = b.segments.map((row) => ({
    ...row,
    baselineContacted: a.segments.find((s) => s.segment === row.segment)?.contacted ?? 0,
  }));

  return (
    <>
      <ConsoleHeading
        title="Comparison panel"
        sub={
          real
            ? 'Two policies, one batch of real leaks, one policy gate, one contact budget. Outcomes are pending, so this compares where each policy would spend — not what it recovered.'
            : 'Two policies, one batch, one policy gate, one contact budget. The only thing that differs is what each ranks by.'
        }
        aside={<DemoModeBadge source={source} />}
      />

      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        {real ? (
          <TerminalPanel title="Cumulative net value" meta={`${batch.batchId} · ${batch.eventCount} real leaks`}>
            <div className="flex h-[340px] flex-col justify-center gap-3 px-2 text-center">
              <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-brass">no curve on real data</p>
              <p className="mx-auto max-w-[520px] text-[13px] leading-[1.65] text-ink-dim">
                {batch.honesty.curveNote}
              </p>
              <p className="mx-auto max-w-[520px] font-mono text-[11px] text-ink-mute">
                {b.metrics.outcomesPending ?? batch.eventCount} outcomes pending ·{' '}
                {b.metrics.holdoutEvents ?? 0} held out as control · source {batch.sourceName}
              </p>
            </div>
          </TerminalPanel>
        ) : (
          <TerminalPanel
            title="Cumulative net value"
            meta={`${batch.batchId} · ${batch.eventCount} events`}
          >
            <ComparisonChartLazy baseline={a.curve} uplift={b.curve} height={340} compact />
            <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
              {batch.honesty.curveNote}
            </p>
          </TerminalPanel>
        )}

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

          <TerminalPanel title={real ? 'What this batch cannot tell you' : 'Where the uplift agent loses'} meta="not a footnote">
            <p className="text-[11.5px] leading-[1.65] text-ink-dim">
              {real ? batch.honesty.noiseNote : batch.honesty.knownWeakness}
            </p>
          </TerminalPanel>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <TerminalPanel title="Metric by metric" meta={real ? 'outcome rows pending' : 'green marks the baseline winning'}>
          <MetricTable rows={buildRows(a.metrics, b.metrics, real)} />
          <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
            {real ? batch.honesty.knownWeakness : batch.honesty.noiseNote}
          </p>
        </TerminalPanel>

        {real ? (
          <TerminalPanel title="Where each policy spent its budget" meta="by reason family">
            <FamilySpend batch={batch} />
            <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
              Without ground truth there is no segment table; the honest view is which failure
              families each policy chose to spend on. Merchant-side and blocked-instrument families
              should show zero contacts under RecoverOps — that is the gate being correct on real
              data from the first event.
            </p>
          </TerminalPanel>
        ) : (
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
        )}
      </div>
    </>
  );
}

/** Real-data replacement for the segment table: contacts by reason family. */
function FamilySpend({ batch }: { batch: Awaited<ReturnType<typeof loadBatch>>['batch'] }) {
  const rows = new Map<string, { n: number; a: number; b: number; amount: number }>();
  for (const e of batch.events) {
    const r = rows.get(e.reasonCode) ?? { n: 0, a: 0, b: 0, amount: 0 };
    r.n += 1;
    r.amount += e.amountPaise;
    if (e.agentA.contacted) r.a += 1;
    if (e.agentB.contacted) r.b += 1;
    rows.set(e.reasonCode, r);
  }
  const sorted = [...rows.entries()].sort((x, y) => y[1].n - x[1].n);
  return (
    <div className="overflow-auto hide-scrollbar">
      <table className="w-full border-collapse text-left font-mono text-[11.5px]">
        <thead>
          <tr className="border-b border-hairline-hi text-[9.5px] uppercase tracking-[0.14em] text-ink-mute">
            <th className="px-2.5 py-2 font-normal">Reason family</th>
            <th className="px-2.5 py-2 text-right font-normal">Leaks</th>
            <th className="px-2.5 py-2 text-right font-normal">At risk</th>
            <th className="px-2.5 py-2 text-right font-normal">Baseline contacted</th>
            <th className="px-2.5 py-2 text-right font-normal">RecoverOps contacted</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(([family, r]) => (
            <tr key={family} className="border-b border-hairline/60">
              <td className="px-2.5 py-2 text-brass">{family}</td>
              <td className="px-2.5 py-2 text-right tabular-nums text-ink-dim">{r.n}</td>
              <td className="px-2.5 py-2 text-right tabular-nums text-ink-dim">{rupees(r.amount)}</td>
              <td className="px-2.5 py-2 text-right tabular-nums text-ink-dim">{r.a}</td>
              <td className="px-2.5 py-2 text-right tabular-nums text-amber">{r.b}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
