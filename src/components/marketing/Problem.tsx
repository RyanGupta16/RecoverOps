import { Reveal } from '@/components/motion/Reveal';
import { StatCard } from '@/components/ui/StatCard';
import { GlassCard, Section, SectionHead } from '@/components/ui/primitives';

/**
 * Every figure here is a published industry range, attributed on the card
 * itself. Where a source reports a range, the range is shown — narrowing
 * "80–90%" to a confident-sounding "85%" would be inventing precision.
 */
export function Problem() {
  return (
    <Section id="problem">
      <SectionHead
        eyebrow="The baseline"
        title={
          <>
            A fixed clock is not
            <br />a decision.
          </>
        }
        lede={
          <>
            Razorpay&rsquo;s documented subscription retry behaviour is a schedule: a next-day
            retry, then a T+3 cycle of up to three attempts before the subscription is halted. That
            is a reasonable default and it is not the target of criticism here — it is simply the
            baseline being improved on. A schedule fires at the same time for the customer whose
            salary lands tomorrow and the one who has already decided to leave.
          </>
        }
      />

      <Reveal className="grid gap-5 md:grid-cols-3" stagger={110} variant="rise">
        <StatCard
          value={80}
          suffix="–90%"
          label="of failed card-not-present payments are soft declines"
          context="Not a customer refusing to pay — a stale card, a spending limit, a balance that clears in a day. Recoverable, if the attempt is timed against something real."
          attribution="Widely reported range for subscription CNP failures"
        />
        <StatCard
          value={20}
          suffix="–40%"
          label="of total churn in subscription businesses is involuntary"
          context="Customers who never chose to leave. They are counted as churn, forecast as churn, and written off as churn — because the payment failed and nothing useful happened next."
          attribution="Commonly cited estimate across subscription businesses"
          delay={120}
        />
        <StatCard
          value={25}
          suffix="–35%"
          label="realistic recovery rate, against a ~55% vendor headline"
          context="Independent analysis across a large sample of Stripe Billing accounts lands well below the published average. We set our own targets against the lower figure."
          attribution="Independent analysis vs. Stripe's published ~55% average"
          delay={240}
        />
      </Reveal>

      <Reveal className="mt-6" variant="fade-up">
        <GlassCard className="p-6 md:p-7" tone="high">
          <p className="max-w-[860px] text-[14.5px] leading-[1.68] text-ink-dim">
            <span className="text-ink">The gap is not the retry schedule.</span> Stripe&rsquo;s
            Smart Retries improved on the fixed clock by timing attempts against the probability a
            charge will clear. That is a real advance, and it is still a model of the wrong
            quantity. Probability answers{' '}
            <em className="italic text-ink">will this customer pay?</em> The question that decides
            whether to spend a message is{' '}
            <em className="italic text-amber">does contacting them change anything?</em> Those come
            apart precisely where the money is.
          </p>
        </GlassCard>
      </Reveal>
    </Section>
  );
}
