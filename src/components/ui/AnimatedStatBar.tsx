'use client';

import { animate, onScroll, stagger } from 'animejs';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from '@/components/motion/useAnimeScope';

export interface BarRow {
  label: string;
  baselineText: string;
  upliftText: string;
  /** 0–1. How full the RecoverOps bar runs relative to the track. */
  fill: number;
  /** 0–1. Same, for the baseline. */
  baselineFill: number;
  note?: string;
  /** Set when a lower number is the good outcome, so the colouring stays honest. */
  lowerIsBetter?: boolean;
}

/**
 * Before/after bars. Widths are tweened by Anime.js rather than a CSS width
 * transition, so the whole page has one motion engine and one set of easing.
 */
export function AnimatedStatBar({ rows }: { rows: BarRow[] }) {
  const { root } = useAnimeScope(
    (self, host) => {
      const { reduceMotion } = self.matches;

      // Animated one at a time rather than with a function-valued width, so each
      // bar's target comes straight off its own element and stays typed.
      const fills = Array.from(host.querySelectorAll<HTMLElement>('[data-fill]'));
      const delays = stagger(reduceMotion ? 0 : 110);

      fills.forEach((fill, i) => {
        animate(fill, {
          width: `${Number(fill.dataset.fillValue) * 100}%`,
          duration: reduceMotion ? 1 : 1400,
          delay: delays(fill, i, fills),
          ease: BRAND_EASE,
          autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
        });
      });
    },
    [rows.length],
  );

  return (
    <div ref={root} className="flex flex-col gap-7">
      {rows.map((row) => (
        <div key={row.label}>
          <h4 className="text-[12px] font-medium uppercase tracking-[0.14em] text-ink-dim">
            {row.label}
          </h4>

          <div className="mt-3 flex items-baseline justify-between gap-4">
            <span className="display text-[18px] text-ink-mute">{row.baselineText}</span>
            <span className="display text-[clamp(26px,3.4vw,36px)] font-medium text-amber">
              {row.upliftText}
            </span>
          </div>

          {/* Two tracks, so the comparison is visible rather than implied. */}
          <div className="mt-3 space-y-1.5">
            <div className="flex items-center gap-3">
              <span className="w-[74px] shrink-0 font-mono text-[9.5px] uppercase tracking-[0.1em] text-ink-mute">
                Baseline
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink/[0.08]">
                <div
                  data-fill
                  data-fill-value={row.baselineFill}
                  style={{ width: 0 }}
                  className="h-full rounded-full bg-ink-mute/70"
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className="w-[74px] shrink-0 font-mono text-[9.5px] uppercase tracking-[0.1em] text-amber">
                RecoverOps
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink/[0.08]">
                <div
                  data-fill
                  data-fill-value={row.fill}
                  style={{ width: 0 }}
                  className="h-full rounded-full bg-gradient-to-r from-rust to-amber"
                />
              </div>
            </div>
          </div>

          {row.note && (
            <p className="mt-2.5 text-[12px] leading-relaxed text-ink-mute">{row.note}</p>
          )}
        </div>
      ))}
    </div>
  );
}
