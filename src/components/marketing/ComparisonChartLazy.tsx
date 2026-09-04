'use client';

import dynamic from 'next/dynamic';
import type { CurvePoint } from '@/lib/types';

/**
 * Recharts is the single heaviest dependency in the project and the chart it
 * draws sits well below the fold on the landing page. Loading it on demand
 * keeps it out of the initial marketing bundle, which is what a judge pays for
 * on first paint.
 *
 * The placeholder reserves the chart's exact height so nothing shifts when the
 * real component arrives.
 */
const ComparisonChart = dynamic(() => import('./ComparisonChart').then((m) => m.ComparisonChart), {
  ssr: false,
  loading: () => (
    <div
      className="flex w-full items-center justify-center rounded-[10px] border border-hairline/60"
      style={{ height: 320 }}
    >
      <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-mute">
        Loading chart…
      </span>
    </div>
  ),
});

export function ComparisonChartLazy(props: {
  baseline: CurvePoint[];
  uplift: CurvePoint[];
  height?: number;
  compact?: boolean;
}) {
  return <ComparisonChart {...props} />;
}
