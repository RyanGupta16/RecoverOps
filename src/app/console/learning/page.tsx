import { ConsoleHeading } from '@/components/console/ConsoleHeading';
import { LearningPanel } from '@/components/console/LearningPanel';
import { TerminalPanel } from '@/components/console/primitives';
import { DemoModeBadge } from '@/components/ui/primitives';
import { fetchLearningStatus } from '@/lib/api';

export const dynamic = 'force-dynamic';

export default async function LearningPage() {
  const [status, health] = await Promise.all([
    fetchLearningStatus(),
    fetch(`${process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}/api/health`, { cache: 'no-store' })
      .then((r) => (r.ok ? (r.json() as Promise<{ razorpayLive?: boolean }>) : null))
      .catch(() => null),
  ]);

  return (
    <>
      <ConsoleHeading
        title="Learning loop"
        sub="On real data nobody sees the branch not taken. A randomised control arm makes the policy's effect measurable; exploration inside the treatment arm makes per-event uplift learnable. Nothing here is labelled measured until the arms support it."
        aside={<DemoModeBadge source={status ? 'live' : 'sample'} />}
      />

      {status ? (
        <LearningPanel status={status} live={Boolean(health?.razorpayLive)} />
      ) : (
        <TerminalPanel title="Learning loop" meta="backend only">
          <p className="text-[12px] leading-relaxed text-ink-dim">
            The learning loop lives in the backend ledger: arm assignment, outcome attribution and
            retraining all read from it. Start the backend to see the measured policy effect here.
          </p>
        </TerminalPanel>
      )}
    </>
  );
}
