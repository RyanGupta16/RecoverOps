'use client';

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { placeVoiceCall } from '@/lib/api';
import { rupees, shortTime } from '@/lib/format';
import type { VoiceCallResult, VoiceStatus } from '@/lib/types';
import { TerminalPanel } from './primitives';

const OUTCOME_TONE: Record<string, string> = {
  promise: 'text-[var(--color-verdict-pass)]',
  link_sent: 'text-[var(--color-verdict-pass)]',
  decline: 'text-[var(--color-verdict-block)]',
  dispute: 'text-[var(--color-verdict-block)]',
  callback: 'text-brass',
  no_answer: 'text-ink-mute',
  unclear: 'text-ink-mute',
};

/** base64 wav → a playable object URL, without trusting the string blindly. */
function audioSrc(b64: string): string | null {
  try {
    return `data:audio/wav;base64,${b64}`;
  } catch {
    return null;
  }
}

export function CallRoom({ eventId, status }: { eventId: string; status: VoiceStatus | null }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [call, setCall] = useState<VoiceCallResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const place = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await placeVoiceCall(eventId);
      setCall(res);
      if (res.recordedPromise) router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The call could not be placed.');
    } finally {
      setBusy(false);
    }
  }, [eventId, router]);

  return (
    <TerminalPanel
      title="Call room"
      meta={status ? (status.live ? `${status.ttsModel} · real audio` : 'script only — no Sarvam key') : 'voice'}
      actions={
        <button
          type="button"
          onClick={place}
          disabled={busy}
          className="rounded-full border border-hairline-hi px-3.5 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.12em] text-ink hover:bg-ink/[0.06] disabled:opacity-45"
        >
          {busy ? 'Calling…' : call ? 'Call again' : 'Place the call'}
        </button>
      }
    >
      {!call && (
        <p className="text-[11.5px] leading-relaxed text-ink-dim">
          A scripted Hinglish collections call over a simulated line. The dialogue is a fixed state machine,
          not a free-running model — what it may say is bounded by the same policy that decided to place it.
          {status && <> {status.note}</>}
        </p>
      )}

      {error && <p className="mt-2 font-mono text-[11px] text-[var(--color-verdict-block)]">{error}</p>}

      {call && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1.5 font-mono text-[11px]">
            <span>
              outcome <span className={OUTCOME_TONE[call.outcome] ?? 'text-ink-dim'}>{call.outcome.replace(/_/g, ' ')}</span>
            </span>
            <span className="text-ink-mute">{call.durationSeconds}s</span>
            <span className={call.audioLive ? 'text-[var(--color-verdict-pass)]' : 'text-ink-mute'}>
              {call.audioLive ? 'real audio' : 'no audio'}
            </span>
          </div>

          <ol className="flex flex-col gap-2">
            {call.turns.map((t, i) => (
              <li
                key={i}
                className={`rounded-[10px] border px-3 py-2 ${
                  t.speaker === 'agent'
                    ? 'border-amber/25 bg-amber/[0.06]'
                    : 'border-hairline bg-ink/[0.03]'
                }`}
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-mute">
                    {t.speaker === 'agent' ? 'RecoverOps' : 'Customer'}
                    {t.intent && <span className="ml-2 text-brass">intent: {t.intent}</span>}
                  </span>
                  {t.latencyMs > 0 && <span className="font-mono text-[9.5px] text-ink-mute">{t.latencyMs} ms</span>}
                </div>
                <p className="mt-1 text-[13px] leading-relaxed text-ink">{t.text || <em className="text-ink-mute">(silence)</em>}</p>
                {t.audioB64 ? (
                  <audio
                    controls
                    preload="none"
                    aria-label={`Spoken audio: ${t.text}`}
                    className="mt-2 h-8 w-full max-w-[380px]"
                    src={audioSrc(t.audioB64) ?? undefined}
                  />
                ) : t.speaker === 'agent' ? (
                  <p className="mt-1 font-mono text-[10px] text-ink-mute">audio not synthesised</p>
                ) : null}
              </li>
            ))}
          </ol>

          {call.promise && (
            <div className="rounded-[10px] border border-[var(--color-verdict-pass)]/40 bg-[var(--color-verdict-pass)]/[0.08] px-3 py-2.5">
              <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--color-verdict-pass)]">
                Promise captured
              </p>
              <p className="mt-1 text-[12.5px] leading-relaxed text-ink-dim">
                {rupees(call.promise.amountPaise)} by {shortTime(call.promise.dueAt)} — from
                &ldquo;{call.promise.verbatim}&rdquo;. It now holds every other action on this counterparty
                until that date, and it counts as kept only when Razorpay reports the money.
              </p>
            </div>
          )}

          <p className="border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">{call.note}</p>
        </div>
      )}
    </TerminalPanel>
  );
}
