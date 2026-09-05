# RecoverOps

An agent for recovering failed payments that ranks by **causal uplift** rather than by
recovery probability. This repository holds both halves: the marketing site and
operational console (`src/`, Next.js) and the decision engine (`backend/`, FastAPI).

The distinction is the whole product. A probability model answers *will this customer
pay?* The question that decides whether to spend a message is *does contacting them
change anything?* Those come apart in exactly the places the money is:

| Segment          | Recovers if quiet | Recovers if contacted | What to do                     |
| ---------------- | ----------------- | --------------------- | ------------------------------ |
| **Sure thing**   | yes               | yes                   | retry silently, spend nothing  |
| **Persuadable**  | no                | yes                   | this is the target             |
| **Lost cause**   | no                | no                     | stop early, escalate if large  |
| **Sleeping dog** | yes               | *less often*, and may cancel | do nothing — and log it |

## Running it

```bash
npm install
npm run dev          # http://localhost:3000
```

The site is fully usable with no backend. Every screen that renders bundled data shows a
**Demo Mode — sample data** badge, which disappears the moment a real backend response
comes back.

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000   # in .env.local; enables live mode
```

### Backend

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

First-ever boot trains the uplift models (~30 s) and caches them in
`backend/data/uplift_models.pkl`; every boot after that is instant. A batch run takes
about 300 ms. `GET /api/health` reports the chosen estimator, the offline benchmark, the
retrieval benchmark, and whether Razorpay / Anthropic keys are configured.

Optional keys go in `backend/.env` (see `backend/.env.example`): Razorpay **test-mode**
keys make the executor create real payment links and orders (capped per batch), and an
Anthropic key makes the diagnosis layer's LLM fallback call Claude Haiku for the two
ambiguous reason codes, grounded in BM25 hits from the error corpus. Without keys both
degrade to labelled mocks — the trace says which happened, always.

**The uplift stack is real, not simulated**: five CATE estimators (T-, S-, X-, DR-learner
on `HistGradientBoosting`, plus hand-specified segment priors as the honesty floor) are
trained on a 100k-row simulated randomised experiment and benchmarked on a 20k holdout —
PEHE, Qini, correlation with true tau. The engine ships whichever wins
(`backend/data/uplift_benchmark.json`; currently the X-learner). A paired churn-uplift
model prices what a contact can break, and Agent B ranks by expected net value:
`tau·amount − churn_tau·amount·residual − cost`.

Expected backend endpoints (`src/lib/api.ts` is typed against them):

| Method | Path                            | Used by                                   |
| ------ | ------------------------------- | ----------------------------------------- |
| `POST` | `/api/batch/run`                | console run button                        |
| `GET`  | `/api/batches?limit=`           | history tab                               |
| `GET`  | `/api/batch/stream`             | live SSE decision stream                  |
| `GET`  | `/api/batch/latest`             | landing page, console side panels         |
| `GET`  | `/api/batch/:id/results`        | comparison panel (`?batch=` on any tab)   |
| `GET`  | `/api/batch/:id/sleeping-dogs`  | sleeping dog ledger for a past batch      |
| `GET`  | `/api/batch/:id/exceptions`     | exception queue for a past batch          |
| `GET`  | `/api/events/:eventId/trace`    | decision trace (`?batch_id=` optional)    |
| `GET`  | `/api/sleeping-dogs`            | sleeping dog ledger, latest batch         |
| `GET`  | `/api/exceptions`               | exception queue, latest batch             |
| `GET`  | `/api/audit?limit=&kind=&ref=`  | history tab, audit log tail               |
| `GET`  | `/api/audit/verify`             | history tab, hash-chain verification      |

Every console page asks the backend first and falls back to the bundled synthetic batch,
served from `/api/sample/*` route handlers so the 800 KB dataset never enters the client
bundle. The badge on each page says which one answered.

### Real data

The console's batch runner has a source selector: **Simulator** (seeded, both branches
known), **Razorpay account** (pulls failed payments and pending/halted subscriptions from
your account on the test-mode keys in `backend/.env`), or an **uploaded file** — a Razorpay
payments export as API JSON (`{"items": [...]}`) or the dashboard CSV. Every real payment's
`error_reason` maps onto one of thirteen reason families (`backend/app/taxonomy.py`),
including three a toy simulator never needs: `CUSTOMER_CANCELLED`, `INSTRUMENT_BLOCKED`
(never retried on the same instrument) and `MERCHANT_CONFIG` (`error_source = business` —
zero customer contact, routed to merchant operations). Those are deterministic from the
reason string, so the gate is correct on them from the first real event.

On real data the ranking runs on reason-family priors until the learning loop has real
outcomes to retrain on, and the batch says so (`dataMode: real`, `estimatorMode: priors`).
Engagement and tenure are proxies estimated from the pulled history and are labelled as
such in every trace. Raw contact details never enter the ledger — only a hash.

`backend/merchant.toml` is the onboarding config: budget, thresholds, which channels are
real, message costs by class, contact windows, MSME status, AFA category.

### The ledger

Everything a batch produces is persisted in `backend/data/ledger.db` (SQLite, WAL mode,
migrations keyed on `PRAGMA user_version`): the batch, every decision trace, the case
memory the retrieval layer reads, and an **append-only audit log**. Each audit row carries
the SHA-256 of the previous row's hash plus its own canonical body; `GET /api/audit/verify`
recomputes the chain from genesis and reports the first break. A batch run writes one
`decision` row per event — action, message class, every rule verdict, the execution record,
the outcome — and one `batch.completed` row. A blocked action leaves the same trail as an
executed one, and a restart changes nothing.

First boot runs one batch if the ledger is empty so the site has live data before anyone
presses Run. To seed history and case memory on a fresh clone:

```bash
cd backend && .venv/bin/python -m app.seed --batches 3
```

Backend tests: `cd backend && .venv/bin/python -m pytest tests -q`.

## Scripts

| Command          | What it does                                                          |
| ---------------- | --------------------------------------------------------------------- |
| `npm run dev`    | dev server                                                            |
| `npm run build`  | production build                                                      |
| `npm run gen`    | regenerate the bundled demo batch from the real engine (`python -m app.export_sample`) |
| `npm run frames` | re-extract the background frame sequence from `assets/`               |
| `npm run lint`   | eslint                                                                |
| `npm run format` | prettier                                                              |

## The evaluation batch is synthetic, on purpose

To know which of the four segments a customer belongs to, you would have to both contact
them and not contact them and compare. You can only ever do one. That is the fundamental
problem of causal inference, not a gap in anyone's engineering — a live system never sees
the branch it didn't take.

So the bundled batch is generated with both branches known — by the same engine, gate and
estimators that run live, with a fixed seed (`python -m app.export_sample`), so it is
byte-identical on every run and there is no second implementation of anything. That makes
the comparison in the console *exact* rather than estimated. It also means these are not
measurements taken on real customers, and the site says so everywhere it shows a number.

Real data goes through the identical pipeline (see *Real data* above); on it, no outcome is
known at decision time and every recovered/churned figure reads as pending until the
learning loop attributes it.

What's real: the policy rules and the regulation they cite, the seven-layer pipeline they
gate, and the Razorpay test-mode API calls made by the backend executor.

Both agents in the comparison get the same events, the same contact budget and the same
policy gate. The baseline is not built to lose — it only ranks by a different objective.

The estimators are honest about what is knowable. Segment membership is latent given the
observable features: a sleeping dog and a persuadable with the same reason code and
engagement are indistinguishable to any estimator, and the Bayes-optimal ranking on this
world (measured — posterior over segments × exact per-segment effects) still touches
roughly seven sleeping dogs per 500-event batch. The one feature that separates a dog from
a persuadable is the customer's response the last time they were chased — the signal a
merchant's own dunning history carries. The simulated world includes it as an informative
but noisy history (`prior_nudge_response`), and the batch's honesty block says so; on real
data that feature is `none` until the learning loop has seen outcomes, which is exactly why
the loop exists. Without it the estimators barely beat the priors (Qini 0.164 vs 0.160) and
the comparison collapses to parity; with it the X-learner wins clearly (0.187 vs 0.151).
What uplift ranking buys robustly is where the budget goes — persuadables over sure things
— and a churn-priced value estimate that declines the clearly dangerous contacts. The
budget also has to bind: with a generous budget both policies contact everyone the gate
allows, so `merchant.toml` sets it at 120 per 500-event batch.

## Architecture

```
src/
  app/
    page.tsx                    marketing page (Register A)
    console/                    ops console (Register B), code-split from marketing
      page.tsx                  batch runner + SSE stream log
      compare/                  Agent A vs Agent B on the same batch
      trace/[eventId]/          full decision chain for one event
      sleeping-dogs/            every no-action decision, and why
      exceptions/               unresolved cases with structured reasons
      history/                  every batch on record + the audit chain
    api/sample/                 demo-mode fallback endpoints
  components/
    motion/                     useAnimeScope, Reveal, CountUp
    bg/ScrubBackground.tsx      scroll-scrubbed canvas frame sequence
    marketing/  ui/  shell/  console/
  lib/
    api.ts        typed client with graceful fallback
    batch.server.ts   server loaders: backend first, bundled batch second, source reported
    policy.ts     the twelve named rules + the seven pipeline layers
    sample.server.ts  server-only access to the bundled batch
backend/
  app/
    store.py      SQLite ledger: batches, traces, case memory, hash-chained audit log
    runtime.py    the live layers wired once; the one way to run and persist a batch
    engine.py     batch orchestration over the seven layers
    sim.py  uplift.py  retrieval.py  policy.py  diagnosis.py  executor.py
    seed.py       python -m app.seed — fill history and case memory on a fresh clone
  tests/          pytest
data/             generated — do not hand-edit
assets/           source video for the background
public/frames/    generated frame sequence
```

### Two registers, one brand

**Register A** (marketing) is editorial: Fraunces headlines, glassmorphic cards, generous
space. **Register B** (console) is operational: monospace, dense tables, PASS/BLOCK
badges, no decoration that isn't carrying information. Both are built from the same
tokens in `globals.css`, so moving from the pitch to the product reads as one product.

### Animation

Anime.js v4 is the only animation engine in the bundle — scroll reveals, count-ups, the
SVG quadrant and pipeline set-pieces, the chart line-draw, the FAQ accordion, the console's
ticking counters, and the background scrub all run through it. There are no CSS
`transition`/`@keyframes` used for component motion and no second animation library.

Every animated component goes through `useAnimeScope` (`src/components/motion/`), which
wraps `createScope({ root, mediaQueries })` and **always calls `revert()` on unmount**.
That isn't polish: without it, React Strict Mode's double-invoke in development and
ordinary unmounts in production both leak running animations and leftover inline styles,
which shows up as motion that compounds the longer a demo session runs.

`prefers-reduced-motion` is read inside the same scope (`self.matches.reduceMotion`), so
accessibility is decided where the animation is defined rather than bolted on after.

### The scroll-scrub background

The background is a pre-extracted JPEG frame sequence drawn to a canvas, scrubbed by
scroll position across the first 70% of the document, then handed off to solid ground for
the policy table, FAQ and submission sections. The console has no video behind it.

Scrubbing a `<video>` by setting `currentTime` stutters badly: seeking to a non-keyframe
forces the decoder back to the previous I-frame and re-decodes forward on every scroll
tick. A frame sequence turns a scrub into an array lookup and one `drawImage`.

Frames stream in coarse-to-fine, so the scrub is roughly right early rather than sharp
only at the top, and the nearest loaded frame is drawn while the rest arrive. If frames
fail to load entirely, the page keeps its flat background — nothing depends on this
rendering.

The source clip is cyan-dominant and the brand is amber on near-black, so the canvas sits
under a warm grade plus a scrim that deepens as the scrub advances, keeping cream-on-dark
text legible over it.

## Before submitting

`src/components/marketing/Submission.tsx` still has clearly marked placeholders for the
team name, members, contact email and demo video URL. They are marked rather than filled
with plausible-looking values. The repository URL is filled in.

## What is and isn't committed

`data/*.json` (the bundled demo batch and traces), `assets/agent-background.mp4` and the
extracted `public/frames/` sequence are committed, so a fresh clone renders correctly
without running the macOS-only frame extractor. The backend's trained models
(`uplift_models.pkl`) and its SQLite ledger (`ledger.db`) are not — the models are rebuilt
on first boot (~30 s, once) and the ledger fills from the first batch.
