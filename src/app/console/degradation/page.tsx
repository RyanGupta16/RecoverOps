import { CohortTable } from '@/components/console/DegradationView';
import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { TerminalPanel } from '@/components/console/primitives';
import { DemoModeBadge } from '@/components/ui/primitives';
import { fetchDegradation } from '@/lib/api';

export const dynamic = 'force-dynamic';

export default async function DegradationPage() {
  const view = await fetchDegradation();
  const live = view?.cohorts.filter((c) => !c.endedAt) ?? [];
  const byRazorpay = live.filter((c) => c.source === 'razorpay').length;
  const byDetector = live.filter((c) => c.source === 'detector').length;
  const history = view?.history ?? [];
  const totalHeld = history.reduce((s, c) => s + (c.eventsHeld ?? 0), 0);

  return (
    <>
      <ConsoleHeading
        title="Degradation"
        sub="The one direction where the customer is never the answer. When an issuer stops working, every message about it is spend that also blames the customer for a bank's problem — so the whole cohort is held until it clears."
        aside={<DemoModeBadge source={view ? 'live' : 'sample'} />}
      />

      {view ? (
        <>
          <div className="mb-4 grid gap-4 lg:grid-cols-4">
            <TerminalPanel title="Live cohorts" meta="holding contact">
              <p className="font-mono text-[28px] tabular-nums text-[var(--color-verdict-block)]">{live.length}</p>
              <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
                {byRazorpay} declared by Razorpay, {byDetector} found by our detector.
              </p>
            </TerminalPanel>

            <TerminalPanel title="Downtime feed" meta="Razorpay">
              <p
                className={`font-mono text-[28px] tabular-nums ${
                  view.feedAvailable ? 'text-[var(--color-verdict-pass)]' : 'text-ink-mute'
                }`}
              >
                {view.feedAvailable ? 'live' : 'off'}
              </p>
              <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
                {view.feedError
                  ? `Last read failed: ${view.feedError}`
                  : view.feedAvailable
                    ? 'GET /v1/payments/downtimes, read on every batch and cached for a minute.'
                    : 'Set Razorpay keys in backend/.env to read the acquirer’s own view.'}
              </p>
            </TerminalPanel>

            <TerminalPanel title="Events held" meta="all batches on record">
              <p className="font-mono text-[28px] tabular-nums text-amber">{totalHeld}</p>
              <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
                Customer-facing actions stopped because the instrument was down. Each one is a message not
                sent and a customer not blamed for someone else&apos;s outage.
              </p>
            </TerminalPanel>

            <TerminalPanel title="Detector" meta="EWMA + CUSUM">
              <p className="font-mono text-[15px] leading-snug text-ink-dim">success rate per instrument</p>
              <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
                Five-minute buckets, a minimum attempt count before any claim, and a present-condition check
                so historical drift alone never declares an outage.
              </p>
            </TerminalPanel>
          </div>

          <TerminalPanel title="Live cohorts" meta={`${live.length} holding`}>
            <CohortTable rows={live} />
            <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
              {view.detectorNote}
            </p>
          </TerminalPanel>

          {history.length > 0 && (
            <div className="mt-4">
              <TerminalPanel title="Seen before" meta={`${history.length} cohorts across all batches`}>
                <CohortTable rows={history} />
                <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
                  A cohort releases itself: when Razorpay marks the downtime resolved, or the success rate
                  recovers, the hold lifts and the held leaks become eligible again on the next batch.
                </p>
              </TerminalPanel>
            </div>
          )}
        </>
      ) : (
        <TerminalPanel title="Degradation" meta="backend only">
          <p className="text-[12px] leading-relaxed text-ink-dim">
            Cohorts come from Razorpay&apos;s downtime feed and from a changepoint detector over the payment
            stream. Start the backend to see them.
          </p>
        </TerminalPanel>
      )}
    </>
  );
}
