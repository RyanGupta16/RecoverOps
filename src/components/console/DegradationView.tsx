'use client';

import { MonoDataTable, type Column } from './primitives';
import { percent, shortTime } from '@/lib/format';
import type { Cohort } from '@/lib/types';

function Severity({ level }: { level: string }) {
  const tone =
    level === 'high'
      ? 'border-[var(--color-verdict-block)]/50 text-[var(--color-verdict-block)]'
      : level === 'medium'
        ? 'border-brass/50 text-brass'
        : 'border-hairline text-ink-mute';
  return (
    <span className={`inline-flex rounded border px-1.5 py-0.5 text-[9.5px] uppercase tracking-[0.08em] ${tone}`}>
      {level}
    </span>
  );
}

function Source({ source }: { source: string }) {
  return source === 'razorpay' ? (
    <span className="text-amber" title="Declared by Razorpay's payment downtime feed">
      razorpay
    </span>
  ) : (
    <span className="text-brass" title="Found by our own changepoint detector on the success rate">
      detector
    </span>
  );
}

export function CohortTable({ rows, held }: { rows: Cohort[]; held?: Record<string, number> }) {
  const columns: Column<Cohort>[] = [
    { key: 'key', header: 'Cohort', render: (r) => <span className="text-ink">{r.key}</span> },
    { key: 'source', header: 'Source', render: (r) => <Source source={r.source} /> },
    { key: 'sev', header: 'Severity', render: (r) => <Severity level={r.severity} /> },
    {
      key: 'status',
      header: 'Status',
      render: (r) =>
        r.endedAt ? (
          <span className="text-ink-mute">resolved</span>
        ) : (
          <span className="text-[var(--color-verdict-block)]">{r.status}</span>
        ),
    },
    { key: 'began', header: 'Began', render: (r) => <span className="text-ink-mute">{r.beganAt ? shortTime(r.beganAt) : '—'}</span> },
    {
      key: 'rate',
      header: 'Success rate',
      numeric: true,
      render: (r) =>
        r.successRate === null ? (
          <span className="text-ink-mute" title="Razorpay declares the outage; it does not publish a rate.">
            —
          </span>
        ) : (
          <span>
            <span className="text-[var(--color-verdict-block)]">{percent(r.successRate)}</span>
            <span className="text-ink-mute"> vs {percent(r.baselineRate ?? 0)}</span>
          </span>
        ),
    },
    {
      key: 'held',
      header: 'Events held',
      numeric: true,
      render: (r) => {
        const n = r.eventsHeld ?? held?.[r.key] ?? 0;
        return n > 0 ? <span className="text-amber">{n}</span> : <span className="text-ink-mute">0</span>;
      },
    },
    {
      key: 'detail',
      header: 'What it says',
      render: (r) => <span className="whitespace-normal text-[11px] text-ink-mute">{r.detail}</span>,
    },
  ];

  return (
    <MonoDataTable
      columns={columns}
      rows={rows}
      getKey={(r) => `${r.key}:${r.source}:${r.beganAt}`}
      maxHeight={520}
      empty="No degradation cohorts on record."
    />
  );
}
