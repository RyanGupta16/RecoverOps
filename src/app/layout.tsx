import type { Metadata, Viewport } from 'next';
import { Fraunces, Inter, JetBrains_Mono } from 'next/font/google';
import { Header } from '@/components/shell/Header';
import './globals.css';

// Self-hosted by next/font — no render-blocking request to a font CDN.
const fraunces = Fraunces({
  subsets: ['latin'],
  variable: '--font-fraunces',
  axes: ['opsz'],
  display: 'swap',
});

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono-face',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'RecoverOps — failed-payment recovery that optimises for causal uplift',
  description:
    'An agent for recovering failed payments that ranks by the causal effect of contacting a customer, not by the probability they will pay. Built for the Razorpay buildathon.',
  applicationName: 'RecoverOps',
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: '#0d0a08',
  colorScheme: 'dark',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${fraunces.variable} ${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="min-h-dvh bg-deep text-ink antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[200] focus:rounded-full focus:bg-amber focus:px-5 focus:py-3 focus:text-sm focus:font-semibold focus:text-deep"
        >
          Skip to content
        </a>
        <Header />
        {children}
      </body>
    </html>
  );
}
