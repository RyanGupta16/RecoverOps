'use client';

import { animate, onScroll, stagger } from 'animejs';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from '@/components/motion/useAnimeScope';
import { POLICY_RULES } from '@/lib/policy';

const CATEGORY_LABEL: Record<string, string> = {
  compliance: 'Compliance',
  frequency: 'Frequency',
  risk: 'Risk',
  economics: 'Economics',
};

export function PolicyTable() {
  const { root } = useAnimeScope<HTMLDivElement>((self, host) => {
    const { reduceMotion } = self.matches;

    animate('[data-rule-row]', {
      opacity: [0, 1],
      translateY: reduceMotion ? 0 : [14, 0],
      duration: reduceMotion ? 1 : 620,
      delay: stagger(reduceMotion ? 0 : 55),
      ease: BRAND_EASE,
      autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
    });
  });

  return (
    <div ref={root} className="overflow-x-auto hide-scrollbar">
      <table className="w-full min-w-[680px] border-collapse text-left">
        <caption className="sr-only">
          The twelve named policy rules, in evaluation order, with the regulation each enforces
          where applicable.
        </caption>
        <thead>
          <tr className="border-b border-hairline-hi">
            <th
              scope="col"
              className="py-3 pr-4 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-mute"
            >
              Rule ID
            </th>
            <th
              scope="col"
              className="py-3 pr-4 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-mute"
            >
              What it enforces
            </th>
            <th
              scope="col"
              className="py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-mute"
            >
              Class
            </th>
          </tr>
        </thead>
        <tbody>
          {POLICY_RULES.map((rule) => (
            <tr
              key={rule.id}
              data-rule-row
              className="reveal-init border-b border-hairline align-top"
            >
              <th
                scope="row"
                className="py-3.5 pr-4 font-mono text-[11.5px] font-normal text-amber"
              >
                {rule.id}
              </th>
              <td className="py-3.5 pr-4 text-[13.5px] leading-[1.55] text-ink-dim">
                {rule.description}
                {rule.citation && (
                  <span className="ml-2 whitespace-nowrap rounded-full bg-ink/[0.08] px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-ink-mute">
                    {rule.citation}
                  </span>
                )}
              </td>
              <td className="py-3.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-ink-mute">
                {CATEGORY_LABEL[rule.category]}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
