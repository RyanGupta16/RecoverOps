import { Reveal } from '@/components/motion/Reveal';
import { GlassCard, Section, SectionHead } from '@/components/ui/primitives';
import { PolicyTable } from './PolicyTable';

export function PolicySection() {
  return (
    <Section id="policy" tone="grounded">
      <SectionHead
        eyebrow="Policy & compliance"
        title={
          <>
            Twelve rules the engine
            <br />
            cannot argue with.
          </>
        }
        lede="The gate runs after the uplift engine and can veto anything it proposes. Rules are evaluated in order; the first block stops evaluation and everything after it is recorded as not-evaluated, so a trace never implies a rule passed when it was never reached."
      />

      <Reveal variant="fade-up">
        <GlassCard className="p-5 md:p-7">
          <PolicyTable />
        </GlassCard>
      </Reveal>

      <Reveal className="mt-8 grid gap-5 lg:grid-cols-[1.1fr_1fr]" variant="fade-up">
        <div className="rounded-[var(--radius-card)] border border-amber/30 bg-amber/[0.06] p-6 md:p-7">
          <h3 className="display text-[22px] text-ink">
            The thirty-minute line most systems never draw
          </h3>
          <p className="mt-3.5 text-[14px] leading-[1.66] text-ink-dim">
            Under India&rsquo;s TCCCPR framework, a payment-retry message sent within 30 minutes of
            the failed attempt qualifies as transactional. The identical message, to the identical
            customer, sent days later does not — it becomes promotional-class, and the rules change
            underneath it. Promotional messages need a consent record, need DND scrubbing, and are
            confined to 09:00–21:00 IST. Consent validity is capped at seven days under the
            amendment.
          </p>
          <p className="mt-3.5 text-[14px] leading-[1.66] text-ink-dim">
            So the clock is not a detail of delivery. It changes what the message legally is. A
            dunning system that schedules a &ldquo;reminder&rdquo; for T+3 without reclassifying it
            has quietly moved into a different regulatory category. RecoverOps re-gates on elapsed
            time and records the classification on every decision.
          </p>
        </div>

        <div className="flex flex-col gap-5">
          <div className="rounded-[var(--radius-card)] border border-hairline bg-glass p-6">
            <h3 className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-mute">
              What the citations mean
            </h3>
            <p className="mt-3 text-[13.5px] leading-[1.6] text-ink-dim">
              Only rules that enforce a published rule carry a citation. The rest are product policy
              — reasonable defaults we chose — and are labelled as such rather than dressed up as
              regulation to look more rigorous.
            </p>
          </div>
          <div className="rounded-[var(--radius-card)] border border-hairline bg-glass p-6">
            <h3 className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-mute">
              Blocks are evidence, not silence
            </h3>
            <p className="mt-3 text-[13.5px] leading-[1.6] text-ink-dim">
              A blocked action produces the same audit trail as an executed one: rule ID, verdict,
              and the specific values that triggered it. Every block on the sample batch is
              inspectable in the console, down to the individual event.
            </p>
          </div>
        </div>
      </Reveal>
    </Section>
  );
}
