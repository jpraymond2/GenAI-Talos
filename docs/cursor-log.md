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

## 2026-10-24–25 — Phone web app (7b)

- **Asked:** Installable phone UI, hold-to-talk, clarification buttons, `/api/ask` speech + `run_id`, token auth, `/api/status`, faster Zetaris queries.
- **Produced:** `702461f` phone surface; README phone section + `RECALL_API_TOKEN` in `.env.example` (`11b0689`).
- **Changed:** Serve bound to localhost; Tailscale for iPhone; ffmpeg for AAC→WAV.

## 2026-10-26 — Synthetic world + identity report

- **Asked:** Fictional Jordan Lefebvre / Tidepool dataset with answer key and reasoned same-name merging.
- **Produced:** `04bbc13` / merge `2293bd1` — `data/synthetic/`, generator scripts, identity report (53/53 perfect, 0 wrong merges).
- **Changed:** Demo dataset is synthetic; `data/sample` remains the unit-test fixture.

## 2026-10-26 — Mac agent consent gate

- **Asked:** Harden Mac agent for demo: consent gate, no hardcoded URL, size/group guards.
- **Produced:** `45ed442`.
- **Changed:** Dry-run extract refuses until consent; live paths stay gitignored under `data/live/`.

## 2026-10-27 — Submission hardening Phase 0 (`ed9e018`)

- **Asked:** Make `data/synthetic` the demo dataset; per-dataset world/H-MEM DBs + fingerprint re-ingest; owner from `dataset.json`; Zetaris scripts read `sample_data_dir`.
- **Produced:** `ed9e018`.
- **Changed:** Defaults → synthetic tables; tests pin `data/sample`.

## 2026-10-27 — Phase 1 (`9be8803`)

- **Asked:** Freeze sample clock; remove global `"default"` agent-cache fallback; offline rules so sample mode is not canned Karim.
- **Produced:** `9be8803`.
- **Changed:** `time_context.now()`; cache miss → offline rules.

## 2026-10-27 — Phase 1b (`fd9e8eb`)

- **Asked:** Fix offline briefs on synthetic data (A–G polish).
- **Produced:** `fd9e8eb` (+ `c792c67` contact-card org).
- **Changed:** Brief quality without LLM keys.

## 2026-10-27 — Phase 1c (`54119d5`)

- **Asked:** Fix World Model regressions, strip dataset literals, polish briefs.
- **Produced:** `54119d5` (+ `190d5d3` sparse Tom / closed intros).
- **Changed:** `tests/test_no_hardcoding.py` green; no synthetic names in agent code.

## 2026-10-27 — Phase 2 (`a043e41`)

- **Asked:** Local auth, dashboard defaults (no mock banner), `input_examples/` / `output_examples/`, eval harness.
- **Produced:** `a043e41`.
- **Changed:** `make examples` / `make eval`; demo token in `.env.example`.

## 2026-10-27 — Phase 2b (`ffd20be`)

- **Asked:** Meaningful conflicts, resolver intents, clean learned aliases.
- **Produced:** `ffd20be`.
- **Changed:** Lucas Montreal vs Paris surfaces as World Model conflict with sources.

## 2026-10-27 — Phase 3 (`7b088ab`)

- **Asked:** H-MEM notes, critic refetch wiring, agent stubs (resolver/retriever/web_scout/actions), Daniel Kim entity-scoped evidence.
- **Produced:** `7b088ab`.
- **Changed:** Orchestrator honors refetch; `/api/confirm` dry-run path; wedding-invite leak fixed via address+handle_links.

## 2026-10-27 — Phase 3b (`2aa6e18`)

- **Asked:** Critic convergence (best_kept), real round diffs, per-example log isolation, explainable traces + dashboard timeline.
- **Produced:** `2aa6e18`.
- **Changed:** Eval **19/23 (82.6%)**, person accuracy 100%; `06_revision_loop` shows Writer↔Critic.

## 2026-10-27 — Phase 4 (`c48020d`)

- **Asked:** README / SPONSOR_TECH / data README / metadata / cursor-log; quote non-verb promises; untrack `data/relationship_scores` JSON; secret scan PASS.
- **Produced:** `c48020d`.
- **Changed:** Docs match guideline paste-ins; promise template never emits “you'd early stage climate”.

## 2026-10-27 — Phase 4b (deterministic examples / web / actions / LLM_LIVE)

- **Asked:** Per-example fresh H-MEM; web scout on brief retrieve (Payloop); actions intro + open-loop draft; `LLM_LIVE` for Nemotron in sample mode with cache fallback.
- **Produced:** `efb18be`.
- **Changed:** `make examples` twice → identical `brief.md`; Omar brief shows Payloop public news.

## 2026-10-27 — Merge origin/main (Hassan #17–#20)

- **Asked:** Resolve mid-merge conflicts; keep Hassan's workspace UI + our mock/local-token/critic UX; SAMPLE_MODE auto-sign-in; preroute assistant→principal; refresh eval after WhatsApp synthetic regen.
- **Produced:** this merge commit.
- **Changed:** Conflicts in `company_aliases.yaml` (ours) and web App/api/auth/CenterPanel (combined). Eval **18/23 (78.3%)**, person accuracy 100%. Judges skip LoginPage when `/api/local-token` succeeds.
