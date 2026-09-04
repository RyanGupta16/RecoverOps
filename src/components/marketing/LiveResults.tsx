import Link from 'next/link';
import { Reveal } from '@/components/motion/Reveal';
import { AnimatedStatBar, type BarRow } from '@/components/ui/AnimatedStatBar';
import { DemoModeBadge, GlassCard, Section, SectionHead } from '@/components/ui/primitives';
import { rupees } from '@/lib/format';
import type { BatchResult, DataSource } from '@/lib/types';
import { ComparisonChartLazy } from './ComparisonChartLazy';

/**
 * Condensed comparison, rendered from whatever batch the page was given —
 * a live backend run when one is reachable, the bundled synthetic batch
 * otherwise, and the badge says which.
 */
export function LiveResults({ batch, source }: { batch: BatchResult; source: DataSource }) {
  const a = batch.agents.A.metrics;
  const b = batch.agents.B.metrics;

  const maxContacts = Math.max(a.contactsMade, b.contactsMade, 1);
  const maxWasted = Math.max(a.wastedContacts, b.wastedContacts, 1);
  const maxDogs = Math.max(a.sleepingDogsTouched, b.sleepingDogsTouched, 1);

  const rows: BarRow[] = [
    {
      label: 'Contacts spent',
      baselineText: `${a.contactsMade}`,
      upliftText: `${b.contactsMade}`,
      baselineFill: a.contactsMade / maxContacts,
      fill: b.contactsMade / maxContacts,
      note: `Same ${a.contactBudget}-contact budget, same policy gate, same events. Only the ranking objective differs.`,
    },
    {
      label: 'Contacts that changed nothing',
      baselineText: `${a.wastedContacts}`,
      upliftText: `${b.wastedContacts}`,
      baselineFill: a.wastedContacts / maxWasted,
      fill: b.wastedContacts / maxWasted,
      note: 'Messages sent to customers whose outcome was identical either way. Visible here only because both branches are known.',
    },
    {
      label: 'Sleeping dogs contacted',
      baselineText: `${a.sleepingDogsTouched}`,
      upliftText: `${b.sleepingDogsTouched}`,
      baselineFill: a.sleepingDogsTouched / maxDogs,
      fill: b.sleepingDogsTouched / maxDogs,
      note: 'Customers who were going to pay on the retry, and who cancel when reminded. No compliance rule catches these — only a negative uplift estimate does.',
    },
  ];

  return (
    <Section id="results">
      <SectionHead
        eyebrow="Live results"
        title={
          <>
            Same batch. Same gate.
            <br />
            Different question.
          </>
        }
        lede="Every run scores both policies over the same events and writes both to the ledger, so this comparison is produced by the batch rather than assembled afterwards."
      />

      <Reveal className="mb-6 flex flex-wrap items-center gap-3" variant="fade">
        <DemoModeBadge source={source} />
        <span className="font-mono text-[11px] text-ink-mute">
          batch {batch.batchId} · {batch.eventCount} events
        </span>
      </Reveal>

      <div className="grid gap-6 lg:grid-cols-[1.35fr_1fr] lg:gap-8">
        <Reveal variant="rise">
          <GlassCard className="p-5 md:p-7" tone="high">
            <div className="mb-5 flex flex-wrap items-baseline justify-between gap-3">
              <h3 className="display text-[21px] text-ink">
                Cumulative net value by contact spent
              </h3>
              <div className="flex items-center gap-4 font-mono text-[10.5px] uppercase tracking-[0.1em]">
                <span className="flex items-center gap-2 text-ink-mute">
                  <span className="h-px w-5 bg-ink-mute" /> Baseline
                </span>
                <span className="flex items-center gap-2 text-amber">
                  <span className="h-px w-5 bg-amber" /> RecoverOps
                </span>
              </div>
            </div>

            <ComparisonChartLazy baseline={batch.agents.A.curve} uplift={batch.agents.B.curve} />

            <p className="mt-4 border-t border-hairline pt-4 text-[12.5px] leading-relaxed text-ink-mute">
              {batch.honesty.curveNote}
            </p>
          </GlassCard>
        </Reveal>

        <Reveal variant="rise">
          <GlassCard className="h-full p-5 md:p-7">
            <AnimatedStatBar rows={rows} />

            <dl className="mt-7 grid grid-cols-2 gap-4 border-t border-hairline pt-5">
              <div>
                <dt className="text-[11px] uppercase tracking-[0.12em] text-ink-mute">
                  Recovered, baseline
                </dt>
                <dd className="display mt-1 text-[19px] text-ink-dim">
                  {rupees(a.recoveredPaise)}
                </dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-[0.12em] text-ink-mute">
                  Recovered, RecoverOps
                </dt>
                <dd className="display mt-1 text-[19px] text-amber">{rupees(b.recoveredPaise)}</dd>
              </div>
            </dl>
          </GlassCard>
        </Reveal>
      </div>

      {/* The uncomfortable paragraphs go on the page, not in a footnote. */}
      <Reveal className="mt-6 grid gap-5 md:grid-cols-2" variant="fade-up">
        <div className="rounded-[var(--radius-card)] border border-hairline bg-glass p-6 backdrop-blur-xl">
          <h3 className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-mute">
            What this does not show
          </h3>
          <p className="mt-3 text-[13.5px] leading-[1.62] text-ink-dim">
            {batch.honesty.noiseNote}
          </p>
        </div>
        <div className="rounded-[var(--radius-card)] border border-hairline bg-glass p-6 backdrop-blur-xl">
          <h3 className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-mute">
            Where the uplift agent loses
          </h3>
          <p className="mt-3 text-[13.5px] leading-[1.62] text-ink-dim">
            {batch.honesty.knownWeakness}
          </p>
        </div>
      </Reveal>

      <Reveal className="mt-8" variant="fade">
        <Link
          href="/console/compare"
          className="inline-flex items-center gap-2 text-[14px] text-amber underline-offset-4 hover:underline"
        >
          Open the full comparison panel
          <span aria-hidden="true">→</span>
        </Link>
      </Reveal>
    </Section>
  );
}
