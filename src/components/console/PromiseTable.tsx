'use client';

import Link from 'next/link';
import { MonoDataTable, type Column } from './primitives';
import { rupees, shortTime } from '@/lib/format';
import type { PromiseRecord } from '@/lib/types';

const STATE_TONE: Record<string, string> = {
  open: 'text-amber',
  reminded: 'text-amber',
  recontacted: 'text-amber',
  kept: 'text-[var(--color-verdict-pass)]',
  partially_kept: 'text-[var(--color-verdict-pass)]',
  broken: 'text-[var(--color-verdict-block)]',
  second_broken: 'text-[var(--color-verdict-block)]',
  risk_escalated: 'text-[var(--color-verdict-block)]',
  cancelled: 'text-ink-mute',
};

export function PromiseTable({ rows }: { rows: PromiseRecord[] }) {
  const columns: Column<PromiseRecord>[] = [
    {
      key: 'who',
      header: 'Counterparty',
      render: (r) =>
        r.eventId ? (
          <Link href={`/console/trace/${r.eventId}`} className="text-ink hover:text-amber hover:underline">
            {r.counterpartyId}
          </Link>
        ) : (
          <span className="text-ink">{r.counterpartyId}</span>
        ),
    },
    { key: 'amount', header: 'Promised', numeric: true, render: (r) => rupees(r.amountPaise) },
    { key: 'due', header: 'Due', render: (r) => <span className="text-ink-dim">{shortTime(r.dueAt)}</span> },
    {
      key: 'state',
      header: 'State',
      render: (r) => (
        <span className={STATE_TONE[r.state] ?? 'text-ink-dim'}>
          {r.state.replace(/_/g, ' ')}
          {r.open && <span className="ml-1.5 text-[9.5px] uppercase tracking-[0.08em] text-ink-mute">holding</span>}
        </span>
      ),
    },
    { key: 'via', header: 'Captured via', render: (r) => <span className="text-brass">{r.capturedVia}</span> },
    {
      key: 'verified',
      header: 'Verified by',
      render: (r) =>
        r.verifiedBy ? (
          <span className="text-[var(--color-verdict-pass)]">{r.verifiedBy}</span>
        ) : (
          <span className="text-ink-mute">—</span>
        ),
    },
    {
      key: 'paid',
      header: 'Paid',
      numeric: true,
      render: (r) => (r.amountPaidPaise > 0 ? rupees(r.amountPaidPaise) : <span className="text-ink-mute">—</span>),
    },
    {
      key: 'broken',
      header: 'Breaks',
      numeric: true,
      render: (r) =>
        r.brokenCount > 0 ? <span className="text-[var(--color-verdict-block)]">{r.brokenCount}</span> : <span className="text-ink-mute">0</span>,
    },
    {
      key: 'said',
      header: 'What they said',
      render: (r) => <span className="whitespace-normal text-[11px] text-ink-mute">{r.verbatim || '—'}</span>,
    },
  ];

  return (
    <MonoDataTable
      columns={columns}
      rows={rows}
      getKey={(r) => String(r.promiseId)}
      maxHeight={520}
      empty="No promises on record. They are captured from a voice call, a reply, or an operator."
    />
  );
}
