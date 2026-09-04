'use client';

import { animate, onScroll, stagger } from 'animejs';
import type { ElementType, ReactNode } from 'react';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from './useAnimeScope';

type RevealVariant = 'fade-up' | 'rise' | 'fade';

const OFFSET: Record<RevealVariant, number> = {
  'fade-up': 26,
  rise: 50,
  fade: 0,
};

/**
 * Scroll reveal for a group of elements: the wrapper's direct children animate
 * in, staggered, as it enters the viewport.
 *
 * The children start hidden through the `reveal-group` class in the
 * server-rendered markup rather than through JavaScript on mount, so there is
 * no paint-then-blank flicker. Anime.js writes inline opacity over the class
 * and removes it again on revert().
 */
export function Reveal({
  children,
  className,
  variant = 'fade-up',
  delay = 0,
  stagger: staggerMs = 80,
  duration = 820,
  as: Tag = 'div',
}: {
  children: ReactNode;
  className?: string;
  variant?: RevealVariant;
  delay?: number;
  stagger?: number;
  duration?: number;
  as?: ElementType;
}) {
  const { root } = useAnimeScope<HTMLElement>((self, el) => {
    const { reduceMotion } = self.matches;

    const targets = Array.from(el.children) as HTMLElement[];
    if (targets.length === 0) return;

    animate(targets, {
      opacity: [0, 1],
      translateY: reduceMotion ? 0 : [OFFSET[variant], 0],
      duration: reduceMotion ? 1 : duration,
      delay: stagger(reduceMotion ? 0 : staggerMs, { start: reduceMotion ? 0 : delay }),
      ease: BRAND_EASE,
      autoplay: onScroll({ ...REVEAL_TRIGGER, target: el }),
    });
  });

  return (
    <Tag ref={root as React.Ref<HTMLElement>} className={`reveal-group ${className ?? ''}`}>
      {children}
    </Tag>
  );
}
