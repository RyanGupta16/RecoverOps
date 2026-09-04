import { Section, SectionHead } from '@/components/ui/primitives';
import { FaqAccordion, type FaqItem } from './FaqAccordion';

/**
 * Judge-oriented, not customer-oriented. Questions we cannot answer from
 * something true of this build are not asked — a vague non-answer is worse
 * than an absent question.
 */
const ITEMS: FaqItem[] = [
  {
    q: 'Is this actually connected to the Razorpay API?',
    a: (
      <>
        Yes, in test mode. Orders, payment links and subscription retries are real SDK calls against
        Razorpay&rsquo;s test environment, and the executor records the endpoint it hit on every
        decision trace. Outbound SMS and WhatsApp delivery is mocked — we do not send messages to
        anyone — and every screen that shows an outbound action labels it as mocked.
      </>
    ),
  },
  {
    q: 'Is the uplift model real machine learning, or rules in a costume?',
    a: (
      <>
        The design is a two-model T-learner: one estimator per branch, uplift as the difference,
        with a documented fallback to hand-specified segment priors when a learned model is not
        wired up. Rather than assert which of those you are looking at, every response carries an{' '}
        <code className="rounded bg-ink/[0.08] px-1.5 py-0.5 font-mono text-[12px] text-amber">
          estimator
        </code>{' '}
        field and the console prints it on each trace. When you run the console with the backend
        offline, it reads <em className="italic">segment-prior simulation (sample data)</em> —
        because that is what produced the numbers on screen.
      </>
    ),
  },
  {
    q: 'What is synthetic here, and what is real?',
    a: (
      <>
        Synthetic: the 500-event batch, its outcomes, and its causal ground truth. That is not a
        shortcut — the four-segment framework needs both the contacted and the uncontacted outcome
        for the same customer, which no live system can observe, so demonstrating the difference
        requires a batch where both are known. Real: the Razorpay error-code corpus the diagnosis
        layer retrieves against, the regulatory citations in the policy gate, the test-mode API
        calls, and the pipeline itself.
      </>
    ),
  },
  {
    q: 'How is this different from Razorpay’s own retry logic, or Stripe Smart Retries?',
    a: (
      <>
        Three different clocks. Razorpay&rsquo;s documented subscription behaviour is a fixed
        schedule — next-day, then a T+3 cycle of up to three attempts. That is a statement of fact
        about the product, not a criticism. Stripe Smart Retries times attempts by predicted success
        probability, which is a genuine improvement on a fixed clock. RecoverOps times and targets
        by causal uplift: not <em className="italic">how likely is this to work</em> but{' '}
        <em className="italic">how much does doing it change the outcome</em>. The first two cannot
        distinguish a customer who needed the message from one who would have paid anyway.
      </>
    ),
  },
  {
    q: 'What happens when the model decides to do nothing?',
    a: (
      <>
        It is written down. A no-action decision produces the same ledger row as an executed one:
        the estimate that drove it, the rule that confirmed it, and the amount at stake. They are
        listed in the Sleeping Dog Ledger, and cases the ladder could not resolve go to the
        Exception Queue with a structured reason rather than disappearing. Doing nothing quietly and
        doing nothing accountably are different products.
      </>
    ),
  },
  {
    q: 'Does the baseline exist to lose?',
    a: (
      <>
        No, and it would be a worthless comparison if it did. The shadow policy runs on the same
        events, with the same contact budget, through the same twelve-rule gate. The only difference
        is what it ranks by. It also beats us in one place — it contacts fewer lost causes, because
        our uplift estimate is noisy near zero and theirs never scores a dead card highly. That is
        on the results section, not buried here.
      </>
    ),
  },
];

export function Faq() {
  return (
    <Section id="faq" tone="grounded">
      <SectionHead eyebrow="Questions" title="What a judge should ask." align="center" />

      <FaqAccordion items={ITEMS} />
    </Section>
  );
}
