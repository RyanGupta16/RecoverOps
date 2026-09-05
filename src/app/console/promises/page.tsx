import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { PromiseTable } from '@/components/console/PromiseTable';
import { TerminalPanel } from '@/components/console/primitives';
import { DemoModeBadge } from '@/components/ui/primitives';
import { fetchPromises } from '@/lib/api';
import { percent, rupees } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function PromisesPage() {
  const view = await fetchPromises();
  const s = view?.stats;

  return (
    <>
      <ConsoleHeading
        title="Promises to pay"
        sub="The strongest stopping rule there is. While a promise is live, nothing happens on that counterparty — not outreach, not a silent retry. The agreed date is the agreement."
        aside={<DemoModeBadge source={view ? 'live' : 'sample'} />}
      />

      {view && s ? (
        <>
          <div className="mb-4 grid gap-4 lg:grid-cols-4">
            <TerminalPanel title="Live promises" meta="holding everything">
              <p className="font-mono text-[28px] tabular-nums text-amber">{s.open}</p>
              <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
                {s.total} on record. A reminder goes out the day before; a promise is not called broken until{' '}
                {s.brokenAfterDays} days past its date.
              </p>
            </TerminalPanel>

            <TerminalPanel title="Kept rate" meta="of resolved promises">
              <p
                className={`font-mono text-[28px] tabular-nums ${
                  s.keptRate === null ? 'text-ink-mute' : s.keptRate >= 0.7 ? 'text-[var(--color-verdict-pass)]' : 'text-[var(--color-verdict-block)]'
                }`}
              >
                {s.keptRate === null ? '—' : percent(s.keptRate)}
              </p>
              <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
                Kept means Razorpay reported the money, not that the customer said so.
              </p>
            </TerminalPanel>

            <TerminalPanel title="Promised" meta="value under promise">
              <p className="font-mono text-[28px] tabular-nums text-ink">{rupees(s.promisedPaise)}</p>
              <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">
                {rupees(s.paidPaise)} of it has actually arrived.
              </p>
            </TerminalPanel>

            <TerminalPanel title="By capture channel" meta="kept / total">
              {Object.keys(s.byChannel).length === 0 ? (
                <p className="text-[11.5px] text-ink-mute">No promises captured yet.</p>
              ) : (
                <ul className="flex flex-col gap-1.5 font-mono text-[11.5px]">
                  {Object.entries(s.byChannel).map(([ch, v]) => (
                    <li key={ch} className="flex items-baseline justify-between gap-3">
                      <span className="text-brass">{ch}</span>
                      <span className="text-ink-dim">
                        {v.kept}/{v.total}
                        {v.broken > 0 && <span className="text-[var(--color-verdict-block)]"> · {v.broken} broken</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </TerminalPanel>
          </div>

          <TerminalPanel title="The book" meta={`${view.promises.length} rows · newest due first`}>
            <PromiseTable rows={view.promises} />
            <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
              The state machine is collections practice, not invention: a reminder the day before, a break
              declared {s.brokenAfterDays} days past the date, a recontact window of {s.recontactWithinHours} hours, and a
              second break escalated from collections to a risk decision rather than chased again. A promise
              is verified only by a payment webhook — never by what the customer said on the call.
            </p>
          </TerminalPanel>
        </>
      ) : (
        <TerminalPanel title="Promises" meta="backend only">
          <p className="text-[12px] leading-relaxed text-ink-dim">
            The promise book lives in the backend ledger, and holds every other action while a promise is
            live. Start the backend to see it.
          </p>
        </TerminalPanel>
      )}
    </>
  );
}
