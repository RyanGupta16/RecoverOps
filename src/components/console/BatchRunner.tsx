'use client';

import { animate } from 'animejs';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BRAND_EASE, useAnimeScope } from '@/components/motion/useAnimeScope';
import { DemoModeBadge } from '@/components/ui/primitives';
import { API_URL, HAS_BACKEND, batchStreamUrl } from '@/lib/api';
import { rupeesCompact } from '@/lib/format';
import type { StreamLine } from '@/lib/types';
import { LiveCounterRow, type Counter } from './LiveCounterRow';
import { TerminalPanel } from './primitives';

type RunState = 'idle' | 'connecting' | 'running' | 'done';

const EMPTY_COUNTERS = {
  processed: 0,
  recoveredPaise: 0,
  contacts: 0,
  sleepingDogsAvoided: 0,
  escalated: 0,
};

/** How fast the bundled script replays, in ms per event. */
const REPLAY_INTERVAL = 26;
/** Lines kept in the DOM. The full run is 500+ events and the log is a view, not the record. */
const LOG_WINDOW = 220;

export function BatchRunner({ script }: { script: StreamLine[] }) {
  const [state, setState] = useState<RunState>('idle');
  const [source, setSource] = useState<'live' | 'sample'>('sample');
  const [lines, setLines] = useState<StreamLine[]>([]);
  const [counters, setCounters] = useState(EMPTY_COUNTERS);

  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const eventSource = useRef<EventSource | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const stopEverything = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    eventSource.current?.close();
    eventSource.current = null;
  }, []);

  useEffect(() => stopEverything, [stopEverything]);

  const pushLine = useCallback((line: StreamLine) => {
    setLines((prev) => {
      const next = [...prev, line];
      return next.length > LOG_WINDOW ? next.slice(next.length - LOG_WINDOW) : next;
    });
    if (line.counters) setCounters(line.counters);
  }, []);

  /** Replays the bundled synthetic batch when the backend never opens a stream. */
  const replaySample = useCallback(() => {
    setSource('sample');
    setState('running');
    script.forEach((line, i) => {
      timers.current.push(
        setTimeout(() => {
          pushLine(line);
          if (i === script.length - 1) setState('done');
        }, i * REPLAY_INTERVAL),
      );
    });
  }, [script, pushLine]);

  const run = useCallback(async () => {
    stopEverything();
    setLines([]);
    setCounters(EMPTY_COUNTERS);
    setState('connecting');

    let batchId = 'bat_sample_20260903';
    let live = false;

    // No backend configured: go straight to the bundled replay rather than
    // firing a request that is known to fail.
    if (!HAS_BACKEND) {
      replaySample();
      return;
    }

    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      const res = await fetch(`${API_URL}/api/batch/run`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({}),
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (res.ok) {
        const json = (await res.json()) as { batchId?: string; batch_id?: string };
        batchId = json.batchId ?? json.batch_id ?? batchId;
        live = true;
      }
    } catch {
      /* backend unreachable */
    }

    if (!live) {
      replaySample();
      return;
    }

    // Backend answered, so stream from it. If the stream errors before
    // delivering anything, fall back rather than leaving the judge staring at
    // an empty terminal.
    let received = 0;
    const es = new EventSource(batchStreamUrl(batchId));
    eventSource.current = es;
    setSource('live');

    es.onmessage = (event) => {
      try {
        const line = JSON.parse(event.data) as StreamLine;
        received += 1;
        pushLine(line);
        setState('running');
      } catch {
        /* ignore malformed frames */
      }
    };
    es.addEventListener('done', () => {
      setState('done');
      es.close();
    });
    es.onerror = () => {
      es.close();
      eventSource.current = null;
      if (received === 0) replaySample();
      else setState('done');
    };
  }, [pushLine, replaySample, stopEverything]);

  // New log lines animate in as they arrive.
  const { root } = useAnimeScope(
    (self) => {
      const { reduceMotion } = self.matches;
      if (reduceMotion) return;
      const last = logRef.current?.lastElementChild;
      if (!last) return;
      animate(last, { opacity: [0, 1], translateX: [-6, 0], duration: 260, ease: BRAND_EASE });
    },
    [lines.length],
  );

  // Keep the newest line in view without hijacking the page scroll.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  const counterRow: Counter[] = [
    { key: 'processed', label: 'Events processed', value: counters.processed },
    {
      key: 'recovered',
      label: 'Recovered',
      value: counters.recoveredPaise,
      kind: 'money',
      emphasis: true,
    },
    { key: 'contacts', label: 'Contacts spent', value: counters.contacts },
    {
      key: 'dogs',
      label: 'Sleeping dogs left alone',
      value: counters.sleepingDogsAvoided,
      emphasis: true,
    },
    { key: 'escalated', label: 'Escalated', value: counters.escalated },
  ];

  return (
    <div ref={root} className="flex flex-col gap-4">
      <TerminalPanel
        title="Batch runner"
        meta={
          state === 'idle'
            ? 'idle'
            : state === 'connecting'
              ? 'connecting…'
              : state === 'running'
                ? 'streaming'
                : 'complete'
        }
        actions={
          <div className="flex items-center gap-3">
            {state !== 'idle' && <DemoModeBadge source={source} />}
            <button
              type="button"
              onClick={run}
              disabled={state === 'connecting' || state === 'running'}
              className="rounded-full bg-amber px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-[0.12em] text-deep disabled:cursor-not-allowed disabled:opacity-45"
            >
              {state === 'idle' ? 'Run batch' : state === 'done' ? 'Run again' : 'Running…'}
            </button>
          </div>
        }
      >
        <LiveCounterRow counters={counterRow} />

        <div
          ref={logRef}
          role="log"
          aria-live="polite"
          aria-label="Batch decision stream"
          className="mt-4 h-[420px] overflow-y-auto hide-scrollbar rounded-[10px] border border-hairline bg-deep/70 p-3 font-mono text-[11.5px] leading-[1.7]"
        >
          {lines.length === 0 ? (
            <p className="text-ink-mute">
              Idle. Press <span className="text-amber">Run batch</span> to process{' '}
              {script.length > 2 ? script.length - 3 : 0} failed payment events. The agent decides,
              the gate rules, and a baseline policy runs on the same events in parallel.
            </p>
          ) : (
            lines.map((line, i) => (
              <p key={`${line.eventId ?? 'sys'}-${i}`} className="flex gap-2.5">
                <span className="shrink-0 text-ink-mute">
                  {line.kind === 'system'
                    ? '  ··'
                    : String(line.counters?.processed ?? '').padStart(4, ' ')}
                </span>
                {line.eventId ? (
                  <Link
                    href={`/console/trace/${line.eventId}`}
                    className={`hover:underline ${
                      line.kind === 'warn'
                        ? 'text-[var(--color-verdict-block)]'
                        : line.kind === 'gate'
                          ? 'text-brass'
                          : 'text-ink-dim'
                    }`}
                  >
                    {line.text}
                  </Link>
                ) : (
                  <span className="text-amber">{line.text}</span>
                )}
              </p>
            ))
          )}
        </div>

        <p className="mt-3 text-[11.5px] leading-relaxed text-ink-mute">
          Every line is a decision. Click one to open its full trace — diagnosis, retrieved
          precedents, per-action uplift estimates, every policy rule with its verdict, and the
          execution result.
          {state === 'done' && (
            <>
              {' '}
              Recovered {rupeesCompact(counters.recoveredPaise)} across {counters.processed} events,
              spending {counters.contacts} contacts.
            </>
          )}
        </p>
      </TerminalPanel>
    </div>
  );
}
