'use client';

import { animate, onScroll, svg, utils } from 'animejs';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { BRAND_EASE, REVEAL_TRIGGER, useAnimeScope } from '@/components/motion/useAnimeScope';
import { rupeesCompact } from '@/lib/format';
import type { CurvePoint } from '@/lib/types';

interface Row {
  contacts: number;
  baseline: number | null;
  uplift: number | null;
}

/**
 * Cumulative incremental value as each agent spends its contact budget, in
 * ranking order. Read left to right: how much net value has this policy created
 * by the time it has made N contacts?
 *
 * Recharts draws the plot; Anime.js draws it *in*. Recharts' own animation is
 * disabled so the line-draw is a single system rather than two animation
 * engines fighting over the same paths.
 */
export function ComparisonChart({
  baseline,
  uplift,
  height = 320,
  compact = false,
}: {
  baseline: CurvePoint[];
  uplift: CurvePoint[];
  height?: number;
  compact?: boolean;
}) {
  const byContacts = new Map<number, Row>();
  for (const p of baseline) {
    byContacts.set(p.contacts, {
      contacts: p.contacts,
      baseline: p.netPaise,
      uplift: null,
    });
  }
  for (const p of uplift) {
    const existing = byContacts.get(p.contacts);
    if (existing) existing.uplift = p.netPaise;
    else
      byContacts.set(p.contacts, {
        contacts: p.contacts,
        baseline: null,
        uplift: p.netPaise,
      });
  }
  const data = [...byContacts.values()].sort((a, b) => a.contacts - b.contacts);

  const { root } = useAnimeScope(
    (self, host) => {
      const { reduceMotion } = self.matches;
      if (reduceMotion) return;

      // Recharts renders asynchronously inside ResponsiveContainer, so the paths
      // do not exist on the first tick. Wait for them, then hand them to Anime.js.
      let cancelled = false;
      let frame = 0;

      const start = () => {
        if (cancelled) return;
        const paths = host.querySelectorAll<SVGPathElement>('.recharts-line-curve');
        if (paths.length < 2) {
          frame = requestAnimationFrame(start);
          return;
        }

        utils.set(paths, { opacity: 1 });
        animate(svg.createDrawable(paths), {
          draw: ['0 0', '0 1'],
          duration: 1500,
          // Baseline draws first, then ours over it.
          delay: (_target: unknown, i = 0) => i * 220,
          ease: BRAND_EASE,
          autoplay: onScroll({ ...REVEAL_TRIGGER, target: host }),
        });
      };

      frame = requestAnimationFrame(start);
      return () => {
        cancelled = true;
        cancelAnimationFrame(frame);
      };
    },
    [data.length],
  );

  return (
    <div ref={root} style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: compact ? -18 : 4 }}>
          <CartesianGrid stroke="rgba(247,242,232,0.08)" vertical={false} />
          <XAxis
            dataKey="contacts"
            stroke="rgba(247,242,232,0.28)"
            tick={{ fill: '#9a927f', fontSize: 11 }}
            tickLine={false}
            label={
              compact
                ? undefined
                : {
                    value: 'Contacts spent, in each policy’s own ranking order',
                    position: 'insideBottom',
                    offset: -2,
                    fill: '#9a927f',
                    fontSize: 11,
                  }
            }
            height={compact ? 26 : 46}
          />
          <YAxis
            stroke="rgba(247,242,232,0.28)"
            tick={{ fill: '#9a927f', fontSize: 11 }}
            tickLine={false}
            width={compact ? 52 : 74}
            tickFormatter={(v: number) => rupeesCompact(v)}
          />
          <ReferenceLine y={0} stroke="rgba(247,242,232,0.35)" strokeDasharray="3 3" />
          <Tooltip
            cursor={{ stroke: 'rgba(247,242,232,0.2)' }}
            contentStyle={{
              background: 'rgba(20,15,11,0.96)',
              border: '1px solid rgba(247,242,232,0.18)',
              borderRadius: 12,
              fontSize: 12,
            }}
            labelStyle={{ color: '#cfc6b5', marginBottom: 4 }}
            labelFormatter={(v) => `${v} contacts spent`}
            formatter={(value, name) => [rupeesCompact(Number(value)), String(name)]}
          />
          <Line
            type="monotone"
            dataKey="baseline"
            name="Baseline — ranks by recovery probability"
            stroke="var(--color-ink-mute)"
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="uplift"
            name="RecoverOps — ranks by causal uplift"
            stroke="var(--color-amber)"
            strokeWidth={2.5}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
