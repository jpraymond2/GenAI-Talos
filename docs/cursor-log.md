# Cursor build log — Recall

Dated notes reconstructed from `git log --stat`. Each step: what we asked Cursor, what it produced, what we changed.

## 2026-10-22 — Scaffold + generator

- **Asked:** Bootstrap the hackathon Python service (uv, FastAPI, Typer, sample mode).
- **Produced:** `4462071` project layout; `adefdc9` / `606470a` NVIDIA-only `recall/llm/generator.py` with sample cache; `7e1c072` schemas + fixture evidence.
- **Changed:** Kept all LLM calls behind `generate` / `generate_json`; no provider SDKs elsewhere.

## 2026-10-22 — 2.2 sample data + 2.3 query layer

- **Asked:** Synthetic CSVs under `data/sample` and a single query layer (DuckDB, Zetaris fallback later).
- **Produced:** `50b780e` DuckDB backend, named queries, `scripts/make_sample_data.py`, sample CSVs; `413581f` tracer wiring for queries.
- **Changed:** Agents must not open source files directly — only `recall/data/query_layer.py`.

## 2026-10-22–23 — Agent brain (JP)

- **Asked (JP):** Router / writer / critic + orchestrator loop.
- **Produced:** `7fdadc9` agents + critic JSON fix; later merges on `agent-brain` (`eb7b8e0`).
- **Changed:** Orchestrator / agents stay JP-owned; others call public `run` only.

## 2026-10-23 — 2.4 live connectors

- **Asked:** Gmail, Calendar, WhatsApp, iMessage → parquet + `ingest --live`.
- **Produced:** `f54fdea` `recall/connectors/*` and live ingest path.
- **Changed:** Live data stays under `data/live/` (gitignored); sample mode never needs credentials.

## 2026-10-23 — 3.1 World Model

- **Asked:** Identity merge, conflicts, open loops, `recall who`.
- **Produced:** `acb8b42` `recall/engines/world` + CLI `who`.
- **Changed:** World DB paths `data/world_sample.db` / `data/world.db`.

## 2026-10-23 — 3.2 H-MEM

- **Asked:** Hierarchical memory — mine notes, hybrid retrieve, ledger, CLI `note` / `memories`.
- **Produced:** `4bccf4b` `recall/engines/hmem`.
- **Changed:** Empty world must reject note/memories with ingest instructions.

## 2026-10-23 — Zetaris live + USL

- **Asked:** Real REST backend (not stubs); USL “recall” + DQ rules; smoke script.
- **Produced:** `2092927` `zetaris_backend` / SQL render / smoke; `93ddcef` USL setup script + read-through semantic layer; notes in `docs/zetaris_notes.md`.
- **Changed:** `DATA_BACKEND=zetaris` with DuckDB fallback; Windows needs Docker/WSL for the platform.

## 2026-10-23 — LinkedIn via Gmail + identity scoring (JP)

- **Asked (JP):** LinkedIn evidence through Gmail; explainable identity scoring.
- **Produced:** `f469909` LinkedIn path; `9d2e3c1` scoring configs + identity score files.
- **Changed:** Later ignored `data/identity_scores/*` runtime output; kept scoring code.

## 2026-10-23 — 8 dashboard

- **Asked:** Brief cards, source chips, live `/ws/trace`, Person/Memory tabs, mock mode.
- **Produced:** `4715187` Vite/React `web/` served from FastAPI when `web/dist` exists.
- **Changed:** `VITE_MOCK` for offline UI; live API wired later on `feature/ship-pack`.

## 2026-10-23–24 — 7 voice

- **Asked:** Parakeet STT (Riva NVCF), TTS, push-to-talk CLI, `POST /api/voice`, dashboard hold-to-talk, `voice --check`.
- **Produced:** `ad98a90` `recall/voice/*`; merge `33a1257`.
- **Changed:** Credential warnings for swapped API key / function id; sample STT returns a fixed demo line.

## 2026-10-24 — CLI restore + test isolation

- **Asked:** JP merge had dropped `who` / `note` / `memories` / `ingest --live`; restore while keeping identity CLI; tests must not hit live Zetaris from `.env`.
- **Produced:** `de99b35` CLI restore, `tzdata`, `tests/conftest.py` forcing `DATA_BACKEND=duckdb`.
- **Changed:** Autouse fixture clears settings + query-layer caches every test.

## 2026-10-24 — Ship pack (this branch)

- **Asked:** Live dashboard API (`/api/ask`, `/api/confirm`, `/api/person`, `/api/memories`), `metadata.json`, cursor-log, README rewrite, identity_scores gitignore, CSV-driven smoke counts, `scripts/secret_scan.py`.
- **Produced:** Server routes + web client wiring, docs, hygiene scripts (this commit series).
- **Changed:** Confirm returns `not_implemented` until actions confirm/execute exists; Person/Memory tabs call the GET endpoints when the backend is up.
