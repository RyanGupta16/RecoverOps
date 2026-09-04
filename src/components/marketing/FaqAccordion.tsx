'use client';

import { animate } from 'animejs';
import { useEffect, useRef, useState } from 'react';
import { BRAND_EASE } from '@/components/motion/useAnimeScope';

export interface FaqItem {
  q: string;
  a: React.ReactNode;
}

/**
 * Height + opacity tween driven by Anime.js.
 *
 * Collapse uses the isLeaving/onComplete pattern: the panel stays mounted while
 * it animates shut and is only unmounted from the callback, which is how this
 * project handles exit transitions without adding a second animation library
 * for an AnimatePresence-style component.
 */
export function FaqAccordion({ items }: { items: FaqItem[] }) {
  const [open, setOpen] = useState<number | null>(0);
  const [leaving, setLeaving] = useState<number | null>(null);
  const panels = useRef<Record<number, HTMLDivElement | null>>({});
  const icons = useRef<Record<number, HTMLSpanElement | null>>({});

  const reduced = () =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /** The marker rotates through Anime.js like everything else on the page. */
  const spinIcon = (index: number, to: number) => {
    const icon = icons.current[index];
    if (!icon) return;
    if (reduced()) {
      icon.style.transform = `rotate(${to}deg)`;
      return;
    }
    animate(icon, { rotate: to, duration: 340, ease: BRAND_EASE });
  };

  // The first item renders open, so it needs to start expanded rather than
  // waiting for a click that will never come.
  useEffect(() => {
    if (open === null) return;
    const panel = panels.current[open];
    if (!panel) return;
    panel.style.height = 'auto';
    panel.style.opacity = '1';
    const icon = icons.current[open];
    if (icon) icon.style.transform = 'rotate(45deg)';
    // Mount only — later opens are animated by toggle().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const expand = (index: number) => {
    const panel = panels.current[index];
    if (!panel) return;
    if (reduced()) {
      panel.style.height = 'auto';
      panel.style.opacity = '1';
      return;
    }
    animate(panel, {
      height: [0, panel.scrollHeight],
      opacity: [0, 1],
      duration: 520,
      ease: BRAND_EASE,
      onComplete: () => {
        // Release the fixed height so the panel reflows with its content.
        panel.style.height = 'auto';
      },
    });
  };

  const collapse = (index: number, then: () => void) => {
    const panel = panels.current[index];
    if (!panel || reduced()) {
      then();
      return;
    }
    animate(panel, {
      height: [panel.scrollHeight, 0],
      opacity: [1, 0],
      duration: 400,
      ease: 'inQuad',
      onComplete: then,
    });
  };

  const toggle = (index: number) => {
    if (open === index) {
      setLeaving(index);
      spinIcon(index, 0);
      collapse(index, () => {
        setOpen(null);
        setLeaving(null);
      });
      return;
    }

    if (open !== null) {
      const previous = open;
      setLeaving(previous);
      spinIcon(previous, 0);
      collapse(previous, () => {
        setLeaving(null);
        setOpen(index);
        spinIcon(index, 45);
        requestAnimationFrame(() => expand(index));
      });
      return;
    }

    setOpen(index);
    spinIcon(index, 45);
    requestAnimationFrame(() => expand(index));
  };

  return (
    <div className="mx-auto flex max-w-[880px] flex-col gap-3">
      {items.map((item, i) => {
        const isOpen = open === i;
        const isVisible = isOpen || leaving === i;
        return (
          <div
            key={item.q}
            className={`overflow-hidden rounded-[14px] border bg-glass backdrop-blur-xl ${
              isOpen ? 'border-hairline-hi' : 'border-hairline'
            }`}
          >
            <h3>
              <button
                type="button"
                onClick={() => toggle(i)}
                aria-expanded={isOpen}
                aria-controls={`faq-panel-${i}`}
                id={`faq-button-${i}`}
                className="flex w-full items-center justify-between gap-5 px-5 py-5 text-left md:px-6"
              >
                <span className="display text-[clamp(16px,2vw,19px)] font-medium text-ink">
                  {item.q}
                </span>
                <span
                  aria-hidden="true"
                  ref={(el) => {
                    icons.current[i] = el;
                  }}
                  className="shrink-0 text-[22px] font-light leading-none text-amber"
                >
                  +
                </span>
              </button>
            </h3>

            {isVisible && (
              <div
                id={`faq-panel-${i}`}
                role="region"
                aria-labelledby={`faq-button-${i}`}
                ref={(el) => {
                  panels.current[i] = el;
                }}
                style={{ height: 0, opacity: 0, overflow: 'hidden' }}
              >
                <div className="px-5 pb-6 text-[14px] leading-[1.68] text-ink-dim md:px-6">
                  {item.a}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
