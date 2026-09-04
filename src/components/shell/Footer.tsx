import Link from 'next/link';

export function Footer() {
  return (
    <footer className="relative z-10 border-t border-hairline bg-deep px-5 py-10 md:px-10">
      <div className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-5">
        <div className="flex items-center gap-2.5">
          <span
            className="size-5 rounded-full"
            style={{ background: 'radial-gradient(circle at 30% 30%, #e8a552, #c86a3c 70%)' }}
          />
          <span className="display text-[16px] text-ink">RecoverOps</span>
        </div>

        <p className="max-w-[520px] text-[12px] leading-relaxed text-ink-mute">
          Built for a Razorpay buildathon. The evaluation batch is synthetic and labelled as such
          everywhere it appears; the policy citations and the test-mode API calls are real. No
          certifications, no customers, no production traffic — those would all be untrue.
        </p>

        <Link href="/console" className="text-[12px] text-ink-dim hover:text-amber">
          Open the console →
        </Link>
      </div>
    </footer>
  );
}
