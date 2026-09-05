#!/usr/bin/env bash
# RecoverOps — one command to a running system.
#
#   ./scripts/run.sh              start backend + frontend
#   ./scripts/run.sh --seed 3     seed three batches first (fills history and case memory)
#
# First run installs dependencies and trains the uplift models (~30 s, once).
# Everything after that starts in seconds. Ctrl-C stops both halves.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
SEED_BATCHES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --seed) SEED_BATCHES="${2:-3}"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\033[38;5;179m▸\033[0m %s\n' "$*"; }

# ---- backend -----------------------------------------------------------
if [[ ! -d backend/.venv ]]; then
  say "creating backend/.venv"
  python3 -m venv backend/.venv
fi
say "installing backend dependencies"
backend/.venv/bin/pip install -q -r backend/requirements.txt

if [[ ! -f backend/.env ]]; then
  cp backend/.env.example backend/.env
  say "created backend/.env — add Razorpay TEST-MODE keys to use your own data"
fi

if [[ "$SEED_BATCHES" -gt 0 ]]; then
  say "seeding $SEED_BATCHES batches"
  (cd backend && .venv/bin/python -m app.seed --batches "$SEED_BATCHES")
fi

say "starting the backend on :8000"
(cd backend && exec .venv/bin/uvicorn app.main:app --port 8000 --log-level warning) &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM

# First boot trains the models; wait for health rather than racing it.
say "waiting for the backend (first boot trains the uplift models, ~30 s)"
for _ in $(seq 1 240); do
  if curl -sf -m 2 http://localhost:8000/api/health >/dev/null 2>&1; then break; fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then echo "backend exited during startup" >&2; exit 1; fi
  sleep 1
done
curl -s http://localhost:8000/api/health | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"  estimator      {d['estimator']}\")
print(f\"  razorpay keys  {'configured' if d['razorpayLive'] else 'not set — executor and account pull stay labelled mocks'}\")
print(f\"  ledger         {d['store']['batches']} batch(es), {d['store']['auditRows']} audit rows\")
" 2>/dev/null || say "backend did not answer /api/health"

# ---- frontend ----------------------------------------------------------
if [[ ! -d node_modules ]]; then
  say "installing frontend dependencies"
  npm install --silent
fi
if [[ ! -f .env.local ]]; then
  echo 'NEXT_PUBLIC_API_URL=http://localhost:8000' > .env.local
  say "created .env.local pointing at the backend"
fi

say "starting the console on http://localhost:3000"
npm run dev
