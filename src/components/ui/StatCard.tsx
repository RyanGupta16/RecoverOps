'use client';

import { CountUp } from '@/components/motion/CountUp';
import { GlassCard } from './primitives';

export function StatCard({
  value,
  suffix = '',
  prefix = '',
  decimals = 0,
  label,
  context,
  attribution,
  delay = 0,
}: {
  value: number;
  suffix?: string;
  prefix?: string;
  decimals?: number;
  label: string;
  context: string;
  /** Where the number comes from. Every stat on this site carries one. */
  attribution: string;
  delay?: number;
}) {
  return (
    <GlassCard className="flex h-full flex-col p-7 md:p-8">
      <div className="display text-[clamp(44px,5.6vw,62px)] leading-none text-amber">
        <CountUp to={value} prefix={prefix} suffix={suffix} decimals={decimals} delay={delay} />
      </div>
      <p className="mt-3.5 text-sm font-medium text-ink">{label}</p>
      <p className="mt-2 flex-1 text-[13px] leading-[1.55] text-ink-mute">{context}</p>
      <p className="mt-4 border-t border-hairline pt-3 font-mono text-[10px] uppercase leading-relaxed tracking-[0.1em] text-ink-mute">
        {attribution}
      </p>
    </GlassCard>
  );
}
