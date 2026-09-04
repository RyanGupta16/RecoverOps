'use client';

import { MonoDataTable, type Column } from './primitives';
import { SEGMENT_LABELS } from '@/lib/format';
import type { SegmentRow } from '@/lib/types';

/**
 * Column definitions live in a client module because `render` is a function,
 * and functions cannot be serialised across the server/client boundary. The
 * pages stay server components and pass plain data.
 */

export interface MetricRow {
  key: string;
  metric: string;
  a: string;
  b: string;
  winner: 'a' | 'b' | null;
  note: string;
}

export function MetricTable({ rows }: { rows: MetricRow[] }) {
  const columns: Column<MetricRow>[] = [
    {
      key: 'metric',
      header: 'Metric',
      render: (r) => <span className="text-ink">{r.metric}</span>,
    },
    {
      key: 'a',
      header: 'Baseline',
      numeric: true,
      render: (r) => (
        <span className={r.winner === 'a' ? 'text-[var(--color-verdict-pass)]' : 'text-ink-dim'}>
          {r.a}
        </span>
      ),
    },
    {
      key: 'b',
      header: 'RecoverOps',
      numeric: true,
      render: (r) => (
        <span className={r.winner === 'b' ? 'text-amber' : 'text-ink-dim'}>{r.b}</span>
      ),
    },
    {
      key: 'note',
      header: 'Reading',
      render: (r) => <span className="whitespace-normal text-[11px] text-ink-mute">{r.note}</span>,
    },
  ];

  return <MonoDataTable columns={columns} rows={rows} getKey={(r) => r.key} />;
}

export type SegmentComparisonRow = SegmentRow & { baselineContacted: number };

export function SegmentTable({ rows }: { rows: SegmentComparisonRow[] }) {
  const columns: Column<SegmentComparisonRow>[] = [
    {
      key: 'segment',
      header: 'True segment',
      render: (r) => <span className="text-ink">{SEGMENT_LABELS[r.segment]}</span>,
    },
    { key: 'pop', header: 'In batch', numeric: true, render: (r) => r.population },
    {
      key: 'uplift',
      header: 'Mean true uplift',
      numeric: true,
      render: (r) => (
        <span className={r.trueUplift < 0 ? 'text-[var(--color-verdict-block)]' : 'text-ink-dim'}>
          {r.trueUplift >= 0 ? '+' : ''}
          {r.trueUplift.toFixed(3)}
        </span>
      ),
    },
    { key: 'a', header: 'Baseline contacted', numeric: true, render: (r) => r.baselineContacted },
    {
      key: 'b',
      header: 'RecoverOps contacted',
      numeric: true,
      render: (r) => <span className="text-amber">{r.contacted}</span>,
    },
  ];

  return <MonoDataTable columns={columns} rows={rows} getKey={(r) => r.segment} />;
}
