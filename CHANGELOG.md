# Changelog

All notable changes to RemaGraph will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`remagraph_store`** — MCP tool for writing memories with five arbitration rules and model2vec deduplication. Supports three kinds: `task_handoff`, `status_update` (auto-supersede), `discovered_constraint` (invalidate existing).
- **`remagraph_search`** — MCP tool for full-text search via FTS5 BM25 with trigram tokenizer (CJK support). Filterable by kind, status, tags, agent_id, task_id. Short queries (≤2 chars) return empty results with warning path.
- **`remagraph_status`** — MCP tool for querying latest project status; returns active `status_update` memories deduplicated by `task_id`.
- **`remagraph serve`** — stdio MCP server entrypoint via FastMCP. Compatible with Claude Desktop, Cursor, OpenCode, and any stdio MCP client.
- **SQLite + FTS5** storage layer with WAL mode, foreign keys, schema versioning, and migration chain (`tokenize='trigram'`).
- **Audit log** — append-only **`audit.jsonl`** under the state directory (default `~/.local/state/remagraph/`); written after successful store commit (see `docs/audit.md`).
- **Five arbitration rules**: (1) summary length ≥30, (2) learnings non-empty, (3) handoff_note ≥20 for `task_handoff` only, (4) model2vec cosine dedup ≥0.90, (5) agent_id format `^[a-z0-9_-]+$` length 3–64.
- **Dedup model** — `potion-multilingual-128M`; `EMBEDDING_DIM=256` locked by measurement; embeddings stored as float32 little-endian BLOB.
- **Environment variable `REMAGRAPH_STATE_DIR`** to customize state/DB/audit location (default: `~/.local/state/remagraph/`).
- **Engineering baseline**: ruff (lint + format), pytest with coverage ≥80%, mypy as optional dev dependency, pre-commit (ruff + gitleaks), CI matrix (Python 3.11–3.13) with smoke → lint → test, pip-audit, gitleaks, Dependabot, mutmut workflow (non-blocking) for arbitration + dedup.
- **Apache-2.0** license and SPDX headers on `src/remagraph/*.py`.
- **Test suite**: unit tests for models, DB, arbitration, dedup, store, search, audit, server; smoke tests under `tests/smoke/` with isolated temp state.

### Notes

- Package version on `main`: **0.1.0** (not yet published to PyPI; HITL only).
- Implementation closeout: `docs/reviews/v1-closeout-status.md`.
