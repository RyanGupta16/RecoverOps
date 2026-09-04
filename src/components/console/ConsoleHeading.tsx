import type { ReactNode } from 'react';

export function ConsoleHeading({
  title,
  sub,
  aside,
}: {
  title: string;
  sub: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="display text-[clamp(24px,3vw,34px)] text-ink">{title}</h1>
        <p className="mt-2 max-w-[720px] text-[13px] leading-[1.6] text-ink-dim">{sub}</p>
      </div>
      {aside}
    </div>
  );
}
