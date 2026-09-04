'use client';

import { animate, createTimeline, onScroll, stagger, svg } from 'animejs';
import { useState } from 'react';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from '@/components/motion/useAnimeScope';

type Key = 'sure_thing' | 'persuadable' | 'lost_cause' | 'sleeping_dog';

interface Quadrant {
  key: Key;
  name: string;
  /** Grid position in the 2×2. */
  col: 0 | 1;
  row: 0 | 1;
  short: string;
  detail: string;
  verdict: string;
  accent: string;
}

/**
 * Axes: recovery without contact (x) against recovery with contact (y).
 * The diagonal is where contact changes nothing. Everything above it is
 * uplift; everything below it is damage.
 */
const QUADRANTS: Quadrant[] = [
  {
    key: 'persuadable',
    name: 'Persuadable',
    col: 0,
    row: 0,
    short: 'Recovers only if you intervene',
    detail:
      'Wants to keep paying, hit a fixable wall — a stale card, a limit, a balance that cleared this morning. Contact is what moves them.',
    verdict: 'The entire target. Spend the budget here.',
    accent: 'var(--color-amber)',
  },
  {
    key: 'sure_thing',
    name: 'Sure thing',
    col: 1,
    row: 0,
    short: 'Recovers either way',
    detail:
      'The issuer was down for ten minutes, or the balance lands tomorrow. A silent retry collects the money without spending a message or a scrap of goodwill.',
    verdict: 'Contact is pure cost. Retry quietly.',
    accent: 'var(--color-brass)',
  },
  {
    key: 'lost_cause',
    name: 'Lost cause',
    col: 0,
    row: 1,
    short: 'Recovers under neither branch',
    detail:
      'The mandate is revoked, the card is dead, the customer left months ago. Every message is spend against an outcome that was already decided.',
    verdict: 'Stop the ladder early. Escalate if the amount warrants it.',
    accent: 'var(--color-ink-mute)',
  },
  {
    key: 'sleeping_dog',
    name: 'Sleeping dog',
    col: 1,
    row: 1,
    short: 'Contact makes it worse',
    detail:
      'A semi-engaged subscriber who would have paid on the retry. Tell them a payment failed and you have reminded them they are paying for something they barely open. They cancel.',
    verdict: 'Negative uplift. Do nothing — and log that you did nothing.',
    accent: 'var(--color-verdict-block)',
  },
];

