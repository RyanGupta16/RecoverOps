import { ScrubBackground } from '@/components/bg/ScrubBackground';
import { Capabilities } from '@/components/marketing/Capabilities';
import { Faq } from '@/components/marketing/Faq';
import { Hero } from '@/components/marketing/Hero';
import { HowItWorks } from '@/components/marketing/HowItWorks';
import { Insight } from '@/components/marketing/Insight';
import { LiveResults } from '@/components/marketing/LiveResults';
import { PolicySection } from '@/components/marketing/PolicySection';
import { Problem } from '@/components/marketing/Problem';
import { Submission } from '@/components/marketing/Submission';
import { Footer } from '@/components/shell/Footer';
import { loadBatch } from '@/lib/batch.server';
import { getHeroFeedRows } from '@/lib/sample.server';

/**
 * The marketing page tries the backend for its Live Results figures and falls
 * back to the bundled synthetic batch, passing the source down so the badge
 * reflects where the numbers actually came from. Same loader as the console,
 * so the pitch and the product can never disagree about which batch is latest.
 */
export const dynamic = 'force-dynamic';

export default async function HomePage() {
  const feed = getHeroFeedRows();
  const { batch, source } = await loadBatch();

  return (
    <>
      {/*
        The scrub runs across the first 70% of the document. Past that the page
        hands off to solid ground for the dense material — a policy table and a
        judge FAQ do not want a moving image behind them.
      */}
      <ScrubBackground scrubExtent={0.7} />

      <main id="main" className="relative z-10">
        <Hero feed={feed} />
        <Problem />
        <Insight />
        <HowItWorks batch={batch} />
        <Capabilities />
        <LiveResults batch={batch} source={source} />
        <PolicySection />
        <Faq />
        <Submission />
      </main>

      <Footer />
    </>
  );
}
