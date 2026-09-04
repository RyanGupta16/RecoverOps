'use client';

import Link from 'next/link';
import { MonoDataTable, type Column } from './primitives';
import { rupees, SEGMENT_LABELS, shortTime, signed } from '@/lib/format';
import type { ExceptionRecord, SleepingDogRecord } from '@/lib/types';

export function SleepingDogTable({ rows }: { rows: SleepingDogRecord[] }) {
  const columns: Column<SleepingDogRecord>[] = [
    {
      key: 'event',
      header: 'Event',
      render: (r) => (
        <Link
          href={`/console/trace/${r.eventId}`}
          className="text-ink hover:text-amber hover:underline"
        >
          {r.eventId}
        </Link>
      ),
    },
    {
      key: 'reason',
      header: 'Reason',
      render: (r) => <span className="text-brass">{r.reasonCode}</span>,
    },
    { key: 'amount', header: 'Amount', numeric: true, render: (r) => rupees(r.amountPaise) },
    {
      key: 'uplift',
      header: 'Est. uplift',
      numeric: true,
      render: (r) => (
        <span className={r.upliftHat <= 0 ? 'text-[var(--color-verdict-block)]' : 'text-ink-dim'}>
          {signed(r.upliftHat)}
        </span>
      ),
    },
    {
      key: 'rule',
      header: 'Rule that held',
      render: (r) => <span className="text-ink-mute">{r.blockedBy}</span>,
    },
    {
      key: 'baseline',
      header: 'Baseline would',
      render: (r) =>
        r.baselineWouldContact ? (
          <span className="text-[var(--color-verdict-block)]">contact</span>
        ) : (
          <span className="text-ink-mute">also skip</span>
        ),
    },
    {
      key: 'truth',
      header: 'True segment',
      render: (r) => (
        <span className={r.truthSegment === 'sleeping_dog' ? 'text-amber' : 'text-ink-mute'}>
          {SEGMENT_LABELS[r.truthSegment]}
        </span>
      ),
    },
    {
      key: 'damage',
      header: 'Est. damage avoided',
      numeric: true,
      render: (r) =>
        r.estimatedDamageAvoidedPaise > 0 ? (
          <span className="text-amber">{rupees(r.estimatedDamageAvoidedPaise)}</span>
        ) : (
          <span className="text-ink-mute">—</span>
        ),
    },
  ];

  return (
    <MonoDataTable
      columns={columns}
      rows={rows}
      getKey={(r) => r.eventId}
      maxHeight={620}
      empty="No no-action decisions in this batch."
    />
  );
}

export function ExceptionTable({ rows }: { rows: ExceptionRecord[] }) {
  const columns: Column<ExceptionRecord>[] = [
    {
      key: 'event',
      header: 'Event',
      render: (r) => (
        <Link
          href={`/console/trace/${r.eventId}`}
          className="text-ink hover:text-amber hover:underline"
        >
          {r.eventId}
        </Link>
      ),
    },
    {
      key: 'raised',
      header: 'Raised',
      render: (r) => <span className="text-ink-mute">{shortTime(r.raisedAt)}</span>,
    },
    { key: 'amount', header: 'Amount', numeric: true, render: (r) => rupees(r.amountPaise) },
    {
      key: 'reason',
      header: 'Reason',
      render: (r) => <span className="text-brass">{r.reasonCode}</span>,
    },
    {
      key: 'denied',
      header: 'Action denied',
      render: (r) => <span className="text-ink-dim">{r.deniedAction}</span>,
    },
    {
      key: 'rule',
      header: 'Blocked by',
      render: (r) => <span className="text-[var(--color-verdict-block)]">{r.blockedBy}</span>,
    },
    {
      key: 'why',
      header: 'Structured reason',
      render: (r) => (
        <span className="whitespace-normal text-[11px] text-ink-mute">{r.structuredReason}</span>
      ),
    },
  ];

  return (
    <MonoDataTable
      columns={columns}
      rows={rows}
      getKey={(r) => r.eventId}
      maxHeight={620}
      empty="Nothing unresolved in this batch."
    />
  );
}
