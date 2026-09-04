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
import { getHeroFeedRows, getSampleBatch } from '@/lib/sample.server';
import type { BatchResult, DataSource } from '@/lib/types';

/**
 * The marketing page tries the backend for its Live Results figures and falls
 * back to the bundled synthetic batch, passing the source down so the badge
 * reflects where the numbers actually came from.
 */
async function loadBatch(): Promise<{ batch: BatchResult; source: DataSource }> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (apiUrl) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      const res = await fetch(`${apiUrl}/api/batch/latest`, {
        signal: controller.signal,
        next: { revalidate: 60 },
      });
      clearTimeout(timer);
      if (res.ok) {
        const live = (await res.json()) as BatchResult;
        return { batch: { ...live, source: 'live' }, source: 'live' };
      }
    } catch {
      /* falls through to the bundled batch */
    }
  }
  return { batch: getSampleBatch(), source: 'sample' };
}

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
