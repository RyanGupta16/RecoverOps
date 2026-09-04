'use client';

import { animate, onScroll, stagger } from 'animejs';
import type { ReactNode } from 'react';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from '@/components/motion/useAnimeScope';
import type { Verdict } from '@/lib/types';

/**
 * Register B. Same tokens as the marketing site — same near-black, same amber —
 * but denser, monospace-forward and deliberately less decorative. A judge
 * moving from the pitch to the product should feel one brand, not two apps.
 */

export function TerminalPanel({
  title,
  meta,
  children,
  className = '',
  actions,
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
  actions?: ReactNode;
}) {
  return (
    <section
      className={`rounded-[12px] border border-hairline bg-[rgba(14,11,8,0.72)] ${className}`}
    >
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h2 className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink">{title}</h2>
          {meta && <span className="font-mono text-[10.5px] text-ink-mute">{meta}</span>}
        </div>
        {actions}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function RuleVerdictBadge({ verdict }: { verdict: Verdict }) {
  const styles: Record<Verdict, string> = {
    PASS: 'border-[var(--color-verdict-pass)]/45 bg-[var(--color-verdict-pass)]/12 text-[var(--color-verdict-pass)]',
    BLOCK:
      'border-[var(--color-verdict-block)]/50 bg-[var(--color-verdict-block)]/14 text-[var(--color-verdict-block)]',
    'N/A': 'border-hairline bg-ink/[0.05] text-ink-mute',
  };
  return (
    <span
      className={`inline-flex w-[52px] shrink-0 justify-center rounded border px-1.5 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.08em] ${styles[verdict]}`}
    >
      {verdict}
    </span>
  );
}

export interface Column<T> {
  key: string;
  header: string;
  /** Right-align numeric columns so digits line up. */
  numeric?: boolean;
  width?: string;
  render: (row: T) => ReactNode;
}

export function MonoDataTable<T>({
  columns,
  rows,
  getKey,
  empty = 'No rows.',
  maxHeight,
}: {
  columns: Column<T>[];
  rows: T[];
  getKey: (row: T) => string;
  empty?: string;
  maxHeight?: number;
}) {
  const { root } = useAnimeScope(
    (self, host) => {
      const { reduceMotion } = self.matches;
      animate('[data-tr]', {
        opacity: [0, 1],
        translateY: reduceMotion ? 0 : [8, 0],
        duration: reduceMotion ? 1 : 420,
        // Capped so a 500-row table does not schedule a two-minute cascade.
        delay: stagger(reduceMotion ? 0 : 14, { from: 0 }),
        ease: BRAND_EASE,
        autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
      });
    },
    [rows.length],
  );

  if (rows.length === 0) {
    return <p className="px-1 py-6 text-center font-mono text-[11px] text-ink-mute">{empty}</p>;
  }

  return (
    <div
      ref={root}
      className="overflow-auto hide-scrollbar"
      style={maxHeight ? { maxHeight } : undefined}
    >
      <table className="w-full border-collapse text-left font-mono text-[11.5px]">
        <thead className="sticky top-0 z-10 bg-[rgba(14,11,8,0.96)]">
          <tr className="border-b border-hairline-hi">
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                style={col.width ? { width: col.width } : undefined}
                className={`whitespace-nowrap px-2.5 py-2 text-[9.5px] font-normal uppercase tracking-[0.14em] text-ink-mute ${
                  col.numeric ? 'text-right' : ''
                }`}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={getKey(row)}
              data-tr
              className="reveal-init border-b border-hairline/60 hover:bg-ink/[0.04]"
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={`whitespace-nowrap px-2.5 py-2 align-middle text-ink-dim ${
                    col.numeric ? 'text-right tabular-nums' : ''
                  }`}
                >
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
