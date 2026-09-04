'use client';

import Link from 'next/link';
import { MonoDataTable, type Column } from './primitives';
import { rupees, shortTime } from '@/lib/format';
import type { AuditEntry, BatchSummary } from '@/lib/types';

/**
 * Column definitions live in a client module because `render` is a function.
 * The history page stays a server component and passes plain data.
 */

function Delta({ a, b, lowerIsBetter = false }: { a: number; b: number; lowerIsBetter?: boolean }) {
  if (a === b) return <span className="text-ink-mute">=</span>;
  const bWins = lowerIsBetter ? b < a : b > a;
  return (
    <span className={bWins ? 'text-amber' : 'text-[var(--color-verdict-pass)]'}>
      {bWins ? 'B' : 'A'}
    </span>
  );
}

export function BatchHistoryTable({ rows }: { rows: BatchSummary[] }) {
  const columns: Column<BatchSummary>[] = [
    {
      key: 'batch',
      header: 'Batch',
      render: (r) => (
        <Link
          href={`/console/compare?batch=${encodeURIComponent(r.batchId)}`}
          className="text-ink hover:text-amber hover:underline"
        >
          {r.batchId}
        </Link>
      ),
    },
    {
      key: 'when',
      header: 'Run at',
      render: (r) => <span className="text-ink-mute">{shortTime(r.createdAt)}</span>,
    },
    {
      key: 'source',
      header: 'Source',
      render: (r) =>
        r.source === 'live' ? (
          <span className="text-[var(--color-verdict-pass)]">live</span>
        ) : (
          <span className="text-amber">sample</span>
        ),
    },
    { key: 'events', header: 'Events', numeric: true, render: (r) => r.eventCount },
    {
      key: 'contacts',
      header: 'Contacts A / B',
      numeric: true,
      render: (r) => (
        <>
          <span className="text-ink-mute">{r.agents.A.contactsMade}</span>
          <span className="text-ink-mute"> / </span>
          <span className="text-amber">{r.agents.B.contactsMade}</span>
        </>
      ),
    },
    {
      key: 'wasted',
      header: 'Changed nothing A / B',
      numeric: true,
      render: (r) => (
        <>
          <span className="text-ink-mute">{r.agents.A.wastedContacts}</span>
          <span className="text-ink-mute"> / </span>
          <span className="text-amber">{r.agents.B.wastedContacts}</span>
        </>
      ),
    },
    {
      key: 'dogs',
      header: 'Dogs touched A / B',
      numeric: true,
      render: (r) => (
        <>
          <span className="text-ink-mute">{r.agents.A.sleepingDogsTouched}</span>
          <span className="text-ink-mute"> / </span>
          <span className="text-amber">{r.agents.B.sleepingDogsTouched}</span>
        </>
      ),
    },
    {
      key: 'net',
      header: 'Net value B',
      numeric: true,
      render: (r) => (
        <span className={r.agents.B.netValuePaise >= r.agents.A.netValuePaise ? 'text-amber' : 'text-ink-dim'}>
          {rupees(r.agents.B.netValuePaise)}
        </span>
      ),
    },
    {
      key: 'winner',
      header: 'Net',
      render: (r) => <Delta a={r.agents.A.netValuePaise} b={r.agents.B.netValuePaise} />,
    },
    {
      key: 'ledgers',
      header: 'Ledgers',
      render: (r) => (
        <span className="flex gap-2.5">
          <Link
            href={`/console/sleeping-dogs?batch=${encodeURIComponent(r.batchId)}`}
            className="text-ink-dim hover:text-amber hover:underline"
          >
            {r.sleepingDogs} no-action
          </Link>
          <Link
            href={`/console/exceptions?batch=${encodeURIComponent(r.batchId)}`}
            className="text-ink-dim hover:text-amber hover:underline"
          >
            {r.exceptions} escalated
          </Link>
        </span>
      ),
    },
  ];

  return (
    <MonoDataTable
      columns={columns}
      rows={rows}
      getKey={(r) => r.batchId}
      maxHeight={520}
      empty="No batches on record yet. Run one from the Batch tab."
    />
  );
}

const KIND_TONE: Record<string, string> = {
  'batch.started': 'text-ink-mute',
  'batch.completed': 'text-amber',
  'batch.imported': 'text-brass',
  decision: 'text-ink-dim',
};

export function AuditTailTable({ rows }: { rows: AuditEntry[] }) {
  const columns: Column<AuditEntry>[] = [
    { key: 'seq', header: '#', numeric: true, render: (r) => <span className="text-ink-mute">{r.seq}</span> },
    { key: 'at', header: 'At', render: (r) => <span className="text-ink-mute">{shortTime(r.at)}</span> },
    {
      key: 'kind',
      header: 'Kind',
      render: (r) => <span className={KIND_TONE[r.kind] ?? 'text-ink-dim'}>{r.kind}</span>,
    },
    { key: 'actor', header: 'Actor', render: (r) => <span className="text-brass">{r.actor}</span> },
    {
      key: 'ref',
      header: 'Ref',
      render: (r) =>
        r.ref?.startsWith('evt_') ? (
          <Link href={`/console/trace/${r.ref}`} className="text-ink hover:text-amber hover:underline">
            {r.ref}
          </Link>
        ) : (
          <span className="text-ink-dim">{r.ref ?? '—'}</span>
        ),
    },
    {
      key: 'what',
      header: 'Record',
      render: (r) => {
        const p = r.payload as Record<string, unknown>;
        if (r.kind === 'decision') {
          const blocked = p.blockedBy as string | null;
          return (
            <span className="text-ink-dim">
              {String(p.action)}
              {blocked && <span className="text-[var(--color-verdict-block)]"> · {blocked}</span>}
            </span>
          );
        }
        if (r.kind === 'batch.completed') {
          const b = (p.agents as { B?: { netValuePaise?: number } } | undefined)?.B;
          return (
            <span className="text-ink-dim">
              {String(p.eventCount)} events · net {b?.netValuePaise != null ? rupees(b.netValuePaise) : '—'}
            </span>
          );
        }
        return <span className="text-ink-mute">{Object.keys(p).slice(0, 3).join(', ')}</span>;
      },
    },
    {
      key: 'hash',
      header: 'Hash',
      render: (r) => <span className="text-ink-mute">{r.hash.slice(0, 12)}…</span>,
    },
  ];

  return (
    <MonoDataTable
      columns={columns}
      rows={rows}
      getKey={(r) => String(r.seq)}
      maxHeight={440}
      empty="The audit log is empty."
    />
  );
}
