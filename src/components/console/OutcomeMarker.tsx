'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { markOutcome } from '@/lib/api';

/**
 * Operator attribution for a real leak whose outcome Razorpay has not
 * reported. Written to the ledger as `manual:<actor>` — it is never dressed
 * up as a webhook, and it feeds the same learning loop.
 */
export function OutcomeMarker({ eventId }: { eventId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mark = async (recovered: boolean, churned: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await markOutcome(eventId, recovered, churned, 'marked from the trace view');
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record the outcome.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 border-t border-hairline pt-3">
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-mute">
        Record the outcome by hand
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => mark(true, false)}
          className="rounded-full border border-[var(--color-verdict-pass)]/50 px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--color-verdict-pass)] hover:bg-[var(--color-verdict-pass)]/10 disabled:opacity-45"
        >
          Recovered
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => mark(false, true)}
          className="rounded-full border border-[var(--color-verdict-block)]/50 px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--color-verdict-block)] hover:bg-[var(--color-verdict-block)]/10 disabled:opacity-45"
        >
          Cancelled
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => mark(false, false)}
          className="rounded-full border border-hairline-hi px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-ink-dim hover:bg-ink/[0.06] disabled:opacity-45"
        >
          Not recovered
        </button>
      </div>
      <p className="mt-2 text-[11px] leading-snug text-ink-mute">
        Labelled <span className="font-mono">manual</span> in the ledger. Razorpay-reported outcomes
        arrive through sync or webhooks and take the same path.
      </p>
      {error && <p className="mt-2 font-mono text-[11px] text-[var(--color-verdict-block)]">{error}</p>}
    </div>
  );
}
