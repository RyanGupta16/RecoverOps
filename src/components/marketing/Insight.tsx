import { Reveal } from '@/components/motion/Reveal';
import { Section, SectionHead } from '@/components/ui/primitives';
import { QuadrantDiagram } from './QuadrantDiagram';

export function Insight() {
  return (
    <Section id="insight">
      <SectionHead
        eyebrow="The insight"
        title={
          <>
            Four customers. One
            <br />
            failed payment each.
          </>
        }
        lede="Split by what contact actually does, failed payments fall into four groups. Every dunning system in production treats the first three as one group and cannot see the fourth at all."
      />

      <QuadrantDiagram />

      <Reveal className="mt-12 grid gap-5 md:grid-cols-2" variant="fade-up">
        <div className="rounded-[var(--radius-card)] border border-hairline bg-glass p-6 backdrop-blur-xl md:p-7">
          <h3 className="display text-[21px] text-ink">Why nobody measures this</h3>
          <p className="mt-3 text-[14px] leading-[1.65] text-ink-dim">
            To know which group a customer is in you would have to contact them and not contact
            them, and compare. You can only ever do one. This is the fundamental problem of causal
            inference, and it is not a gap in anyone&rsquo;s engineering — it is a property of
            reality. A live system never sees the branch it did not take.
          </p>
        </div>
        <div className="rounded-[var(--radius-card)] border border-amber/30 bg-amber/[0.06] p-6 backdrop-blur-xl md:p-7">
          <h3 className="display text-[21px] text-ink">Why we can show it anyway</h3>
          <p className="mt-3 text-[14px] leading-[1.65] text-ink-dim">
            Our evaluation batch is synthetic, and both branches are written into it. That is the
            reason it is synthetic — not convenience. It means the comparison you can run in the
            console is exact rather than estimated. It also means these are not measurements taken
            on real customers, and we do not present them as if they were.
          </p>
        </div>
      </Reveal>
    </Section>
  );
}
