'use client';

import { animate, onScroll, stagger } from 'animejs';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from '@/components/motion/useAnimeScope';
import { EyebrowLabel, Section } from '@/components/ui/primitives';

interface Tile {
  glyph: string;
  title: string;
  body: string;
  /** Column span in the 6-column bento grid at desktop width. */
  span: 2 | 3 | 4;
}

/**
 * Written out in full so Tailwind can see the class names. The grid collapses
 * to one column below `md`, where these spans must not apply — which rules out
 * an inline gridColumn style, since that would survive the breakpoint.
 */
const SPAN_CLASS: Record<Tile['span'], string> = {
  2: 'md:col-span-2',
  3: 'md:col-span-3',
  4: 'md:col-span-4',
};

const TILES: Tile[] = [
  {
    glyph: '⌗',
    title: 'Retrieval grounded in Razorpay’s own corpus',
    body: 'Diagnosis cites Razorpay’s published error-code documentation rather than recalling it. An unmapped code returns a miss and escalates, instead of producing a confident sentence about a code that does not exist.',
    span: 4,
  },
  {
    glyph: '◷',
    title: 'Case memory',
    body: 'Every resolved case is written back with the action tried and what followed, so the next similar failure is decided against precedent.',
    span: 2,
  },
  {
    glyph: '⇅',
    title: 'Two-model uplift',
    body: 'A T-learner estimates both branches separately and subtracts, with a documented fallback to hand-specified segment priors when the learned model is not ready.',
    span: 2,
  },
  {
    glyph: '§',
    title: 'A gate that cites its rules',
    body: 'Twenty-nine named rules, eighteen of them citing the published regulation they enforce, evaluated in order. Blocks carry the rule ID and a plain-English reason, and rules after the first block are recorded as not-evaluated rather than quietly skipped.',
    span: 4,
  },
  {
    glyph: '⊞',
    title: 'Dual ledger',
    body: 'Audit and shadow, written in the same pass. Every batch carries its own baseline comparison — there is no separate evaluation step to cherry-pick.',
    span: 3,
  },
  {
    glyph: '↗',
    title: 'Real test-mode execution',
    body: 'Orders, payment links, invoices and virtual accounts are genuine Razorpay test-mode API calls, and the payment-downtime feed is read live. Razorpay exposes no merchant retry endpoint, so a failed subscription charge waits on its own T+1 schedule — the trace says so rather than inventing a call. Outbound SMS and WhatsApp delivery is mocked, and says so wherever it appears.',
    span: 3,
  },
];

export function Capabilities() {
  const { root } = useAnimeScope((self, host) => {
    const { reduceMotion } = self.matches;

    animate('[data-tile]', {
      opacity: [0, 1],
      translateY: reduceMotion ? 0 : [40, 0],
      duration: reduceMotion ? 1 : 780,
      delay: stagger(reduceMotion ? 0 : 90),
      ease: BRAND_EASE,
      autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
    });

    if (reduceMotion) return;

    const tiles = Array.from(host.querySelectorAll<HTMLElement>('[data-tile]'));
    const bindings = tiles.map((tile) => {
      const enter = () =>
        animate(tile, {
          translateY: -5,
          borderColor: 'rgba(247,242,232,0.28)',
          duration: 380,
          ease: 'out(3)',
        });
      const leave = () =>
        animate(tile, {
          translateY: 0,
          borderColor: 'rgba(247,242,232,0.14)',
          duration: 460,
          ease: 'out(3)',
        });
      tile.addEventListener('pointerenter', enter);
      tile.addEventListener('pointerleave', leave);
      return () => {
        tile.removeEventListener('pointerenter', enter);
        tile.removeEventListener('pointerleave', leave);
      };
    });

    return () => bindings.forEach((unbind) => unbind());
  });

  return (
    <Section id="capabilities">
      <div className="mb-14 max-w-[760px]">
        <EyebrowLabel>Capabilities</EyebrowLabel>
        <h2 className="display mt-4 text-[clamp(32px,4.6vw,60px)] text-ink">
          Built for the ways
          <br />
          payments actually break.
        </h2>
      </div>

      <div ref={root} className="grid gap-4 md:grid-cols-6 md:gap-5">
        {TILES.map((tile) => (
          <article
            key={tile.title}
            data-tile
            className={`reveal-init flex flex-col justify-between rounded-[var(--radius-card)] border border-hairline bg-glass p-6 backdrop-blur-xl md:p-7 ${SPAN_CLASS[tile.span]}`}
          >
            <span
              aria-hidden="true"
              className="mb-6 flex size-10 items-center justify-center rounded-[10px] border border-amber/30 font-display text-[19px] text-amber"
              style={{
                background: 'linear-gradient(135deg, rgba(232,165,82,0.22), rgba(200,106,60,0.10))',
              }}
            >
              {tile.glyph}
            </span>
            <div>
              <h3 className="display text-[clamp(19px,2.1vw,23px)] leading-tight text-ink">
                {tile.title}
              </h3>
              <p className="mt-2.5 text-[13.5px] leading-[1.62] text-ink-dim">{tile.body}</p>
            </div>
          </article>
        ))}
      </div>
    </Section>
  );
}
