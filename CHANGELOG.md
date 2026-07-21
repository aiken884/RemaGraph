# Changelog

All notable changes to RemaGraph will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (v2 — Phase 1 公開前必做)

- **路徑穿越防禦 (A3)**: `REMAGRAPH_STATE_DIR` 字元正則驗證 + `resolve()` 後禁止系統目錄（`/etc`, `/usr`, `/bin` 等）
- **Rate limiting (A1)**: per-agent thread-safe token bucket（60 calls/60 秒 window），防止濫用與 DoS
- **輸入驗證 (A2)**: `task_id` / `agent_id` 經 Pydantic `@field_validator` 檢核格式 `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`
- **mypy CI gate (D1)**: strict mode 設定（`pyproject.toml`）+ CI 流程加入 `mypy src/`，目前 9 源檔零錯誤
- **Migration 框架 (O5)**: `_migrate_v1_to_v2` 函式啟用，schema 版本 `1→2`
- **CONTRIBUTING.md**: 貢獻者指南（開發環境、測試、程式碼風格、PR 流程、安全考量）
- **PR template**: `.github/PULL_REQUEST_TEMPLATE.md`（checklist 含 ruff/mypy/pytest/CHANGELOG）
- **PyPI publish workflow**: `.github/workflows/publish.yml`（tag `v*` 觸發、trusted publishing、GitHub Release）
- **架構文件**: `docs/architecture.md`（系統圖 + 模組說明 + 資料流）

### Added (v2 — Phase 2 短期補上)

- **Audit rotation (O1)**: `audit.jsonl` → `audit-YYYYMM.jsonl` 按月自動分檔
- **DB 容量上限 (O3)**: `PRAGMA max_page_count` 設定 100MB soft limit
- **Superseded 清理 (O2)**: `arbitration.cleanup_superseded(conn, max_age_days=90)` 清除超期非 active 記錄
- **ADR 0001**: `docs/decisions/0001-v2-plan-and-governance.md` 決策紀錄
- **Dependabot 策略強化**: labels + reviewers 設定

### Security

- `_RateLimiter` 使用 `threading.Lock` 確保原子操作（修復 race condition）
- `ArbitrationReason` literal 型別修復（`XXsummary_too_shortXX` → `summary_too_short`）
- `pathlib.Path.resolve()` + forbidden prefixes 雙層路徑防禦
- PPLX 對抗式審查執行完畢，發現問題已全數修復

### Notes

- CI workflows 因 Actions 額度不足暫停（`disabled_manually`），可透過 GitHub UI 重新啟用
- 包版本 `0.2.0-dev`（尚未 publish PyPI；HITL only）

## [0.1.0] — 2026-07-21

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
