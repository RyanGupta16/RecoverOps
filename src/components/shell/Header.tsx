'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { animate, stagger } from 'animejs';
import { BRAND_EASE, useAnimeScope } from '@/components/motion/useAnimeScope';

const MARKETING_LINKS = [
  { href: '/#problem', label: 'Problem' },
  { href: '/#insight', label: 'Insight' },
  { href: '/#pipeline', label: 'Pipeline' },
  { href: '/#results', label: 'Results' },
  { href: '/#policy', label: 'Policy' },
];

const CONSOLE_LINKS = [
  { href: '/console', label: 'Batch' },
  { href: '/console/compare', label: 'Compare' },
  { href: '/console/sleeping-dogs', label: 'Sleeping dogs' },
  { href: '/console/exceptions', label: 'Exceptions' },
  { href: '/console/degradation', label: 'Degradation' },
  { href: '/console/promises', label: 'Promises' },
  { href: '/console/history', label: 'History' },
  { href: '/console/learning', label: 'Learning' },
];

export function Header() {
  const pathname = usePathname();
  const inConsole = pathname.startsWith('/console');
  const links = inConsole ? CONSOLE_LINKS : MARKETING_LINKS;

  const { root } = useAnimeScope<HTMLElement>(
    (self) => {
      const { reduceMotion } = self.matches;
      animate('[data-nav-item]', {
        opacity: [0, 1],
        translateY: reduceMotion ? 0 : [-8, 0],
        duration: reduceMotion ? 1 : 620,
        delay: stagger(reduceMotion ? 0 : 45),
        ease: BRAND_EASE,
      });
    },
    [inConsole],
  );

  return (
    <header
      ref={root}
      className="fixed inset-x-0 top-0 z-[100] border-b border-transparent bg-gradient-to-b from-deep/85 to-transparent backdrop-blur-xl"
    >
      <div className="mx-auto flex max-w-[1240px] items-center justify-between gap-4 px-5 py-3.5 md:px-10">
        <Link href="/" className="flex shrink-0 items-center gap-2.5" data-nav-item>
          <span
            className="size-7 rounded-full shadow-[0_0_20px_rgba(232,165,82,0.4),inset_0_0_8px_rgba(0,0,0,0.3)]"
            style={{ background: 'radial-gradient(circle at 30% 30%, #e8a552, #c86a3c 70%)' }}
          />
          <span className="display text-[21px] text-ink">RecoverOps</span>
          {inConsole && (
            <span className="hidden font-mono text-[10px] uppercase tracking-[0.2em] text-ink-mute sm:inline">
              / console
            </span>
          )}
        </Link>

        <nav
          className="flex items-center gap-1 md:gap-2"
          aria-label={inConsole ? 'Console' : 'Sections'}
        >
          <ul className="hidden items-center gap-1 lg:flex">
            {links.map((link) => {
              const active = inConsole && pathname === link.href;
              return (
                <li key={link.href} data-nav-item>
                  <NavPill href={link.href} active={active} mono={inConsole}>
                    {link.label}
                  </NavPill>
                </li>
              );
            })}
          </ul>

          <div data-nav-item>
            {inConsole ? (
              <NavPill href="/" variant="ghost">
                Back to the pitch
              </NavPill>
            ) : (
              <NavPill href="/console" variant="solid">
                Open the console
              </NavPill>
            )}
          </div>
        </nav>
      </div>
    </header>
  );
}

export function NavPill({
  href,
  children,
  variant = 'quiet',
  active = false,
  mono = false,
}: {
  href: string;
  children: React.ReactNode;
  variant?: 'quiet' | 'ghost' | 'solid';
  active?: boolean;
  mono?: boolean;
}) {
  const base = 'inline-flex items-center rounded-full px-3.5 py-2 text-[13px] md:px-4';
  const styles = {
    quiet: active ? 'bg-amber/15 text-amber' : 'text-ink-dim hover:bg-ink/[0.06] hover:text-ink',
    ghost: 'border border-hairline-hi text-ink hover:border-ink hover:bg-ink/[0.08]',
    solid: 'bg-ink font-semibold text-deep hover:bg-amber',
  }[variant];

  return (
    <Link
      href={href}
      aria-current={active ? 'page' : undefined}
      className={`${base} ${styles} ${mono ? 'font-mono text-[11px] uppercase tracking-[0.12em]' : ''}`}
    >
      {children}
    </Link>
  );
}
