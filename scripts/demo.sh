#!/usr/bin/env bash
# One-command local demo: sync, env, ingest, build web, serve.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

uv sync
if [[ ! -f .env ]]; then
  cp -n .env.example .env || cp .env.example .env
fi
export SAMPLE_MODE=true
uv run python -m recall ingest --sample
(
  cd web
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
  npm run build
)
exec uv run python -m recall serve
