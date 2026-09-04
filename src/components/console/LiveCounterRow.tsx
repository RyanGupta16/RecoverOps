'use client';

import { animate, utils } from 'animejs';
import { useEffect, useRef } from 'react';
import { rupeesCompact } from '@/lib/format';

export interface Counter {
  key: string;
  label: string;
  value: number;
  kind?: 'number' | 'money';
  emphasis?: boolean;
}

/**
 * Ticking counters for a running batch. Each value tweens from its previous
 * reading to the new one rather than jumping, so a fast stream still reads as
 * motion rather than flicker.
 */
export function LiveCounterRow({ counters }: { counters: Counter[] }) {
  const refs = useRef<Record<string, HTMLSpanElement | null>>({});
  const previous = useRef<Record<string, number>>({});

  useEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const animations = counters.map((counter) => {
      const el = refs.current[counter.key];
      if (!el) return null;

      const format = (v: number) =>
        counter.kind === 'money' ? rupeesCompact(v) : Math.round(v).toLocaleString('en-IN');

      const from = previous.current[counter.key] ?? 0;
      previous.current[counter.key] = counter.value;

      if (reduceMotion || from === counter.value) {
        el.textContent = format(counter.value);
        return null;
      }

      const proxy = { v: from };
      return animate(proxy, {
        v: counter.value,
        duration: 420,
        ease: 'out(2)',
        modifier: utils.round(0),
        onUpdate: () => {
          el.textContent = format(proxy.v);
        },
        onComplete: () => {
          el.textContent = format(counter.value);
        },
      });
    });

    return () => {
      animations.forEach((a) => a?.revert());
    };
  }, [counters]);

  return (
    <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[10px] border border-hairline bg-hairline sm:grid-cols-3 lg:grid-cols-5">
      {counters.map((counter) => (
        <div key={counter.key} className="bg-[rgba(14,11,8,0.9)] px-3.5 py-3">
          <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-mute">
            {counter.label}
          </dt>
          <dd
            className={`mt-1.5 font-mono text-[19px] tabular-nums ${
              counter.emphasis ? 'text-amber' : 'text-ink'
            }`}
          >
            <span
              ref={(el) => {
                refs.current[counter.key] = el;
              }}
            >
              {counter.kind === 'money'
                ? rupeesCompact(counter.value)
                : counter.value.toLocaleString('en-IN')}
            </span>
          </dd>
        </div>
      ))}
    </dl>
  );
}
