import type { Metadata } from 'next';
import { ConsoleTabs } from '@/components/console/ConsoleTabs';

export const metadata: Metadata = {
  title: 'RecoverOps console',
  description:
    'Run a batch of failed payments, watch the agent decide in real time, and compare it against a probability-threshold baseline on the same events.',
};

/**
 * Register B shell. Flat background — no scroll-scrub video behind dense data
 * tables — and the code for the console is split away from the marketing
 * bundle by living under its own route segment.
 */
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-dvh bg-deep">
      <main id="main" className="mx-auto max-w-[1320px] px-4 pb-16 pt-24 md:px-8">
        <ConsoleTabs />
        {children}
      </main>
    </div>
  );
}
