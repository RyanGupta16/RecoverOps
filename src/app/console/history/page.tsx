import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { AuditTailTable, BatchHistoryTable } from '@/components/console/HistoryTables';
import { TerminalPanel } from '@/components/console/primitives';
import { DemoModeBadge } from '@/components/ui/primitives';
import { loadAudit, loadHistory } from '@/lib/batch.server';
import { rupees } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function HistoryPage() {
  const [{ rows, source }, audit] = await Promise.all([loadHistory(), loadAudit()]);

  const live = rows.filter((r) => r.source === 'live');
  const netB = live.reduce((s, r) => s + r.agents.B.netValuePaise, 0);
  const netA = live.reduce((s, r) => s + r.agents.A.netValuePaise, 0);
  const dogsSpared = live.reduce(
    (s, r) => s + Math.max(0, r.agents.A.sleepingDogsTouched - r.agents.B.sleepingDogsTouched),
    0,
  );

  return (
    <>
      <ConsoleHeading
        title="Batch history and audit"
        sub="Every batch this backend has run, in the order it ran them, and the append-only ledger underneath. A restart changes nothing here — that is the point of a ledger."
        aside={<DemoModeBadge source={source} />}
      />

      <div className="mb-4 grid gap-4 lg:grid-cols-4">
        <TerminalPanel title="Batches on record" meta="this ledger">
          <p className="font-mono text-[28px] tabular-nums text-ink">{rows.length}</p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            {live.length === rows.length
              ? 'All from live runs.'
              : source === 'sample'
                ? 'No backend reachable — the bundled batch is the only row.'
                : `${live.length} live, ${rows.length - live.length} sample.`}
          </p>
        </TerminalPanel>

        <TerminalPanel title="Net value, all live runs" meta="RecoverOps vs baseline">
          <p className="font-mono text-[28px] tabular-nums text-amber">{rupees(netB)}</p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            Baseline over the same batches: {rupees(netA)}. Incremental, net of message cost and
            churn the outreach caused.
          </p>
        </TerminalPanel>

        <TerminalPanel title="Sleeping dogs spared" meta="baseline touched, RecoverOps did not">
          <p className="font-mono text-[28px] tabular-nums text-ink">{dogsSpared}</p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            Summed per batch. Graded against ground truth, which only a synthetic batch has.
          </p>
        </TerminalPanel>

        <TerminalPanel title="Audit chain" meta={audit ? `${audit.total} rows` : 'backend only'}>
          {audit ? (
            <>
              <p
                className={`font-mono text-[28px] tabular-nums ${
                  audit.verify.ok ? 'text-[var(--color-verdict-pass)]' : 'text-[var(--color-verdict-block)]'
                }`}
              >
                {audit.verify.ok ? 'intact' : `broken @${audit.verify.firstBreak}`}
              </p>
              <p className="mt-1.5 break-all font-mono text-[10.5px] leading-snug text-ink-mute">
                head {audit.verify.head.slice(0, 20)}… — every row hashes the one before it; this
                was recomputed from genesis on this request.
              </p>
            </>
          ) : (
            <p className="text-[11.5px] leading-snug text-ink-mute">
              The hash-chained audit log lives in the backend ledger. Start the backend to verify
              it here.
            </p>
          )}
        </TerminalPanel>
      </div>

      <TerminalPanel title="Batches" meta={`${rows.length} rows · newest first`}>
        <BatchHistoryTable rows={rows} />
        <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
          Open any batch&apos;s comparison, no-action ledger or exception queue as it was when it ran.
          Trace links inside them resolve against the same ledger, so nothing here goes stale.
        </p>
      </TerminalPanel>

      {audit && (
        <div className="mt-4">
          <TerminalPanel title="Audit log" meta={`latest ${audit.tail.length} of ${audit.total}`}>
            <AuditTailTable rows={audit.tail} />
            <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
              One <span className="font-mono text-ink-dim">decision</span> row per event for the
              acting agent — action, message class, every rule verdict, the execution record and the
              outcome — plus a <span className="font-mono text-amber">batch.completed</span> row
              carrying the summary. A blocked action leaves the same trail as an executed one.
            </p>
          </TerminalPanel>
        </div>
      )}
    </>
  );
}
