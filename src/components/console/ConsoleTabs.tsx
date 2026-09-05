'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const TABS = [
  { href: '/console', label: 'Batch' },
  { href: '/console/compare', label: 'Compare' },
  { href: '/console/sleeping-dogs', label: 'Sleeping dogs' },
  { href: '/console/exceptions', label: 'Exceptions' },
  { href: '/console/degradation', label: 'Degradation' },
  { href: '/console/promises', label: 'Promises' },
  { href: '/console/history', label: 'History' },
  { href: '/console/learning', label: 'Learning' },
];

/**
 * The header's console links only appear at large widths. Without this, a judge
 * on a phone can reach the batch runner and nothing else — so the console
 * carries its own scrollable tab strip below that breakpoint.
 */
export function ConsoleTabs() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Console sections"
      className="-mx-4 mb-5 overflow-x-auto hide-scrollbar px-4 lg:hidden"
    >
      <ul className="flex w-max gap-2">
        {TABS.map((tab) => {
          const active = pathname === tab.href;
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={active ? 'page' : undefined}
                className={`inline-flex whitespace-nowrap rounded-full border px-3.5 py-2 font-mono text-[11px] uppercase tracking-[0.12em] ${
                  active
                    ? 'border-amber/45 bg-amber/[0.12] text-amber'
                    : 'border-hairline text-ink-dim'
                }`}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
