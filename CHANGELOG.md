# Changelog

All notable changes to RemaGraph will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> 發行前準備階段更新（真實運作中，暫不公開發布）。

### Added
- **fleet_member kind**：新增 "fleet_member"，由 tower 擁有完整 record/recycle 支援。supersede 邏輯擴展至 fleet_member。schema v4 migration 自動處理。
- **LightCommander / AcpRouter 強化整合**：dispatch_with_memory.py 中 recall/store 成為 before/after prompt hooks 的 mandatory 路徑；統一 _recall/_store 呼叫；新增 record_fleet_member / recycle_fleet_member。
- 支援 project_id 透傳 cross-space ack 驗證。
- DB 自動維護機制（WAL、prune、FTS optimize、vacuum、integrity + safety valves）。

### Changed
- **herdr 整合層級澄清**：PPLX Priority B 完成：recall/store 在所有 ACP 派工路徑強制；MemoryDispatcher / hooks 統一。
- 所有相關文件已對齊（dispatch_with_memory.py、README、task-memory-convention.md、DESIGN.md）。
- 跨專案溝通持續使用 ACP 直接協調；fleet 管理由 tower 透過 RemaGraph 記錄。
- 發行準備文件更新：反映 Herdr Bridge 真實運作現況，暫不 tag 發布。

## [0.2.0-alpha] — 2026-07-22（內部 Alpha 測試版）

> **注意**：此版本僅供內部使用，尚未對外發布 PyPI。僅用於 herdr Bridge 內部測試與獨立 headless agent 測試。

### Added

#### 極簡任務記憶 / headless CLI
- **`remagraph init`**：一行初始化專案記憶目錄，並產生可 `source` 的 `env.sh`
- **`remagraph auto`**：一鍵 recall → 執行指令 → 自動 store（非技術使用者主入口）
  - 新增 `--recall-only`：指揮塔派工前可只讀取記憶、不執行、不儲存
- **`remagraph store` / `search` / `status`**：CLI 子命令，JSON 輸出到 stdout（argparse、零新依賴）；MCP 模式維持 `remagraph serve`
- **`search` 支援只帶 `--task-id`**（不必 `--query`），方便任務軌跡回顧
- **極簡包裝腳本**：`examples/simple/remagraph-task.sh`
- **herdr Bridge 範例**：`examples/herdr-bridge/dispatch_with_memory.py`、`simple-memory-helper.sh`
- **白話慣例文件**：`docs/task-memory-convention.md`
- **內部測試 Playbook**：`docs/internal/alpha-test-playbook.md`（含測試場景、命名規則、回饋模板）
- **指揮塔自動化提示詞**：`docs/internal/指揮塔自動化提示詞.md`（供另一 Agent 實作）
- **一鍵安裝腳本**：`scripts/one-key-install.sh`
- init 與 quickstart 大幅強化非技術使用者與 herdr 使用說明

#### 安全 / 治理 / 可靠度（v2 Phase 1–2）
- **路徑穿越防禦 (A3)**：`REMAGRAPH_STATE_DIR` 字元正則驗證 + `resolve()` 後禁止系統目錄
- **Rate limiting (A1)**：per-agent thread-safe token bucket（60 calls/60 秒）
- **輸入驗證 (A2)**：`task_id` / `agent_id` 經 Pydantic `@field_validator` 檢核
- **mypy CI gate (D1)**：strict mode + CI `mypy src/`
- **Migration 框架 (O5)**：schema 版本 `1→2`
- **Audit rotation (O1)**：`audit.jsonl` → `audit-YYYYMM.jsonl` 按月分檔
- **DB 容量上限 (O3)**：`PRAGMA max_page_count` 100MB soft limit
- **Superseded 清理 (O2)**：`cleanup_superseded(conn, max_age_days=90)`
- **CONTRIBUTING.md**、**PR template**、**PyPI publish workflow**（tag `v*`）、**ADR 0001**、**架構文件**

### Security
- `_RateLimiter` 使用 `threading.Lock` 確保原子操作
- `pathlib.Path.resolve()` + forbidden prefixes 雙層路徑防禦
- PPLX 對抗式審查執行完畢，發現問題已全數修復

### Notes
- CI workflows 可能因 Actions 額度暫停；本地 gate：`ruff` / `mypy` / `pytest`（≥224 tests）
- PyPI 發布需 HITL：確認後打 `v0.2.0` tag 觸發 `.github/workflows/publish.yml`
- `task_id` / `agent_id` 僅允許英數與 `_`/`-`（agent_id 另須小寫、長度 3–64）

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

[Unreleased]: https://github.com/aiken884/RemaGraph/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/aiken884/RemaGraph/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/aiken884/RemaGraph/releases/tag/v0.1.0
