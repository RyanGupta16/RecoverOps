import { Reveal } from '@/components/motion/Reveal';
import { GlassCard, Section, SectionHead } from '@/components/ui/primitives';
import { POLICY_RULES } from '@/lib/policy';
import { PolicyTable } from './PolicyTable';

const RULE_COUNT_WORDS: Record<number, string> = {
  12: 'Twelve',
  20: 'Twenty',
  21: 'Twenty-one',
  22: 'Twenty-two',
};

export function PolicySection() {
  const count = RULE_COUNT_WORDS[POLICY_RULES.length] ?? String(POLICY_RULES.length);
  return (
    <Section id="policy" tone="grounded">
      <SectionHead
        eyebrow="Policy & compliance"
        title={
          <>
            {count} rules the engine
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
            Three classes of message, and the line most systems never draw
          </h3>
          <p className="mt-3.5 text-[14px] leading-[1.66] text-ink-dim">
            Under TRAI&rsquo;s TCCCPR amendment of February 2025, a message is{' '}
            <em>transactional</em> only if it answers a customer-initiated transaction within
            thirty minutes. A recurring charge is merchant-initiated, so a failed-subscription notice
            is never transactional — it is a <em>service</em> message about a product the customer
            holds: no explicit consent, no time band. Put an incentive in it and the whole message
            becomes <em>promotional</em>: consent record, DND scrub, 09:00–21:00 IST, and consent
            given to complete a purchase expires after seven days.
          </p>
          <p className="mt-3.5 text-[14px] leading-[1.66] text-ink-dim">
            So the content and the clock are not details of delivery. They change what the message
            legally is. A dunning system that adds a &ldquo;10% off if you pay today&rdquo; to a
            reminder has quietly moved into a different regulatory category. RecoverOps classifies
            every message before it gates it, and records the class — and the clause — on every
            decision.
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
