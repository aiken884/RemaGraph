# Changelog

All notable changes to RemaGraph will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`remagraph_store`** — MCP tool for writing memories with five arbitration rules and model2vec deduplication. Supports three kinds: `task_handoff`, `status_update` (auto-supersede), `discovered_constraint` (invalidate existing).
- **`remagraph_search`** — MCP tool for full-text search via FTS5 BM25 with trigram tokenizer (CJK support). Filterable by kind, status, tags, agent_id, task_id.
- **`remagraph_status`** — MCP tool for querying latest project status; returns active `status_update` memories deduplicated by `task_id`.
- **`remagraph serve`** — stdio MCP server entrypoint via FastMCP. Compatible with Claude Desktop, Cursor, OpenCode, and any stdio MCP client.
- **SQLite + FTS5** storage layer with WAL mode, foreign keys, schema versioning, and migration chain.
- **Audit log** (`audit_log` table) recording all store outcomes for traceability.
- **Five arbitration rules**: empty summary, too short, too long, excessive repetition, missing handoff_note for task_handoff.
- **Environment variable `REMAGRAPH_STATE_DIR`** to customize DB location (default: `~/.local/state/remagraph/`).
- **Engineering baseline**: ruff (lint + format), pytest with coverage ≥80%, mypy, pre-commit hooks (ruff + gitleaks), CI matrix (3.11–3.13), pip-audit.
- **Apache-2.0** license.
- **Comprehensive test suite**: unit tests for models, DB, arbitration, dedup, store, search, audit; smoke tests for MCP stdio round-trip.
