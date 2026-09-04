'use client';

import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { createScope, type Scope } from 'animejs';

/**
 * The one way this project talks to Anime.js.
 *
 * Every animated component mounts a scope rooted at its own subtree, so
 * selector strings can never reach outside the component, and every scope is
 * reverted on unmount. That cleanup is not optional polish: React Strict Mode
 * double-invokes effects in development, and without revert() the first scope's
 * animations and its leftover inline style overrides survive the remount. The
 * symptom is motion that compounds and grows janky the longer a demo session
 * runs — precisely the failure mode you do not want in front of a judge.
 *
 * `self.matches.reduceMotion` comes from the mediaQueries option, so
 * accessibility is decided in the same place as the animation rather than
 * bolted on afterwards.
 */
/**
 * `host` is the scope's root element, handed in rather than read off the ref
 * the caller is in the middle of declaring — closing over that ref would mean
 * reading a binding before it exists.
 */
export type ScopeSetup<T extends HTMLElement = HTMLDivElement> = (
  self: Scope & { matches: Record<string, boolean> },
  host: T,
) => void | (() => void);

export function useAnimeScope<T extends HTMLElement = HTMLDivElement>(
  setup: ScopeSetup<T>,
  deps: unknown[] = [],
): { root: RefObject<T | null>; scope: RefObject<Scope | null> } {
  const root = useRef<T>(null);
  const scope = useRef<Scope | null>(null);

  // `setup` is almost always an inline closure, so it changes identity on every
  // render. Holding it in a ref lets callers omit it from deps without the
  // scope being torn down and rebuilt on each render. The ref is written in a
  // layout effect rather than during render, and declared before the scope
  // effect below so it is always current by the time the scope is built.
  const setupRef = useRef(setup);
  useLayoutEffect(() => {
    setupRef.current = setup;
  });

  useEffect(() => {
    const host = root.current;
    if (!host) return;

    scope.current = createScope({
      root,
      mediaQueries: { reduceMotion: '(prefers-reduced-motion: reduce)' },
    }).add((self) => {
      // Anime.js runs whatever the constructor returns as part of revert(), so
      // listeners and observers registered here are torn down with the scope.
      return setupRef.current(self as Scope & { matches: Record<string, boolean> }, host);
    });

    return () => {
      scope.current?.revert();
      scope.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { root, scope };
}

/** Scroll trigger shared by every reveal in the project. Fires once. */
export const REVEAL_TRIGGER = {
  enter: 'bottom-=64 top',
  leave: 'top bottom',
  repeat: false,
} as const;

/** The brand motion curve, expressed the way Anime.js v4 wants it. */
export const BRAND_EASE = 'cubicBezier(0.22, 1, 0.36, 1)';
