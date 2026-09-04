/**
 * Headless smoke test.
 *
 * Scroll reveals legitimately start at opacity 0, so visibility is only checked
 * AFTER an element has been scrolled into view — otherwise the test flags the
 * feature working correctly as a failure.
 *
 * Usage: node scripts/smoke.mjs [baseUrl]
 */

import puppeteer from 'puppeteer';
import { mkdirSync } from 'node:fs';

const BASE = process.argv[2] ?? 'http://localhost:3100';
const OUT = '/tmp/shots';
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function openPage(browser, width = 1440, height = 900) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: 1 });
  const log = { consoleErrors: [], pageErrors: [], failed: [] };
  page.on('console', (m) => {
    if (m.type() === 'error') log.consoleErrors.push(m.text());
  });
  page.on('pageerror', (e) => log.pageErrors.push(e.message));
  page.on('requestfailed', (r) => {
    // Next prefetches RSC payloads for links it may never navigate to and
    // aborts them; those aborts are not failures.
    const url = r.url();
    if (r.failure()?.errorText === 'net::ERR_ABORTED' && url.includes('_rsc=')) return;
    log.failed.push(`${r.failure()?.errorText} ${url}`);
  });
  return { page, log };
}

/** Scrolls the whole page in viewport steps, letting reveals fire. */
async function scrollThrough(page) {
  const height = await page.evaluate(() => document.body.scrollHeight);
  const step = await page.evaluate(() => window.innerHeight * 0.8);
  for (let y = 0; y < height; y += step) {
    await page.evaluate((yy) => window.scrollTo(0, yy), y);
    await sleep(420);
  }
  await sleep(1200);
}

/** Anything still transparent after it has been scrolled past is a real bug. */
async function findStuckHidden(page) {
  return page.evaluate(() => {
    const stuck = [];
    const els = document.querySelectorAll('.reveal-group > *, .reveal-init');
    for (const el of els) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (r.height === 0) continue;
      if (parseFloat(cs.opacity) < 0.05) {
        stuck.push(
          `${el.tagName.toLowerCase()}.${String(el.className).split(' ').slice(0, 2).join('.')} — "${(el.textContent || '').trim().slice(0, 46)}"`,
        );
      }
    }
    return stuck;
  });
}

function report(label, log, stuck) {
  const bad = log.pageErrors.length + log.consoleErrors.length + log.failed.length + stuck.length;
  console.log(`\n=== ${label} ===`);
  if (log.pageErrors.length) console.log('  PAGE ERRORS:', log.pageErrors.slice(0, 5));
  if (log.consoleErrors.length) console.log('  CONSOLE ERRORS:', log.consoleErrors.slice(0, 5));
  if (log.failed.length) console.log('  FAILED REQUESTS:', log.failed.slice(0, 5));
  if (stuck.length) {
    console.log(`  STUCK INVISIBLE (${stuck.length}):`);
    stuck.slice(0, 10).forEach((s) => console.log('    -', s));
  }
  if (!bad) console.log('  clean');
  return bad;
}

const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
let problems = 0;

/* ---- marketing page, desktop ---- */
{
  const { page, log } = await openPage(browser);
  await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 45000 });
  await sleep(2000);
  await page.screenshot({ path: `${OUT}/home-hero.png` });
  await scrollThrough(page);
  const stuck = await findStuckHidden(page);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(900);
  await page.screenshot({ path: `${OUT}/home-foot.png` });
  problems += report('/ (desktop, after full scroll)', log, stuck);

  // Section-by-section screenshots for a visual pass.
  for (const id of ['problem', 'insight', 'pipeline', 'results', 'policy', 'faq', 'submission']) {
    const found = await page.evaluate((i) => {
      const el = document.getElementById(i);
      if (!el) return false;
      window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 70);
      return true;
    }, id);
    if (found) {
      await sleep(1100);
      await page.screenshot({ path: `${OUT}/sec-${id}.png` });
    } else {
      console.log(`  missing section #${id}`);
      problems += 1;
    }
  }
  await page.close();
}

/* ---- marketing page, 375px ---- */
{
  const { page, log } = await openPage(browser, 375, 780);
  await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 45000 });
  await sleep(1500);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  await scrollThrough(page);
  const stuck = await findStuckHidden(page);
  await page.screenshot({ path: `${OUT}/home-375.png` });
  console.log(
    `\n  375px horizontal overflow: ${overflow}px ${overflow > 1 ? '<-- SCROLLS SIDEWAYS' : 'ok'}`,
  );
  if (overflow > 1) problems += 1;
  problems += report('/ (375px)', log, stuck);
  await page.close();
}

/* ---- console: actually run a batch ---- */
{
  const { page, log } = await openPage(browser);
  await page.goto(BASE + '/console', { waitUntil: 'networkidle2', timeout: 45000 });
  await sleep(1200);

  const btn = await page.$('button');
  await btn?.click();
  await sleep(6000);

  const state = await page.evaluate(() => {
    const logEl = document.querySelector('[role="log"]');
    const counters = [...document.querySelectorAll('dd')].map((d) => d.textContent?.trim());
    return {
      lines: logEl ? logEl.children.length : 0,
      firstLine: logEl?.children[0]?.textContent?.trim().slice(0, 70) ?? '',
      counters: counters.slice(0, 5),
    };
  });
  await page.screenshot({ path: `${OUT}/console-running.png` });
  console.log('\n  batch run →', state.lines, 'log lines; counters:', state.counters.join(' | '));
  if (state.lines < 5) {
    console.log('  BATCH DID NOT STREAM');
    problems += 1;
  }
  problems += report('/console (after clicking Run batch)', log, await findStuckHidden(page));
  await page.close();
}

/* ---- remaining console routes ---- */
for (const [name, path] of [
  ['compare', '/console/compare'],
  ['dogs', '/console/sleeping-dogs'],
  ['exceptions', '/console/exceptions'],
]) {
  const { page, log } = await openPage(browser);
  await page.goto(BASE + path, { waitUntil: 'networkidle2', timeout: 45000 });
  await sleep(1200);
  await scrollThrough(page);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  problems += report(path, log, await findStuckHidden(page));
  await page.close();
}

/* ---- a decision trace ---- */
{
  const { page, log } = await openPage(browser);
  await page.goto(BASE + '/console/exceptions', { waitUntil: 'networkidle2', timeout: 45000 });
  const href = await page.evaluate(
    () => document.querySelector('a[href^="/console/trace/"]')?.getAttribute('href') ?? null,
  );
  if (href) {
    await page.goto(BASE + href, { waitUntil: 'networkidle2', timeout: 45000 });
    await sleep(1500);
    await scrollThrough(page);
    await page.screenshot({ path: `${OUT}/trace.png` });
    problems += report(href, log, await findStuckHidden(page));
  } else {
    console.log('\n  no trace link found on the exception queue');
    problems += 1;
  }
  await page.close();
}

await browser.close();
console.log(`\n${problems ? `${problems} problem(s) found` : 'ALL CLEAN'}`);