export function QuadrantDiagram() {
  const [active, setActive] = useState<Key>('sleeping_dog');
  const activeQuadrant = QUADRANTS.find((q) => q.key === active)!;

  const { root } = useAnimeScope((self, host) => {
    const { reduceMotion } = self.matches;

    const tl = createTimeline({
      defaults: { ease: BRAND_EASE },
      autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
    });

    if (reduceMotion) {
      // Draw everything, instantly, with no transforms.
      tl.add(svg.createDrawable('[data-draw]'), {
        draw: ['0 0', '0 1'],
        duration: 1,
      })
        .add('[data-quad]', { opacity: [0, 1], duration: 1 }, 0)
        .add('[data-axis-label]', { opacity: [0, 1], duration: 1 }, 0);
      return;
    }

    // Axes draw first, then the diagonal that separates uplift from damage,
    // then the four cells land on top of it.
    tl.add(svg.createDrawable('[data-draw-axis]'), {
      draw: ['0 0', '0 1'],
      duration: 760,
    })
      .add(
        svg.createDrawable('[data-draw-diagonal]'),
        { draw: ['0 0', '0 1'], duration: 720 },
        '-=280',
      )
      .add('[data-axis-label]', { opacity: [0, 1], duration: 520, delay: stagger(70) }, '-=500')
      .add(
        '[data-quad]',
        {
          opacity: [0, 1],
          scale: [0.9, 1],
          duration: 700,
          delay: stagger(110),
        },
        '-=420',
      );

    // Hover lift for each cell, registered as a scope method so the handlers
    // are torn down with the scope.
    const cells = Array.from(host.querySelectorAll<HTMLElement>('[data-quad]'));
    const bindings = cells.map((cell) => {
      const enter = () => animate(cell, { translateY: -4, duration: 320, ease: 'out(3)' });
      const leave = () => animate(cell, { translateY: 0, duration: 420, ease: 'out(3)' });
      cell.addEventListener('pointerenter', enter);
      cell.addEventListener('pointerleave', leave);
      return () => {
        cell.removeEventListener('pointerenter', enter);
        cell.removeEventListener('pointerleave', leave);
      };
    });

    return () => bindings.forEach((unbind) => unbind());
  });

  return (
    <div ref={root} className="grid gap-8 lg:grid-cols-[1.15fr_0.85fr] lg:gap-12">
      <div className="relative">
        {/* Axis frame. The SVG carries only the rules and labels; the cells are
            real focusable buttons layered over it, so the diagram is keyboard
            navigable rather than being a picture of an idea. */}
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
          className="absolute inset-0 size-full"
        >
          <line
            data-draw
            data-draw-axis
            x1="0"
            y1="100"
            x2="100"
            y2="100"
            stroke="var(--color-hairline-hi)"
            strokeWidth="0.4"
            vectorEffect="non-scaling-stroke"
          />
          <line
            data-draw
            data-draw-axis
            x1="0"
            y1="0"
            x2="0"
            y2="100"
            stroke="var(--color-hairline-hi)"
            strokeWidth="0.4"
            vectorEffect="non-scaling-stroke"
          />
          <line
            data-draw
            data-draw-diagonal
            x1="0"
            y1="100"
            x2="100"
            y2="0"
            stroke="var(--color-amber)"
            strokeWidth="0.4"
            strokeDasharray="2 2"
            opacity="0.5"
            vectorEffect="non-scaling-stroke"
          />
        </svg>

        <div className="relative grid aspect-[1.5] grid-cols-2 grid-rows-2 gap-2.5 p-1 sm:gap-3.5">
          {QUADRANTS.map((q) => {
            const isActive = q.key === active;
            return (
              <button
                key={q.key}
                type="button"
                data-quad
                onPointerEnter={() => setActive(q.key)}
                onFocus={() => setActive(q.key)}
                onClick={() => setActive(q.key)}
                aria-pressed={isActive}
                className={[
                  'reveal-init flex flex-col justify-end gap-3 rounded-[16px] border p-4 text-left backdrop-blur-xl sm:p-5',
                  isActive
                    ? 'border-hairline-hi bg-glass-hi'
                    : 'border-hairline bg-glass hover:border-hairline-hi',
                ].join(' ')}
                style={{ gridColumn: q.col + 1, gridRow: q.row + 1 }}
              >
                <span
                  className="size-2 rounded-full"
                  style={{
                    background: q.accent,
                    boxShadow: `0 0 12px ${q.accent}`,
                  }}
                />
                <span>
                  <span className="display block text-[clamp(17px,2.1vw,24px)] text-ink">
                    {q.name}
                  </span>
                  <span className="mt-1.5 block text-[12px] leading-snug text-ink-mute">
                    {q.short}
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        <div className="pointer-events-none mt-3 flex justify-between font-mono text-[10px] uppercase tracking-[0.14em] text-ink-mute">
          <span data-axis-label className="reveal-init">
            Recovers without contact →
          </span>
          <span data-axis-label className="reveal-init text-right">
            Dashed line: contact changes nothing
          </span>
        </div>
      </div>

      <div className="flex flex-col justify-center">
        <div
          className="rounded-[var(--radius-card)] border border-hairline-hi bg-glass-hi p-6 backdrop-blur-xl md:p-7"
          aria-live="polite"
        >
          <span
            className="inline-block size-2 rounded-full"
            style={{
              background: activeQuadrant.accent,
              boxShadow: `0 0 12px ${activeQuadrant.accent}`,
            }}
          />
          <h3 className="display mt-3.5 text-[clamp(24px,3vw,34px)] text-ink">
            {activeQuadrant.name}
          </h3>
          <p className="mt-3 text-[14.5px] leading-[1.62] text-ink-dim">{activeQuadrant.detail}</p>
          <p className="mt-5 border-t border-hairline pt-4 font-mono text-[11.5px] leading-relaxed text-amber">
            {activeQuadrant.verdict}
          </p>
        </div>
        <p className="mt-4 px-1 text-[12px] leading-relaxed text-ink-mute">
          Hover, tap or tab through the cells. A probability model can only see the vertical axis —
          how likely someone is to pay once contacted. It cannot tell a persuadable from a sleeping
          dog, because both can look moderately likely to pay.
        </p>
      </div>
    </div>
  );
}
