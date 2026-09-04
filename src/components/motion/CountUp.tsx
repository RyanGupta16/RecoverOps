'use client';

import { animate, onScroll, utils } from 'animejs';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from './useAnimeScope';

/**
 * Tweens the number itself rather than just fading the element in — Anime.js
 * animates a plain object and each frame writes the formatted value.
 */
export function CountUp({
  to,
  from = 0,
  decimals = 0,
  prefix = '',
  suffix = '',
  duration = 1600,
  delay = 0,
  className,
  format,
}: {
  to: number;
  from?: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  duration?: number;
  delay?: number;
  className?: string;
  /** Overrides prefix/suffix/decimals entirely when supplied. */
  format?: (value: number) => string;
}) {
  const render = (value: number) =>
    format ? format(value) : `${prefix}${value.toFixed(decimals)}${suffix}`;

  const { root } = useAnimeScope<HTMLSpanElement>(
    (self, host) => {
      const { reduceMotion } = self.matches;
      const out = host.querySelector<HTMLElement>('[data-countup-value]');
      if (!out) return;

      if (reduceMotion) {
        out.textContent = render(to);
        return;
      }

      const proxy = { value: from };
      out.textContent = render(from);

      animate(proxy, {
        value: to,
        duration,
        delay,
        ease: BRAND_EASE,
        // Integer counters must never flash a fractional value mid-tween.
        ...(format ? {} : { modifier: utils.round(decimals) }),
        onUpdate: () => {
          out.textContent = render(proxy.value);
        },
        onComplete: () => {
          // Land exactly on the target rather than wherever the last frame fell.
          out.textContent = render(to);
        },
        autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
      });
    },
    [to, from, decimals, prefix, suffix],
  );

  return (
    <span ref={root} className={className}>
      {/* Server-rendered final value: correct without JS, and what a screen
          reader announces. The tween overwrites it on the client. */}
      <span data-countup-value>{render(to)}</span>
    </span>
  );
}
