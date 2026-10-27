# Sponsor technology — Recall (Open Agent Hackathon 2026)

Paste-ready notes for HackOS. Every LLM call still goes through `recall/llm/generator.py`; every data read through `recall/data/query_layer.py`.

---

## Zetaris

**What it does in Recall.** Federated read path for handles, interactions, contacts, and events. Unified Semantic Layer namespace `lightning.metastore.recallai.recall` over four FILESTORE CSV tables plus Postgres calendar events via the `calendar_pg` datasource. Two DQ rules compile with the USL. The query layer prefers Zetaris when `DATA_BACKEND=zetaris`, then falls back to DuckDB (circuit breaker). Live smoke is **17/17** on our self-hosted Docker instance (GCP VM): table + USL counts and every named query match DuckDB row-for-row ([output_examples/zetaris_smoke.txt](../output_examples/zetaris_smoke.txt), [docs/zetaris_notes.md](zetaris_notes.md)).

**Exact files.** `recall/data/zetaris_backend.py`, `recall/data/query_layer.py`, `scripts/zetaris_setup_usl.py`, `scripts/zetaris_load_sample.sql`, `scripts/zetaris_smoke.py`, `docs/zetaris_notes.md`.

**How a judge verifies.** Open the checked-in 17/17 transcript, or with a cluster: `DATA_BACKEND=zetaris uv run python scripts/zetaris_smoke.py`. Without a cluster (default sample): traces show `fallback zetaris→duckdb reason=circuit_open` — expected; DuckDB serves the same named queries through the same query layer.

**What was unique.** One semantic layer federating CSV file sources and a Postgres database, with DQ rules, read by agents through a single query layer — smoke asserts USL / tables / DuckDB named-query parity so the agent never cares which backend answered.

---

## Meterless

**What it does in Recall.** World Model (identity merge, conflicts, person context) and H-MEM (mine notes, hybrid retrieve, trust ledger). Persistent SQLite stores are fingerprinted per dataset so switching `SAMPLE_DATA_DIR` re-ingests automatically.

**Exact files.** `recall/engines/world/` (`store.py`, `ingest.py`, `context_builder.py`, …), `recall/engines/hmem/`, ledgers under `logs/<run_id>/` (`world_provenance.jsonl`, `identity_ledger.jsonl`, `memory_ledger.jsonl`, …).

**How a judge verifies.** `uv run python -m recall ingest --sample` then `who Omar` / `note "…"` / `memories Omar`. Open `output_examples/05_memory_carryover/how_it_worked.md` for H-MEM carry-over; `data/synthetic/identity_report.md` for merge quality (53/53 perfect, 0 wrong merges).

**What was unique.** Meterless-shaped services in Python with explainable identity scoring and JSONL evidence, wired into the Writer↔Critic loop — not a thin wrapper. Markovian is unused: we need inspectable multi-run memory, not a single-turn policy.

---

## Cursor

**What it does in Recall.** Project rules (`.cursor/rules/recall.mdc`) enforce generator-only LLM, query-layer-only reads, tracing, sample mode, and bounds. The dated build log records what we asked Cursor and what landed.

**Exact files.** `.cursor/rules/`, `docs/cursor-log.md`.

**How a judge verifies.** Read `docs/cursor-log.md` (hardening pass phases 0–4 with commit hashes) and `.cursor/rules/recall.mdc`.

**What was unique.** Rules as hard gates (not suggestions) plus a reconstructible Ask/Produce/Change log aligned to git history for the submission window.

---

## NVIDIA

**What it does in Recall.** Nemotron 3 Ultra / Super (main vs fast) via `ROLE_MODELS`; Nemotron embeddings for H-MEM; Parakeet ASR over Riva NVCF for voice. Sample mode uses recorded fixtures then offline rules when keys are absent.

**Exact files.** `recall/llm/generator.py`, `recall/config.py` (`ROLE_MODELS`, model slugs), `recall/voice/stt.py`, `data/cache/llm/`.

**How a judge verifies.** `uv run python -m recall llm-test` with keys; without keys, `make demo` / `make examples` still produce briefs via cache → offline rules. Voice: `uv run python -m recall voice --check`.

**What was unique.** One generator façade with role→tier mapping, think-block stripping, and a three-tier fallback (live → cache → offline) so judges get a working agent with zero credentials.
