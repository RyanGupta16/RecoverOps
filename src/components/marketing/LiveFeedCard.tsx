'use client';

import { animate } from 'animejs';
import { useEffect, useRef, useState } from 'react';
import { BRAND_EASE, useAnimeScope } from '@/components/motion/useAnimeScope';
import { GlassCard, Tag } from '@/components/ui/primitives';
import { rupeesPrecise, signed } from '@/lib/format';

export interface FeedRow {
  eventId: string;
  paymentId: string;
  amountPaise: number;
  reasonLabel: string;
  action: string;
  contacted: boolean;
  blockedBy: string | null;
  upliftHat: number;
  issuer: string;
}

/**
 * Hero decision feed. Rows arrive one at a time so the card reads as a system
 * making decisions rather than a static list, but the header says plainly that
 * this is a replay of the synthetic batch. It is not live production traffic,
 * and it does not pretend to be.
 */
export function LiveFeedCard({ rows }: { rows: FeedRow[] }) {
  // Starts at one row so the server-rendered markup and the first client render
  // agree; the rest are scheduled, never set synchronously inside the effect.
  const [visible, setVisible] = useState(1);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (rows.length <= 1) return;
    const timers: ReturnType<typeof setTimeout>[] = [];

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      timers.push(setTimeout(() => setVisible(rows.length), 0));
    } else {
      for (let i = 2; i <= rows.length; i += 1) {
        timers.push(setTimeout(() => setVisible(i), 620 + (i - 2) * 780));
      }
    }

    return () => timers.forEach(clearTimeout);
  }, [rows.length]);

  // Each newly appended row gets its own entrance tween rather than popping in.
  const { root } = useAnimeScope(
    (self) => {
      const { reduceMotion } = self.matches;
      if (reduceMotion || visible === 0) return;
      const el = listRef.current?.querySelector<HTMLElement>(`[data-row-index="${visible - 1}"]`);
      if (!el) return;
      animate(el, {
        opacity: [0, 1],
        translateY: [12, 0],
        duration: 620,
        ease: BRAND_EASE,
      });
    },
    [visible],
  );

  return (
    <div ref={root}>
      <GlassCard className="p-5 md:p-6" tone="high">
        <div className="mb-4 flex items-center justify-between gap-3 border-b border-hairline pb-3.5">
          <h2 className="text-[12px] font-medium uppercase tracking-[0.2em] text-ink-dim">
            Decision feed
          </h2>
          <Tag tone="amber">Sample data</Tag>
        </div>

        <ul ref={listRef} className="flex flex-col">
          {rows.slice(0, visible).map((row, i) => (
            <li
              key={row.eventId}
              data-row-index={i}
              className="flex items-start justify-between gap-4 border-b border-dashed border-hairline py-3 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="font-mono text-[12px] text-ink">{row.paymentId}</p>
                <p className="mt-1 text-[11.5px] leading-snug text-ink-mute">
                  {row.reasonLabel} · {row.issuer}
                </p>
                <p className="mt-1.5">
                  {row.blockedBy ? (
                    <Tag tone="quiet">Gated · {row.blockedBy}</Tag>
                  ) : row.contacted ? (
                    <Tag tone="rust">{row.action}</Tag>
                  ) : (
                    <Tag tone="neutral">{row.action}</Tag>
                  )}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p className="display text-[15px] text-ink">{rupeesPrecise(row.amountPaise)}</p>
                <p className="mt-1 font-mono text-[10.5px] text-ink-mute">
                  uplift {signed(row.upliftHat)}
                </p>
              </div>
            </li>
          ))}
        </ul>

        <p className="mt-4 border-t border-hairline pt-3 text-[11px] leading-relaxed text-ink-mute">
          Replay of the bundled synthetic batch. Not live traffic — the console runs the same events
          end to end.
        </p>
      </GlassCard>
    </div>
  );
}
