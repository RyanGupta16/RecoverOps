'use client';

import { createTimeline, onScroll, stagger } from 'animejs';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from '@/components/motion/useAnimeScope';
import { PIPELINE_LAYERS } from '@/lib/policy';

/**
 * The seven layers, sequenced so the connector between steps lights before the
 * step it leads to — the timeline reads as signal moving down a pipeline
 * rather than seven cards fading in.
 */
export function PipelineSteps({
  diagnosis,
}: {
  /** Measured coverage of the deterministic lookup on the batch being shown. */
  diagnosis?: { deterministicLookups: number; llmFallbacks: number; deterministicShare: number };
}) {
  const { root } = useAnimeScope<HTMLOListElement>((self, host) => {
    const { reduceMotion } = self.matches;

    const tl = createTimeline({
      defaults: { ease: BRAND_EASE },
      autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
    });

    if (reduceMotion) {
      tl.add('[data-step]', { opacity: [0, 1], duration: 1 })
        .add('[data-connector]', { opacity: [0, 1], duration: 1 }, 0)
        .add('[data-node]', { opacity: [0, 1], duration: 1 }, 0);
      return;
    }

    tl.add('[data-node]', {
      opacity: [0, 1],
      scale: [0.4, 1],
      duration: 460,
      delay: stagger(150),
    })
      .add(
        '[data-connector]',
        {
          opacity: [0, 1],
          scaleY: [0, 1],
          duration: 420,
          delay: stagger(150),
        },
        90,
      )
      .add(
        '[data-step]',
        {
          opacity: [0, 1],
          translateX: [22, 0],
          duration: 700,
          delay: stagger(150),
        },
        120,
      );
  });

  return (
    <ol ref={root} className="flex flex-col">
      {PIPELINE_LAYERS.map((layer, i) => (
        <li
          key={layer.n}
          className="grid grid-cols-[28px_1fr] gap-x-4 md:grid-cols-[36px_1fr] md:gap-x-6"
        >
          {/* Rail: node + connector down to the next step. */}
          <div className="flex flex-col items-center">
            <span
              data-node
              className="reveal-init mt-5 size-2.5 shrink-0 rounded-full bg-amber shadow-[0_0_14px_rgba(232,165,82,0.6)]"
            />
            {i < PIPELINE_LAYERS.length - 1 && (
              <span
                data-connector
                className="reveal-init mt-2 w-px flex-1 origin-top bg-gradient-to-b from-amber/50 to-hairline"
              />
            )}
          </div>

          <div
            data-step
            className="reveal-init mb-2 rounded-[14px] border border-hairline bg-glass p-5 backdrop-blur-xl md:p-6"
          >
            <div className="flex items-baseline gap-3">
              <span className="display text-[22px] leading-none text-amber">{layer.n}</span>
              <h3 className="text-[15.5px] font-semibold text-ink">{layer.title}</h3>
            </div>
            <p className="mt-2.5 text-[13.5px] leading-[1.6] text-ink-dim">{layer.body}</p>
            {'stat' in layer && layer.stat === 'diagnosisShare' && diagnosis && (
              <p className="mt-2.5 border-t border-hairline pt-2.5 font-mono text-[11px] leading-relaxed text-brass">
                On the batch shown here: {diagnosis.deterministicLookups} of{' '}
                {diagnosis.deterministicLookups + diagnosis.llmFallbacks} events resolved without a
                model call ({(diagnosis.deterministicShare * 100).toFixed(0)}%).
              </p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
