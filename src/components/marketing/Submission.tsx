import { Reveal } from '@/components/motion/Reveal';
import { ButtonLink, EyebrowLabel, Section } from '@/components/ui/primitives';

/**
 * Placeholders here are marked, not invented. Team name, members, contact
 * address, repo URL and video URL are the submitter's to fill in — writing
 * plausible-looking values would be exactly the kind of fabrication the rest of
 * this site is built to avoid.
 */
const PLACEHOLDER = 'TODO — fill in before submitting';

const REPO_URL = 'https://github.com/RyanGupta16/RecoverOps';

const LINKS = [
  { label: 'GitHub repository', value: REPO_URL, href: REPO_URL },
  { label: 'Demo video', value: PLACEHOLDER, href: null },
  { label: 'Live console', value: '/console', href: '/console' },
];

const STACK = [
  ['Frontend', 'Next.js App Router, TypeScript, Tailwind CSS'],
  ['Motion', 'Anime.js v4 — the only animation engine in the bundle'],
  ['Charts', 'Recharts, with the line-draw layered on by Anime.js'],
  ['Backend', 'Python, FastAPI, Server-Sent Events for batch progress'],
  ['Payments', 'Razorpay SDK, test mode — orders, payment links, subscription retries'],
  ['Evaluation', 'Seeded synthetic batch generator with known causal ground truth'],
];

export function Submission() {
  return (
    <Section id="submission" tone="grounded">
      <Reveal variant="rise">
        <div className="rounded-[var(--radius-card-lg)] border border-hairline-hi bg-glass-hi p-7 backdrop-blur-xl md:p-12">
          <div className="grid gap-10 lg:grid-cols-[1fr_1fr] lg:gap-14">
            <div>
              <EyebrowLabel>Submission</EyebrowLabel>
              <h2 className="display mt-4 text-[clamp(30px,4vw,46px)] text-ink">
                Read the code,
                <br />
                then run the batch.
              </h2>
              <p className="mt-5 max-w-[460px] text-[15px] leading-[1.66] text-ink-dim">
                The console is not a mockup of the product — it is the product, running the same
                policy gate and the same ledger the pipeline writes to. Run a batch and every
                decision on screen is inspectable down to the rule that produced it.
              </p>

              <div className="mt-8 flex flex-wrap gap-3.5">
                <ButtonLink href="/console">Run a batch</ButtonLink>
                <ButtonLink href="/console/compare" variant="ghost">
                  See the comparison
                </ButtonLink>
              </div>

              <dl className="mt-10 space-y-4">
                {LINKS.map((link) => (
                  <div key={link.label} className="border-t border-hairline pt-4">
                    <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-mute">
                      {link.label}
                    </dt>
                    <dd className="mt-1.5 text-[14px]">
                      {link.href ? (
                        <a
                          href={link.href}
                          className="text-amber underline-offset-4 hover:underline"
                        >
                          {link.value}
                        </a>
                      ) : (
                        <span className="rounded bg-rust/[0.16] px-2 py-1 font-mono text-[12px] text-[#e08b60]">
                          {link.value}
                        </span>
                      )}
                    </dd>
                  </div>
                ))}
                <div className="border-t border-hairline pt-4">
                  <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-mute">
                    Team
                  </dt>
                  <dd className="mt-1.5">
                    <span className="rounded bg-rust/[0.16] px-2 py-1 font-mono text-[12px] text-[#e08b60]">
                      {PLACEHOLDER} — team name, members, contact email
                    </span>
                  </dd>
                </div>
              </dl>
            </div>

            <div>
              <h3 className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-mute">
                Stack
              </h3>
              <dl className="mt-4 space-y-3.5">
                {STACK.map(([label, value]) => (
                  <div
                    key={label}
                    className="grid grid-cols-[92px_1fr] gap-4 border-b border-hairline pb-3.5"
                  >
                    <dt className="font-mono text-[11px] uppercase tracking-[0.1em] text-brass">
                      {label}
                    </dt>
                    <dd className="text-[13.5px] leading-snug text-ink-dim">{value}</dd>
                  </div>
                ))}
              </dl>

              <div className="mt-7 rounded-[14px] border border-hairline bg-glass p-5">
                <h4 className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-mute">
                  Regenerating the batch
                </h4>
                <p className="mt-2.5 text-[13px] leading-relaxed text-ink-dim">
                  The evaluation batch is produced by a seeded generator, so it is byte-identical on
                  every run and every claim on this site can be traced back to code rather than to a
                  screenshot.
                </p>
                <code className="mt-3 block rounded bg-deep/60 px-3 py-2 font-mono text-[12px] text-amber">
                  npm run gen
                </code>
              </div>
            </div>
          </div>
        </div>
      </Reveal>
    </Section>
  );
}
