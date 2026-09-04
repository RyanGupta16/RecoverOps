'use client';

import { animate, onScroll, utils } from 'animejs';
import { useEffect, useRef } from 'react';

const FRAME_COUNT = 110;
const framePath = (i: number) => `/frames/frame-${String(i).padStart(3, '0')}.jpg`;

/**
 * Scroll-scrubbed cinematic background.
 *
 * Why a frame sequence rather than a <video> with currentTime driven by scroll:
 * seeking H.264 backwards or to a non-keyframe forces the decoder to jump to
 * the previous I-frame and re-decode forward, which stutters visibly on every
 * scroll tick. Frames pre-extracted at build time (npm run frames) turn a scrub
 * into an array lookup and one drawImage call.
 *
 * The whole thing is self-contained: local assets, no iframe, no postMessage,
 * no third-party host. If frames fail to load the page simply keeps its flat
 * background — the site never depends on this rendering.
 */
export function ScrubBackground({
  /** Fraction of total page scroll the sequence is mapped across. */
  scrubExtent = 0.7,
}: {
  scrubExtent?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;

    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return;

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const frames: HTMLImageElement[] = new Array(FRAME_COUNT);
    const loaded = new Set<number>();
    let disposed = false;
    let currentFrame = -1;
    let fadeIn: ReturnType<typeof animate> | null = null;

    /* --- painting ------------------------------------------------- */

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(window.innerWidth * dpr);
      canvas.height = Math.floor(window.innerHeight * dpr);
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;
      currentFrame = -1; // force a repaint at the new size
    };

    /** Nearest loaded frame, so scrubbing stays responsive while frames stream in. */
    const resolve = (index: number) => {
      if (loaded.has(index)) return frames[index];
      for (let d = 1; d < FRAME_COUNT; d += 1) {
        if (loaded.has(index - d)) return frames[index - d];
        if (loaded.has(index + d)) return frames[index + d];
      }
      return null;
    };

    const paint = (index: number) => {
      if (disposed || index === currentFrame) return;
      const img = resolve(index);
      if (!img) return;
      currentFrame = index;

      // object-fit: cover, by hand.
      const cw = canvas.width;
      const ch = canvas.height;
      const scale = Math.max(cw / img.naturalWidth, ch / img.naturalHeight);
      const w = img.naturalWidth * scale;
      const h = img.naturalHeight * scale;
      ctx.drawImage(img, (cw - w) / 2, (ch - h) / 2, w, h);
    };

    /* --- loading -------------------------------------------------- */

    const load = (index: number) =>
      new Promise<void>((resolve_) => {
        const img = new Image();
        img.decoding = 'async';
        img.src = framePath(index);
        img.onload = () => {
          frames[index] = img;
          loaded.add(index);
          resolve_();
        };
        img.onerror = () => resolve_();
      });

    // First frame paints immediately; the rest stream in coarse-to-fine so the
    // scrub is roughly correct early instead of sharp only at the top.
    const streamFrames = async () => {
      await load(0);
      if (disposed) return;
      resize();
      paint(0);
      // Faded up by Anime.js rather than a CSS transition, so the one motion
      // engine owns this too.
      fadeIn = animate(canvas, {
        opacity: [0, 0.85],
        duration: reduceMotion ? 1 : 700,
        ease: 'out(2)',
      });

      const order: number[] = [];
      for (let step = 16; step >= 1; step = Math.floor(step / 2)) {
        for (let i = 0; i < FRAME_COUNT; i += step) if (!order.includes(i)) order.push(i);
        if (step === 1) break;
      }

      const CONCURRENCY = 6;
      let cursor = 0;
      const workers = Array.from({ length: CONCURRENCY }, async () => {
        while (!disposed && cursor < order.length) {
          const i = order[cursor];
          cursor += 1;
          if (!loaded.has(i)) await load(i);
        }
      });
      await Promise.all(workers);
    };

    void streamFrames();

    window.addEventListener('resize', resize);

    /* --- scrubbing ------------------------------------------------ */

    // Anime.js drives both the frame index and the scrim from a single scroll
    // observer. `sync` ties playback to scroll position rather than to time,
    // with a little smoothing so a fast flick does not strobe the frames.
    const state = { frame: 0, scrim: 0 };
    const scrub = reduceMotion
      ? null
      : animate(state, {
          frame: FRAME_COUNT - 1,
          scrim: 1,
          ease: 'linear',
          modifier: utils.round(3),
          onUpdate: () => {
            paint(Math.round(utils.clamp(state.frame, 0, FRAME_COUNT - 1)));
            host.style.setProperty('--scrub-progress', String(state.scrim));
          },
          autoplay: onScroll({
            target: document.documentElement,
            // Map the sequence across the requested share of the document, so
            // the scrub finishes as the page hands off to its final act.
            //
            // Object form on purpose: the string shorthand is parsed as
            // "container target", so '70% bottom' would put the 70% on the
            // viewport and end the scrub at the bottom of the document — the
            // whole page rather than the first 70% of it.
            enter: { container: 'top', target: 'top' },
            leave: { container: 'top', target: `${Math.round(scrubExtent * 100)}%` },
            sync: 0.28, // a touch of smoothing so fast flicks do not strobe
          }),
        });

    if (reduceMotion) {
      host.style.setProperty('--scrub-progress', '0.55');
    }

    return () => {
      disposed = true;
      window.removeEventListener('resize', resize);
      fadeIn?.revert();
      scrub?.revert();
    };
  }, [scrubExtent]);

  return (
    <div
      ref={hostRef}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-0 bg-deep"
      style={{ ['--scrub-progress' as string]: '0' }}
    >
      <canvas ref={canvasRef} className="h-full w-full" style={{ opacity: 0 }} />

      {/*
        Warm grade. The clip is cyan-dominant and the brand is amber/rust/cream
        on near-black, so the raw footage fights the palette. These layers pull
        it warm and hold cream-on-dark contrast at an accessible ratio.
      */}
      <div
        className="absolute inset-0 mix-blend-color"
        style={{
          background: 'linear-gradient(150deg, #c86a3c, #e8a552 55%, #b8935a)',
          opacity: 0.55,
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(1200px 780px at 18% 12%, rgba(200,106,60,0.30), transparent 60%), radial-gradient(900px 620px at 84% 78%, rgba(232,165,82,0.20), transparent 55%)',
        }}
      />
      {/*
        Scrim deepens as the scrub advances: the hero can afford a light veil,
        but by the time dense stat cards and tables are on screen the background
        has to recede behind them.
      */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(180deg, rgba(13,10,8,0.92) 0%, rgba(13,10,8,0.82) 38%, rgba(13,10,8,0.95) 100%)',
          opacity: 'calc(0.84 + 0.16 * var(--scrub-progress))',
        }}
      />
      <div
        className="absolute inset-0 bg-deep"
        style={{ opacity: 'calc(0.22 + 0.30 * var(--scrub-progress))' }}
      />
    </div>
  );
}
