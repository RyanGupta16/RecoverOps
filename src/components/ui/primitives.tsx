import Link from 'next/link';
import type { ReactNode } from 'react';
import { Reveal } from '@/components/motion/Reveal';

/** Small uppercase accent label that sits above every section headline. */
export function EyebrowLabel({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-block text-[11px] font-medium uppercase tracking-[0.28em] text-brass ${className}`}
    >
      {children}
    </span>
  );
}

/**
 * Carries its own Reveal so the eyebrow, headline and lede stagger against each
 * other. Call sites do not need to wrap it.
 */
export function SectionHead({
  eyebrow,
  title,
  lede,
  align = 'left',
  id,
}: {
  eyebrow: string;
  title: ReactNode;
  lede?: ReactNode;
  align?: 'left' | 'center';
  id?: string;
}) {
  return (
    <Reveal className={`mb-14 max-w-[760px] ${align === 'center' ? 'mx-auto text-center' : ''}`}>
      <div>
        <EyebrowLabel>{eyebrow}</EyebrowLabel>
      </div>
      <h2 id={id} className="display mt-4 text-[clamp(32px,4.6vw,60px)] text-ink">
        {title}
      </h2>
      {lede && (
        <p
          className={`mt-5 max-w-[600px] text-[15.5px] leading-[1.65] text-ink-dim ${align === 'center' ? 'mx-auto' : ''}`}
        >
          {lede}
        </p>
      )}
    </Reveal>
  );
}

export function GlassCard({
  children,
  className = '',
  accent = true,
  tone = 'base',
}: {
  children: ReactNode;
  className?: string;
  accent?: boolean;
  tone?: 'base' | 'high';
}) {
  return (
    <div
      className={[
        'rounded-[var(--radius-card)] border backdrop-blur-xl',
        tone === 'high' ? 'border-hairline-hi bg-glass-hi' : 'border-hairline bg-glass',
        accent ? 'card-accent' : '',
        className,
      ].join(' ')}
    >
      {children}
    </div>
  );
}

export function Tag({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'amber' | 'rust' | 'quiet';
}) {
  const tones = {
    amber: 'bg-amber/[0.18] text-amber',
    rust: 'bg-rust/[0.20] text-[#e08b60]',
    neutral: 'bg-ink/[0.10] text-ink-dim',
    quiet: 'bg-ink/[0.06] text-ink-mute',
  }[tone];
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.1em] ${tones}`}
    >
      {children}
    </span>
  );
}

/**
 * Rendered anywhere the numbers on screen came from the bundled synthetic batch
 * rather than a live backend run. It disappears the moment a real response
 * arrives — that switch is driven by the `source` field on the response, not by
 * a prop someone might forget to flip.
 */
export function DemoModeBadge({
  source,
  className = '',
  detail,
}: {
  source: 'live' | 'sample';
  className?: string;
  detail?: string;
}) {
  if (source === 'live') {
    return (
      <span
        className={`inline-flex items-center gap-2 rounded-full border border-hairline bg-glass px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-dim ${className}`}
      >
        <span className="size-1.5 rounded-full bg-[var(--color-verdict-pass)]" />
        Live backend
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full border border-amber/40 bg-amber/[0.12] px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-amber ${className}`}
      title={
        detail ??
        'The backend is unreachable, so these figures come from the bundled synthetic batch.'
      }
    >
      <span className="size-1.5 rounded-full bg-amber" />
      Demo mode — sample data
    </span>
  );
}

export function ButtonLink({
  href,
  children,
  variant = 'primary',
  className = '',
}: {
  href: string;
  children: ReactNode;
  variant?: 'primary' | 'ghost';
  className?: string;
}) {
  const styles =
    variant === 'primary'
      ? 'bg-amber text-deep font-semibold hover:shadow-[0_16px_40px_rgba(232,165,82,0.35)]'
      : 'border border-hairline-hi text-ink font-medium hover:border-ink hover:bg-ink/[0.08]';
  return (
    <Link
      href={href}
      data-press
      className={`inline-flex items-center justify-center rounded-full px-7 py-4 text-sm ${styles} ${className}`}
    >
      {children}
    </Link>
  );
}

/** Section wrapper: consistent vertical rhythm and a max-width container. */
export function Section({
  id,
  children,
  className = '',
  tone = 'over-video',
}: {
  id?: string;
  children: ReactNode;
  className?: string;
  /** `grounded` sections sit on solid background after the scrub hands off. */
  tone?: 'over-video' | 'grounded';
}) {
  return (
    <section
      id={id}
      className={`relative px-5 py-[clamp(72px,11vw,132px)] md:px-10 ${tone === 'grounded' ? 'bg-deep' : ''} ${className}`}
    >
      <div className="mx-auto max-w-[1200px]">{children}</div>
    </section>
  );
}
