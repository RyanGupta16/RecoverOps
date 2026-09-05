'use client';

import { animate } from 'animejs';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BRAND_EASE, useAnimeScope } from '@/components/motion/useAnimeScope';
import { DemoModeBadge } from '@/components/ui/primitives';
import { batchStreamUrl, fetchSources, HAS_BACKEND, startBatchRun, uploadIngestFile } from '@/lib/api';
import { rupees, rupeesCompact } from '@/lib/format';
import type { FileIngestMeta, LeakSourceInfo, LeakSourceName, StreamLine } from '@/lib/types';
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

const SOURCE_LABEL: Record<LeakSourceName, string> = {
  simulator: 'Simulator',
  razorpay: 'Razorpay account',
  file: 'Uploaded file',
};

export function BatchRunner({ script }: { script: StreamLine[] }) {
  const [state, setState] = useState<RunState>('idle');
  const [source, setSource] = useState<'live' | 'sample'>('sample');
  const [lines, setLines] = useState<StreamLine[]>([]);
  const [counters, setCounters] = useState(EMPTY_COUNTERS);

  const [sources, setSources] = useState<LeakSourceInfo[]>([]);
  const [leakSource, setLeakSource] = useState<LeakSourceName>('simulator');
  const [fileId, setFileId] = useState<string>('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<{ eventCount?: number; dataMode?: string } | null>(null);

  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const eventSource = useRef<EventSource | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const stopEverything = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    eventSource.current?.close();
    eventSource.current = null;
  }, []);

  useEffect(() => stopEverything, [stopEverything]);

  const applySources = useCallback((list: LeakSourceInfo[]) => {
    setSources(list);
    const files = list.find((s) => s.name === 'file')?.files ?? [];
    setFileId((current) => current || files[0]?.fileId || '');
  }, []);

  const refreshSources = useCallback(async () => {
    if (!HAS_BACKEND) return;
    applySources(await fetchSources());
  }, [applySources]);

  // Subscribe to the backend's source list on mount; state is set in the
  // fetch callback, never synchronously in the effect body.
  useEffect(() => {
    if (!HAS_BACKEND) return;
    let cancelled = false;
    fetchSources().then((list) => {
      if (!cancelled) applySources(list);
    });
    return () => {
      cancelled = true;
    };
  }, [applySources]);

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
    setLastRun({ eventCount: script.length > 2 ? script.length - 3 : 0, dataMode: 'synthetic' });
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
    setLastRun(null);
    setState('connecting');

    // No backend configured: go straight to the bundled replay rather than
    // firing a request that is known to fail.
    if (!HAS_BACKEND) {
      replaySample();
      return;
    }

    const outcome = await startBatchRun({
      source: leakSource,
      fileId: leakSource === 'file' ? fileId || undefined : undefined,
    });

    if (!outcome.ok) {
      // A backend that answered with a reason is not the same as no backend:
      // say what went wrong instead of quietly showing demo data.
      if (outcome.error) {
        setSource('live');
        pushLine({ kind: 'warn', text: `run refused — ${outcome.error}`, counters: null });
        setState('done');
        return;
      }
      replaySample();
      return;
    }

    setLastRun({ eventCount: outcome.eventCount, dataMode: outcome.dataMode });

    // Backend answered, so stream from it. If the stream errors before
    // delivering anything, fall back rather than leaving the judge staring at
    // an empty terminal.
    let received = 0;
    const es = new EventSource(batchStreamUrl(outcome.batchId));
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
  }, [fileId, leakSource, pushLine, replaySample, stopEverything]);

  const onUpload = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setUploading(true);
      setUploadError(null);
      try {
        const meta = await uploadIngestFile(file);
        await refreshSources();
        setFileId(meta.fileId);
        setLeakSource('file');
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : 'Upload failed.');
      } finally {
        setUploading(false);
        if (fileInput.current) fileInput.current.value = '';
      }
    },
    [refreshSources],
  );

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

  const real = lastRun?.dataMode === 'real';
  const counterRow: Counter[] = [
    { key: 'processed', label: 'Events processed', value: counters.processed },
    {
      key: 'recovered',
      label: real ? 'Recovered · outcomes pending' : 'Recovered',
      value: counters.recoveredPaise,
      kind: 'money',
      emphasis: !real,
    },
    { key: 'contacts', label: 'Contacts spent', value: counters.contacts },
    {
      key: 'dogs',
      label: real ? 'Sleeping dogs · unknown on real data' : 'Sleeping dogs left alone',
      value: counters.sleepingDogsAvoided,
      emphasis: !real,
    },
    { key: 'escalated', label: 'Escalated', value: counters.escalated },
  ];

  const fileSource = sources.find((s) => s.name === 'file');
  const files: FileIngestMeta[] = fileSource?.files ?? [];
  const selectedFile = files.find((f) => f.fileId === fileId);
  const busy = state === 'connecting' || state === 'running';
  const canRun = !busy && (leakSource !== 'file' || Boolean(fileId));

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
              disabled={!canRun}
              className="rounded-full bg-amber px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-[0.12em] text-deep disabled:cursor-not-allowed disabled:opacity-45"
            >
              {state === 'idle' ? 'Run batch' : state === 'done' ? 'Run again' : 'Running…'}
            </button>
          </div>
        }
      >
        {HAS_BACKEND && (
          <div className="mb-4 flex flex-col gap-3 rounded-[10px] border border-hairline bg-deep/50 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="mr-1 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-mute">
                Leaks from
              </span>
              {(['simulator', 'razorpay', 'file'] as LeakSourceName[]).map((name) => {
                const info = sources.find((s) => s.name === name);
                const available = info?.available ?? name === 'simulator';
                const active = leakSource === name;
                return (
                  <button
                    key={name}
                    type="button"
                    disabled={busy || !available}
                    onClick={() => setLeakSource(name)}
                    title={info?.note}
                    aria-pressed={active}
                    className={`rounded-full border px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.1em] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                      active
                        ? 'border-amber/50 bg-amber/[0.12] text-amber'
                        : 'border-hairline text-ink-dim hover:border-hairline-hi hover:text-ink'
                    }`}
                  >
                    {SOURCE_LABEL[name]}
                    {info && (
                      <span className={`ml-1.5 ${info.dataMode === 'real' ? 'text-brass' : 'text-ink-mute'}`}>
                        · {info.dataMode}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            <p className="text-[11.5px] leading-snug text-ink-mute">
              {sources.find((s) => s.name === leakSource)?.note ??
                'Seeded generator with both potential outcomes known.'}
            </p>

            {leakSource === 'file' && (
              <div className="flex flex-col gap-2.5 border-t border-hairline pt-3">
                <div className="flex flex-wrap items-center gap-2.5">
                  <label className="cursor-pointer rounded-full border border-hairline-hi px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-ink hover:bg-ink/[0.06]">
                    {uploading ? 'Uploading…' : 'Upload export (.json / .csv)'}
                    <input
                      ref={fileInput}
                      type="file"
                      accept=".json,.csv,application/json,text/csv"
                      className="sr-only"
                      disabled={uploading || busy}
                      onChange={(e) => void onUpload(e.target.files?.[0])}
                    />
                  </label>
                  {files.length > 0 && (
                    <select
                      value={fileId}
                      onChange={(e) => setFileId(e.target.value)}
                      disabled={busy}
                      aria-label="Uploaded file to run"
                      className="rounded-full border border-hairline bg-deep px-3 py-1.5 font-mono text-[10.5px] text-ink-dim"
                    >
                      {files.map((f) => (
                        <option key={f.fileId} value={f.fileId}>
                          {f.filename} · {f.failedRows} failed of {f.rows}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                {uploadError && (
                  <p className="font-mono text-[11px] text-[var(--color-verdict-block)]">{uploadError}</p>
                )}
                {selectedFile && (
                  <div className="grid gap-1 font-mono text-[10.5px] text-ink-mute sm:grid-cols-2">
                    <span>
                      <span className="text-ink-dim">{selectedFile.failedRows}</span> failed payments ·{' '}
                      <span className="text-ink-dim">{rupees(selectedFile.amountPaise)}</span> at risk
                    </span>
                    <span>
                      {Object.entries(selectedFile.byFamily)
                        .slice(0, 3)
                        .map(([k, v]) => `${k} ${v}`)
                        .join(' · ')}
                      {selectedFile.lowConfidence > 0 && (
                        <span className="text-brass"> · {selectedFile.lowConfidence} low-confidence</span>
                      )}
                    </span>
                    {selectedFile.warnings.map((w) => (
                      <span key={w} className="text-[var(--color-verdict-block)] sm:col-span-2">
                        {w}
                      </span>
                    ))}
                  </div>
                )}
                {files.length === 0 && !uploading && (
                  <p className="text-[11px] text-ink-mute">
                    A Razorpay payments export: the API&apos;s JSON (<span className="font-mono">{'{items: [...]}'}</span>) or
                    the dashboard CSV. Only rows with <span className="font-mono">status = failed</span> become leaks.
                  </p>
                )}
              </div>
            )}
          </div>
        )}

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
              Idle. Press <span className="text-amber">Run batch</span> to pull leak events from the{' '}
              <span className="text-ink-dim">{SOURCE_LABEL[leakSource].toLowerCase()}</span> and process
              them. The agent decides, the gate rules, and a baseline policy runs on the same events in
              parallel.
            </p>
          ) : (
            lines.map((line, i) => (
              <p key={`${line.eventId ?? 'sys'}-${i}`} className="flex gap-2.5">
                <span className="shrink-0 text-ink-mute">
                  {line.kind === 'system' || !line.counters
                    ? '  ··'
                    : String(line.counters.processed).padStart(4, ' ')}
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
                  <span className={line.kind === 'warn' ? 'text-[var(--color-verdict-block)]' : 'text-amber'}>
                    {line.text}
                  </span>
                )}
              </p>
            ))
          )}
        </div>

        <p className="mt-3 text-[11.5px] leading-relaxed text-ink-mute">
          Every line is a decision. Click one to open its full trace — diagnosis, retrieved
          precedents, per-action uplift estimates, every policy rule with its verdict and citation,
          and the execution result.
          {state === 'done' && lastRun && (
            <>
              {' '}
              {real
                ? `Processed ${counters.processed} real leaks, spending ${counters.contacts} contacts. Outcomes are pending — nothing here is counted as recovered until Razorpay reports it.`
                : `Recovered ${rupeesCompact(counters.recoveredPaise)} across ${counters.processed} events, spending ${counters.contacts} contacts.`}
            </>
          )}
        </p>
      </TerminalPanel>
    </div>
  );
}
