import { Reveal } from '@/components/motion/Reveal';
import type { BatchResult } from '@/lib/types';
import { GlassCard, Section, SectionHead } from '@/components/ui/primitives';
import { PipelineSteps } from './PipelineSteps';

export function HowItWorks({ batch }: { batch: BatchResult }) {
  return (
    <Section id="pipeline">
      <SectionHead
        eyebrow="How it works"
        title={
          <>
            Seven layers. The model
            <br />
            only gets one of them.
          </>
        }
        lede="A failed payment enters at the top and leaves with a decision, an execution result, and a row in two ledgers. Inference is used where judgement is genuinely needed and kept out of everywhere else."
      />

      <div className="grid gap-10 lg:grid-cols-[1fr_360px] lg:gap-14">
        <PipelineSteps diagnosis={batch.pipelineStats} />

        <div className="flex flex-col gap-5 lg:sticky lg:top-28 lg:self-start">
          <Reveal variant="fade-up">
            <GlassCard className="p-6" tone="high">
              <h3 className="display text-[20px] text-ink">Where the LLM is not</h3>
              <p className="mt-3 text-[13.5px] leading-[1.62] text-ink-dim">
                Classifying a known reason code is a dictionary lookup. Doing it with a language
                model would add latency, cost and a failure mode, and buy nothing — the mapping is
                already deterministic. On the batch shown below,{' '}
                {(batch.pipelineStats.deterministicShare * 100).toFixed(0)}% of events resolved this
                way. The model is called for the rest: unmapped codes, free-text gateway messages,
                genuine ambiguity.
              </p>
            </GlassCard>
          </Reveal>

          <Reveal variant="fade-up">
            <GlassCard className="p-6">
              <h3 className="display text-[20px] text-ink">Why the gate sits after the engine</h3>
              <p className="mt-3 text-[13.5px] leading-[1.62] text-ink-dim">
                Compliance is not a term in the objective function. If it were, a large enough
                expected value could outvote it. The uplift engine proposes; the policy gate
                disposes, and it can veto any action for any reason in its rule set — including
                actions the engine is confident about.
              </p>
            </GlassCard>
          </Reveal>

          <Reveal variant="fade-up">
            <GlassCard className="p-6">
              <h3 className="display text-[20px] text-ink">Why the baseline runs too</h3>
              <p className="mt-3 text-[13.5px] leading-[1.62] text-ink-dim">
                The shadow ledger runs a probability-threshold policy over the same events, in the
                same batch, through the same gate. Every run produces its own comparison, so there
                is no batch to pick after the fact and no version of these numbers that came from
                choosing a good day.
              </p>
            </GlassCard>
          </Reveal>
        </div>
      </div>
    </Section>
  );
}
