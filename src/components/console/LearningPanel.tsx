'use client';

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { retrainLearner, syncOutcomes } from '@/lib/api';
import { percent, rupees, shortTime } from '@/lib/format';
import type { LearningStatus, SyncReport } from '@/lib/types';
import { TerminalPanel } from './primitives';

function Big({ children, tone = 'ink' }: { children: React.ReactNode; tone?: 'ink' | 'amber' | 'mute' | 'pass' | 'block' }) {
  const cls = {
    ink: 'text-ink',
    amber: 'text-amber',
    mute: 'text-ink-mute',
    pass: 'text-[var(--color-verdict-pass)]',
    block: 'text-[var(--color-verdict-block)]',
  }[tone];
  return <p className={`font-mono text-[28px] tabular-nums ${cls}`}>{children}</p>;
}

export function LearningPanel({ status, live }: { status: LearningStatus; live: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState<'sync' | 'retrain' | null>(null);
  const [report, setReport] = useState<SyncReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async (kind: 'sync' | 'retrain') => {
      setBusy(kind);
      setError(null);
      try {
        if (kind === 'sync') setReport(await syncOutcomes());
        else await retrainLearner();
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Request failed.');
      } finally {
        setBusy(null);
      }
    },
    [router],
  );

  const c = status.counts;
  const eff = status.policyEffect;
  const learned = status.estimatorMode === 'learned-real';

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-4">
        <TerminalPanel title="Real leaks on record" meta="all batches">
          <Big>{c.real}</Big>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            {c.pending} outcome{c.pending === 1 ? '' : 's'} pending · {c.resolved} resolved.
            {c.synthetic > 0 && ` ${c.synthetic} synthetic rows are kept apart and never trained on.`}
          </p>
        </TerminalPanel>

        <TerminalPanel title="Control arm" meta="never contacted">
          <Big tone="amber">{c.control}</Big>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            Hashed per counterparty, so a customer stays in one arm across batches. Both agents
            take the silent path on them.
          </p>
        </TerminalPanel>

        <TerminalPanel title="Explored decisions" meta="treatment arm">
          <Big>{c.explored}</Big>
          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
            Contact decisions Agent B flipped at random so every contactable leak has a known
            propensity — the price of learning uplift without confounding.
          </p>
        </TerminalPanel>

        <TerminalPanel title="Estimator on real data" meta={learned ? 'learned' : 'priors'}>
          <Big tone={learned ? 'pass' : 'mute'}>{learned ? 'learned' : 'priors'}</Big>
          <p className="mt-1.5 break-words text-[11.5px] leading-snug text-ink-mute">{status.estimator}</p>
        </TerminalPanel>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <TerminalPanel
          title="Measured policy effect"
          meta={eff.measurable ? `${eff.treatmentRows} treatment · ${eff.controlRows} control` : 'not yet measurable'}
        >
          {eff.measurable && eff.ateRate !== null && eff.ateRateCi && eff.incrementalPaiseCi ? (
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-mute">Recovery, treatment</p>
                  <Big>{percent(eff.rateTreatment ?? 0)}</Big>
                </div>
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-mute">Recovery, control</p>
                  <Big tone="mute">{percent(eff.rateControl ?? 0)}</Big>
                </div>
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-mute">Difference</p>
                  <Big tone={eff.ateRateCi[0] > 0 ? 'amber' : eff.ateRateCi[1] < 0 ? 'block' : 'ink'}>
                    {eff.ateRate >= 0 ? '+' : ''}
                    {percent(eff.ateRate)}
                  </Big>
                  <p className="font-mono text-[10.5px] text-ink-mute">
                    95% CI {eff.ateRateCi[0] >= 0 ? '+' : ''}
                    {percent(eff.ateRateCi[0])} to {eff.ateRateCi[1] >= 0 ? '+' : ''}
                    {percent(eff.ateRateCi[1])}
                  </p>
                </div>
              </div>
              <div className="mt-4 border-t border-hairline pt-3">
                <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-mute">
                  Incremental recovery, measured
                </p>
                <p className="font-mono text-[22px] tabular-nums text-amber">
                  {rupees(eff.incrementalPaise ?? 0)}
                  <span className="ml-3 text-[11px] text-ink-mute">
                    CI {rupees(eff.incrementalPaiseCi[0])} to {rupees(eff.incrementalPaiseCi[1])}
                  </span>
                </p>
                <p className="mt-2 text-[11.5px] leading-relaxed text-ink-mute">{eff.note}</p>
              </div>
            </>
          ) : (
            <p className="text-[12px] leading-relaxed text-ink-dim">
              {eff.note ??
                'Both arms need resolved outcomes before the policy effect can be measured. Until then every money figure on real batches is an estimate on priors, and says so.'}
            </p>
          )}
        </TerminalPanel>

        <div className="flex flex-col gap-4">
          <TerminalPanel
            title="Close the loop"
            meta={live ? 'Razorpay keys configured' : 'no Razorpay keys'}
            actions={
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => run('sync')}
                  disabled={busy !== null}
                  className="rounded-full border border-hairline-hi px-3.5 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.12em] text-ink hover:bg-ink/[0.06] disabled:opacity-45"
                >
                  {busy === 'sync' ? 'Syncing…' : 'Sync outcomes'}
                </button>
                <button
                  type="button"
                  onClick={() => run('retrain')}
                  disabled={busy !== null}
                  className="rounded-full bg-amber px-3.5 py-1.5 font-mono text-[10.5px] font-semibold uppercase tracking-[0.12em] text-deep disabled:opacity-45"
                >
                  {busy === 'retrain' ? 'Fitting…' : 'Retrain'}
                </button>
              </div>
            }
          >
            <p className="text-[11.5px] leading-relaxed text-ink-dim">
              Sync polls Razorpay for every pending real leak — the payment link it created, the
              retry order, the subscription&apos;s state — and attributes what it finds. With keys
              configured this also runs every ten minutes. Retrain refits the real-data estimator;
              it is used only once it beats random on a real holdout.
            </p>
            {report && (
              <p className="mt-3 border-t border-hairline pt-3 font-mono text-[11px] text-ink-mute">
                checked {report.checked} · recovered {report.recovered} · churned {report.churned} · stale{' '}
                {report.stale} · still pending {report.stillPending}
                {!report.live && ' · no keys — nothing could be learned from Razorpay'}
                {report.errors.length > 0 && ` · ${report.errors.length} error(s)`}
              </p>
            )}
            {error && <p className="mt-3 font-mono text-[11px] text-[var(--color-verdict-block)]">{error}</p>}
          </TerminalPanel>

          <TerminalPanel title="Last fit" meta={status.lastRun?.at ? shortTime(status.lastRun.at) : 'never'}>
            {status.lastRun ? (
              <dl className="grid grid-cols-[130px_1fr] gap-x-4 gap-y-1.5 font-mono text-[11.5px]">
                <dt className="text-ink-mute">rows_used</dt>
                <dd className="text-ink-dim">
                  {status.lastRun.rowsUsed} ({status.lastRun.treatedRows} contacted / {status.lastRun.controlRows} not)
                </dd>
                <dt className="text-ink-mute">qini_real</dt>
                <dd className={status.lastRun.qiniReal && status.lastRun.qiniReal > 0 ? 'text-[var(--color-verdict-pass)]' : 'text-ink-dim'}>
                  {status.lastRun.qiniReal === null ? '—' : status.lastRun.qiniReal.toFixed(4)}
                </dd>
                <dt className="text-ink-mute">ready</dt>
                <dd className={status.lastRun.ready ? 'text-[var(--color-verdict-pass)]' : 'text-ink-dim'}>
                  {status.lastRun.ready ? 'yes — in use on real batches' : 'no — priors stay in use'}
                </dd>
                <dt className="text-ink-mute">thresholds</dt>
                <dd className="text-ink-dim">
                  ≥ {status.thresholds.minRows} rows, ≥ {status.thresholds.minPerArm} per arm
                </dd>
              </dl>
            ) : (
              <p className="text-[11.5px] leading-relaxed text-ink-mute">
                No fit yet. Thresholds: at least {status.thresholds.minRows} resolved treatment-arm rows
                with {status.thresholds.minPerArm} contacted and {status.thresholds.minPerArm} not.
              </p>
            )}
            {status.lastRun?.note && (
              <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
                {status.lastRun.note}
              </p>
            )}
          </TerminalPanel>
        </div>
      </div>
    </div>
  );
}
