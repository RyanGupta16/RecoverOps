'use client';

import { animate, createTimeline, stagger } from 'animejs';
import { BRAND_EASE, useAnimeScope } from '@/components/motion/useAnimeScope';
import { ButtonLink, EyebrowLabel } from '@/components/ui/primitives';
import { LiveFeedCard, type FeedRow } from './LiveFeedCard';

/**
 * Facts about the build itself. Nothing here is a performance claim — there is
 * no "+41.6% uplift" badge, because no such number has been measured on real
 * traffic and inventing one is exactly what the brief rules out.
 */
const HERO_FACTS = [
  { label: 'Pipeline layers', value: '7' },
  { label: 'Policy rules enforced', value: '12' },
  { label: 'Evaluation batch', value: '500 events' },
];

export function Hero({ feed }: { feed: FeedRow[] }) {
  const { root } = useAnimeScope<HTMLElement>((self, host) => {
    const { reduceMotion } = self.matches;

    // One continuous sequence rather than five elements that happen to fade in
    // at once — this is the first thing a judge sees.
    const tl = createTimeline({
      defaults: { ease: BRAND_EASE, duration: reduceMotion ? 1 : 900 },
    });

    tl.add('[data-hero-eyebrow]', {
      opacity: [0, 1],
      translateY: reduceMotion ? 0 : [16, 0],
    })
      .add(
        '[data-hero-line]',
        {
          opacity: [0, 1],
          translateY: reduceMotion ? 0 : [34, 0],
          delay: stagger(reduceMotion ? 0 : 90),
        },
        reduceMotion ? 0 : '-=620',
      )
      .add(
        '[data-hero-sub]',
        { opacity: [0, 1], translateY: reduceMotion ? 0 : [22, 0] },
        reduceMotion ? 0 : '-=560',
      )
      .add(
        '[data-hero-cta]',
        {
          opacity: [0, 1],
          translateY: reduceMotion ? 0 : [18, 0],
          delay: stagger(reduceMotion ? 0 : 70),
        },
        reduceMotion ? 0 : '-=520',
      )
      .add(
        '[data-hero-fact]',
        {
          opacity: [0, 1],
          translateY: reduceMotion ? 0 : [14, 0],
          delay: stagger(reduceMotion ? 0 : 60),
        },
        reduceMotion ? 0 : '-=480',
      )
      .add(
        '[data-hero-panel]',
        { opacity: [0, 1], translateY: reduceMotion ? 0 : [46, 0] },
        reduceMotion ? 0 : '-=900',
      );

    // Press feedback on the CTAs. Anime.js owns this too — no CSS :active
    // transition anywhere in the project.
    const buttons = Array.from(host.querySelectorAll<HTMLElement>('[data-press]'));
    const down = (el: HTMLElement) => () =>
      animate(el, {
        scale: reduceMotion ? 1 : 0.965,
        duration: 140,
        ease: 'out(2)',
      });
    const up = (el: HTMLElement) => () => animate(el, { scale: 1, duration: 420, ease: 'out(3)' });

    const bindings = buttons.map((el) => {
      const d = down(el);
      const u = up(el);
      el.addEventListener('pointerdown', d);
      el.addEventListener('pointerup', u);
      el.addEventListener('pointerleave', u);
      return () => {
        el.removeEventListener('pointerdown', d);
        el.removeEventListener('pointerup', u);
        el.removeEventListener('pointerleave', u);
      };
    });

    return () => bindings.forEach((unbind) => unbind());
  });

  return (
    <section
      id="home"
      ref={root}
      className="relative flex min-h-[100svh] flex-col justify-end px-5 pb-16 pt-28 md:px-10 md:pb-20"
    >
      <div className="mx-auto grid w-full max-w-[1200px] items-end gap-12 lg:grid-cols-[1.25fr_1fr] lg:gap-14">
        <div>
          <div data-hero-eyebrow className="reveal-init">
            <EyebrowLabel>Failed-payment recovery · Razorpay buildathon</EyebrowLabel>
          </div>

          <h1 className="display mt-5 text-[clamp(40px,6.6vw,88px)] text-ink">
            <span data-hero-line className="reveal-init block">
              Every retry system asks
            </span>
            <span data-hero-line className="reveal-init block">
              who will pay.
            </span>
            <span data-hero-line className="reveal-init block">
              <em className="font-light not-italic text-amber italic">Wrong question.</em>
            </span>
          </h1>

          <p
            data-hero-sub
            className="reveal-init mt-7 max-w-[540px] text-[16.5px] leading-[1.62] text-ink-dim"
          >
            RecoverOps ranks failed payments by causal uplift — how much contacting a customer
            actually changes the outcome — not by how likely they are to pay. Some recover on their
            own, and chasing them is wasted spend. Some cancel{' '}
            <em className="italic text-ink">because</em> you reminded them a payment failed. Every
            system that ranks by probability treats all three the same.
          </p>

          <div className="mt-9 flex flex-wrap gap-3.5">
            <div data-hero-cta className="reveal-init">
              <ButtonLink href="/console">Run the live demo</ButtonLink>
            </div>
            <div data-hero-cta className="reveal-init">
              <ButtonLink href="#insight" variant="ghost">
                See the insight
              </ButtonLink>
            </div>
          </div>

          <dl className="mt-10 flex flex-wrap gap-x-9 gap-y-5">
            {HERO_FACTS.map((fact) => (
              <div key={fact.label} data-hero-fact className="reveal-init">
                <dt className="text-[11px] uppercase tracking-[0.14em] text-ink-mute">
                  {fact.label}
                </dt>
                <dd className="display mt-1 text-[22px] text-ink">{fact.value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div data-hero-panel className="reveal-init">
          <LiveFeedCard rows={feed} />
        </div>
      </div>
    </section>
  );
}
