'use client';

import { animate, onScroll, stagger } from 'animejs';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from '@/components/motion/useAnimeScope';
import { DemoModeBadge } from '@/components/ui/primitives';
import { rupeesPrecise, SEGMENT_LABELS, shortTime, signed } from '@/lib/format';
import type { DataSource, DecisionTrace, Outcome } from '@/lib/types';
import { OutcomeMarker } from './OutcomeMarker';
import { RuleVerdictBadge, TerminalPanel } from './primitives';

function outcomeWord(o: Outcome): string {
  if (o === null) return 'pending';
  return o.recovered ? 'recovered' : o.churned ? 'cancelled' : 'unresolved';
}

export function DecisionTraceView({ trace, source }: { trace: DecisionTrace; source: DataSource }) {
  const { root } = useAnimeScope((self, host) => {
    const { reduceMotion } = self.matches;

    animate('[data-stage]', {
      opacity: [0, 1],
      translateY: reduceMotion ? 0 : [14, 0],
      duration: reduceMotion ? 1 : 560,
      delay: stagger(reduceMotion ? 0 : 70),
      ease: BRAND_EASE,
      autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
    });

    animate('[data-gate-row]', {
      opacity: [0, 1],
      translateX: reduceMotion ? 0 : [-8, 0],
      duration: reduceMotion ? 1 : 380,
      delay: stagger(reduceMotion ? 0 : 34, { start: reduceMotion ? 0 : 260 }),
      ease: BRAND_EASE,
      autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
    });
  });

  const { diagnosis, precedents, uplift, agentB, agentA, truth, leak } = trace;
  const real = trace.dataMode === 'real';
  const maxAbsEv = Math.max(...uplift.perAction.map((p) => Math.abs(p.expectedValuePaise)), 1);

  return (
    <div ref={root} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <DemoModeBadge source={source} />
        {real ? (
          <span className="inline-flex items-center gap-2 rounded-full border border-brass/50 bg-brass/[0.12] px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-brass">
            <span className="size-1.5 rounded-full bg-brass" />
            Real data · {trace.source} · outcome pending
          </span>
        ) : (
          <span className="font-mono text-[11px] text-ink-mute">
            synthetic · both branches known
          </span>
        )}
        <span className="font-mono text-[11px] text-ink-mute">
          estimator: <span className="text-brass">{uplift.estimator}</span>
        </span>
        {trace.kind && (
          <span className="font-mono text-[11px] text-ink-mute">
            leak: <span className="text-ink-dim">{trace.kind.replace(/_/g, ' ')}</span>
          </span>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <div data-stage className="reveal-init">
          <TerminalPanel title="01 · Diagnosis" meta={diagnosis.method.replace('_', ' ')}>
            <dl className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-2 font-mono text-[11.5px]">
              <dt className="text-ink-mute">reason_family</dt>
              <dd className="text-amber">{diagnosis.reasonCode}</dd>
              {leak?.rawReason && (
                <>
                  <dt className="text-ink-mute">error_reason</dt>
                  <dd className="text-ink-dim">
                    {leak.rawReason}
                    <span className="ml-2 text-ink-mute">· {leak.reasonConfidence} confidence</span>
                  </dd>
                </>
              )}
              <dt className="text-ink-mute">description</dt>
              <dd className="text-ink-dim">{diagnosis.reasonLabel}</dd>
              <dt className="text-ink-mute">attributed</dt>
              <dd className="text-ink-dim">{diagnosis.failureSide}-side</dd>
              <dt className="text-ink-mute">model_latency</dt>
              <dd
                className={
                  diagnosis.latencyMs === 0 ? 'text-[var(--color-verdict-pass)]' : 'text-ink-dim'
                }
              >
                {diagnosis.latencyMs} ms
              </dd>
            </dl>
            <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-ink-mute">
              {diagnosis.note}
            </p>
          </TerminalPanel>
        </div>

        <div data-stage className="reveal-init">
          <TerminalPanel title="02 · Retrieval" meta={`${precedents.length} precedents`}>
            <ul className="flex flex-col gap-2.5">
              {precedents.map((p, i) => (
                <li
                  key={`${p.ref}-${i}`}
                  className="border-b border-hairline pb-2.5 last:border-b-0 last:pb-0"
                >
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="rounded bg-ink/[0.07] px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.08em] text-brass">
                      {p.source}
                    </span>
                    <span className="font-mono text-[11px] text-ink">{p.ref}</span>
                  </div>
                  <p className="mt-1.5 text-[11.5px] leading-snug text-ink-mute">{p.note}</p>
                </li>
              ))}
            </ul>
          </TerminalPanel>
        </div>
      </div>

      <div data-stage className="reveal-init">
        <TerminalPanel
          title="03 · Uplift engine"
          meta={`p(recover | quiet) ${uplift.pControlHat.toFixed(3)} · p(recover | contact) ${uplift.pTreatHat.toFixed(3)}${
            uplift.churnUpliftHat != null ? ` · churn uplift ${signed(uplift.churnUpliftHat)}` : ''
          }`}
        >
          <div className="mb-4 flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <div>
              <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-mute">
                Estimated uplift
              </span>
              <p
                className={`font-mono text-[26px] tabular-nums ${
                  uplift.upliftHat > 0 ? 'text-amber' : 'text-[var(--color-verdict-block)]'
                }`}
              >
                {signed(uplift.upliftHat)}
              </p>
            </div>
            <p className="max-w-[520px] text-[11.5px] leading-relaxed text-ink-mute">
              {uplift.estimatorMode === 'priors'
                ? 'From reason-family priors. No model has seen this customer: on real data the estimator earns the right to rank only after the holdout has measured real outcomes.'
                : 'The difference between the two branch estimates. Positive means contact helps; negative means contact costs you the payment and, often, the subscription.'}
            </p>
          </div>

          <ul className="flex flex-col gap-1.5">
            {uplift.perAction.map((action) => {
              const isChosen = action.action === agentB.chosenAction;
              const width = (Math.abs(action.expectedValuePaise) / maxAbsEv) * 100;
              return (
                <li
                  key={action.action}
                  // Label over bar on narrow screens; three columns once there
                  // is room, so the bar never gets squeezed to nothing.
                  className={`grid grid-cols-[1fr_92px] items-center gap-x-3 gap-y-1 rounded px-2 py-1.5 sm:grid-cols-[168px_1fr_92px] ${
                    isChosen ? 'bg-amber/[0.10]' : ''
                  } ${action.eligible ? '' : 'opacity-45'}`}
                >
                  <span
                    className={`col-start-1 row-start-1 font-mono text-[11px] ${
                      isChosen ? 'text-amber' : 'text-ink-dim'
                    }`}
                  >
                    {action.label}
                    {action.messageClass && (
                      <span className="ml-1.5 text-[9.5px] uppercase tracking-[0.08em] text-ink-mute">
                        {action.messageClass}
                        {action.costPaise != null && ` · ${rupeesPrecise(action.costPaise)}`}
                      </span>
                    )}
                  </span>
                  {/* Explicit placement: auto-flow would push the value onto a
                      third row once the bar spans the full width on mobile. */}
                  <span className="col-span-2 col-start-1 row-start-2 flex h-1.5 items-center sm:col-span-1 sm:col-start-2 sm:row-start-1">
                    <span
                      className={`h-1.5 rounded-full ${
                        action.expectedValuePaise >= 0
                          ? 'bg-brass'
                          : 'bg-[var(--color-verdict-block)]'
                      }`}
                      style={{ width: `${Math.max(width, 2)}%` }}
                    />
                  </span>
                  <span
                    className={`col-start-2 row-start-1 text-right font-mono text-[11px] tabular-nums sm:col-start-3 ${
                      action.expectedValuePaise >= 0
                        ? 'text-ink-dim'
                        : 'text-[var(--color-verdict-block)]'
                    }`}
                  >
                    {rupeesPrecise(action.expectedValuePaise)}
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 border-t border-hairline pt-3 font-mono text-[10.5px] text-ink-mute">
            Expected value = uplift × amount − churn uplift × amount × residual cycles − channel cost
            at the message class the gate assigns. Ineligible actions dimmed.
          </p>
        </TerminalPanel>
      </div>

      <div data-stage className="reveal-init">
        <TerminalPanel
          title="04 · Policy gate"
          meta={`message class: ${agentB.messageClass ?? 'none — no outbound message'}${
            agentB.blockedBy ? ` · blocked by ${agentB.blockedBy}` : ' · no block'
          }`}
        >
          {agentB.deniedBy && (
            <p className="mb-3 rounded border border-[var(--color-verdict-block)]/40 bg-[var(--color-verdict-block)]/[0.10] px-3 py-2 text-[11.5px] leading-snug text-ink-dim">
              The agent first wanted <span className="text-ink">{agentB.deniedAction}</span>, which{' '}
              <span className="font-mono text-[var(--color-verdict-block)]">{agentB.deniedBy}</span>{' '}
              refused. What follows is the gate run for the action it fell back to.
            </p>
          )}

          <ul className="flex flex-col">
            {agentB.gate.map((rule, i) => (
              <li
                key={`${rule.ruleId}-${i}`}
                data-gate-row
                className="reveal-init grid grid-cols-[52px_1fr] items-start gap-x-3 gap-y-1 border-b border-hairline/60 py-2 last:border-b-0 lg:grid-cols-[52px_248px_1fr]"
              >
                <RuleVerdictBadge verdict={rule.verdict} />
                <span className="flex flex-col gap-0.5">
                  <span
                    className={`font-mono text-[10.5px] break-all ${
                      rule.verdict === 'BLOCK' ? 'text-[var(--color-verdict-block)]' : 'text-brass'
                    }`}
                  >
                    {rule.ruleId}
                  </span>
                  {rule.citation && (
                    <span className="font-mono text-[9px] uppercase tracking-[0.08em] text-ink-mute">
                      {rule.citation}
                    </span>
                  )}
                </span>
                <span className="col-start-2 text-[11.5px] leading-snug text-ink-mute lg:col-start-3">
                  {rule.note}
                </span>
              </li>
            ))}
          </ul>
        </TerminalPanel>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <div data-stage className="reveal-init">
          <TerminalPanel
            title="05 · Execution"
            meta={agentB.execution.mocked ? 'delivery mocked' : 'test mode'}
          >
            <p className="font-mono text-[12px] text-amber">{agentB.chosenLabel}</p>
            <p className="mt-2 break-all font-mono text-[11px] leading-relaxed text-ink-dim">
              {agentB.execution.detail}
            </p>
            {agentB.costPaise != null && agentB.costPaise > 0 && (
              <p className="mt-2 font-mono text-[10.5px] text-ink-mute">
                channel cost {rupeesPrecise(agentB.costPaise)} at {agentB.messageClass} class
              </p>
            )}
            {agentB.execution.mocked && (
              <p className="mt-3 rounded border border-hairline bg-ink/[0.04] px-3 py-2 text-[11px] leading-snug text-ink-mute">
                Razorpay objects are real test-mode objects where keys are configured. The message
                carrying them is not sent — outbound delivery is mocked and labelled throughout.
              </p>
            )}
          </TerminalPanel>
        </div>

        <div data-stage className="reveal-init">
          {truth ? (
            <TerminalPanel
              title="06 · Outcome, and the branch we did not take"
              meta="synthetic ground truth"
            >
              <dl className="grid grid-cols-[142px_1fr] gap-x-4 gap-y-2 font-mono text-[11.5px]">
                <dt className="text-ink-mute">true_segment</dt>
                <dd className="text-amber">{SEGMENT_LABELS[truth.segment]}</dd>
                <dt className="text-ink-mute">p_recover_quiet</dt>
                <dd className="text-ink-dim">{truth.pControl.toFixed(2)}</dd>
                <dt className="text-ink-mute">p_recover_contact</dt>
                <dd className="text-ink-dim">{truth.pTreat.toFixed(2)}</dd>
                <dt className="text-ink-mute">p_cancel_quiet</dt>
                <dd className="text-ink-dim">{truth.churnControl.toFixed(2)}</dd>
                <dt className="text-ink-mute">p_cancel_contact</dt>
                <dd
                  className={
                    truth.churnTreat > truth.churnControl
                      ? 'text-[var(--color-verdict-block)]'
                      : 'text-ink-dim'
                  }
                >
                  {truth.churnTreat.toFixed(2)}
                </dd>
              </dl>

              <div className="mt-3 grid grid-cols-2 gap-3 border-t border-hairline pt-3">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-ink-mute">
                    RecoverOps
                  </p>
                  <p className="mt-1 font-mono text-[12px] text-ink">{outcomeWord(agentB.outcome)}</p>
                </div>
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-ink-mute">
                    Baseline · {agentA.chosenLabel}
                  </p>
                  <p className="mt-1 font-mono text-[12px] text-ink-dim">{outcomeWord(agentA.outcome)}</p>
                </div>
              </div>

              <p className="mt-3 border-t border-hairline pt-3 text-[11px] leading-relaxed text-ink-mute">
                These probabilities exist because the batch is synthetic. On live traffic this panel
                would be empty — you never observe the branch you did not take.
              </p>
            </TerminalPanel>
          ) : (
            <TerminalPanel
              title="06 · Outcome"
              meta={
                trace.outcomeAttribution
                  ? `${trace.outcomeAttribution.state} · ${trace.outcomeAttribution.source ?? 'unknown source'}`
                  : 'pending — real data'
              }
            >
              {trace.outcomeAttribution ? (
                <div>
                  <p className="font-mono text-[20px] tabular-nums text-ink">
                    {trace.outcomeAttribution.state === 'resolved' ? outcomeWord(agentB.outcome) : 'unresolved'}
                  </p>
                  <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-mute">
                    Attributed via <span className="font-mono text-ink-dim">{trace.outcomeAttribution.source}</span>
                    {trace.outcomeAttribution.at && ` at ${shortTime(trace.outcomeAttribution.at)}`}. This leak now
                    counts in the measured policy effect and in case memory. The decision trace above is
                    unchanged — the outcome is overlaid, never rewritten.
                  </p>
                </div>
              ) : (
                <p className="text-[12px] leading-relaxed text-ink-dim">
                  No outcome is known yet. The branch not taken is never observed on real data, and
                  the branch taken has not resolved. The learning loop attributes the outcome when
                  Razorpay reports it — <span className="font-mono text-ink">subscription.charged</span>,{' '}
                  <span className="font-mono text-ink">payment_link.paid</span> — and only then does
                  this leak count toward measured recovery.
                </p>
              )}
              {agentB.arm && (
                <p className="mt-3 font-mono text-[11px] text-ink-mute">
                  arm <span className={agentB.arm === 'control' ? 'text-amber' : 'text-ink-dim'}>{agentB.arm}</span>
                  {agentB.propensity != null && (
                    <>
                      {' '}
                      · P(contact) <span className="text-ink-dim">{agentB.propensity.toFixed(2)}</span>
                    </>
                  )}
                  {agentB.explored && <span className="text-brass"> · explored (decision flipped at random)</span>}
                </p>
              )}
              {!trace.outcomeAttribution && source === 'live' && <OutcomeMarker eventId={trace.eventId} />}
              {leak && (
                <dl className="mt-3 grid grid-cols-[142px_1fr] gap-x-4 gap-y-2 border-t border-hairline pt-3 font-mono text-[11.5px]">
                  <dt className="text-ink-mute">baseline_action</dt>
                  <dd className="text-ink-dim">{agentA.chosenLabel}</dd>
                  <dt className="text-ink-mute">counterparty</dt>
                  <dd className="text-ink-dim">
                    {leak.customerId}
                    {leak.contactHash && <span className="text-ink-mute"> · contact #{leak.contactHash}</span>}
                  </dd>
                  <dt className="text-ink-mute">history</dt>
                  <dd className="text-ink-dim">
                    {leak.attemptsThisCycle} attempt(s) · {leak.retries30d} in 30d · {leak.contactsLast7d}{' '}
                    contact(s) in 7d
                  </dd>
                  <dt className="text-ink-mute">features</dt>
                  <dd className={leak.featuresAreProxies ? 'text-brass' : 'text-ink-dim'}>
                    {leak.featuresAreProxies ? 'engagement and tenure are proxies' : 'measured'}
                  </dd>
                  <dt className="text-ink-mute">holdout</dt>
                  <dd className="text-ink-dim">{leak.holdout ? 'control arm — silent path only' : 'treatment arm'}</dd>
                </dl>
              )}
            </TerminalPanel>
          )}
        </div>
      </div>
    </div>
  );
}
