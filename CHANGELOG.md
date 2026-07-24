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

#### 跨專案版本相容性 + 標籤搜尋（PPLX 架構改善計畫，schema v4→v6）
- **前向相容 `_meta` 欄位（schema v4→v5）**：新增 `min_reader_version` / `min_writer_version` / `upgrade_hint`，讓資料庫本身攜帶升級指引，不再只依賴消費端當時執行的那份舊程式碼字串常數；降級拒絕訊息改為防禦性讀取並附加顯示資料庫內建的 `upgrade_hint`（全新資料庫與既有 v4→v5 升級路徑皆會種下這三個欄位）。
- **三層版本相容性判斷 + 唯讀降級取代全面阻斷**：`db.connect()` 依上述兩個版本欄位拆出完全相容／唯讀降級／完全拒絕三層。唯讀降級時連線不再拋出例外——`remagraph_search` / `remagraph_status` 完全不受影響，只有 `remagraph_store` 會在最前面被拒絕（`status="rejected"`, `reason="read_only_mode"`），不進入 transaction；自動維護（`light_maintenance_on_connect()` / `remagraph_maintain`）也會偵測同一個唯讀標記並跳過所有寫入操作（WAL checkpoint/prune/FTS optimize/VACUUM/ANALYZE/完整性檢查），即使呼叫端要求 `force=True` 也一律跳過，並記一筆 `maintenance_skipped_read_only` audit 事件。
- **`remagraph_status` / CLI `status` 擴充為版本相容性 handshake**：回應新增 `server_code_version` / `db_schema_version` / `min_reader_version` / `min_writer_version` / `upgrade_hint` / `read_only` 欄位（`db.get_compat_status()`），讓呼叫端能在真正嘗試寫入、撞牆失敗之前就先掌握相容性等級。
- **跨專案共用登記表（`project_registry`）**：新增 `db.list_known_projects()` / `db.connect_foreign_project_readonly()`，記錄已知的 `project_id` 及各自的 state_dir，作為跨專案查詢的地基；`maintenance.resolve_project_state_dir()` 每次解析都會 best-effort 自動登記，無需顯式呼叫。唯讀跨專案連線使用 SQLite URI `mode=ro` + `PRAGMA query_only=1`，避免 TOCTOU 競態下意外生出空白新資料庫。
- **`memory_labels` 共享標籤表（schema v5→v6）+ `remagraph_search` 的 `cross_project_label` 參數**：`remagraph_store` 新增 `labels` 參數，支援命名空間化標籤（`namespace:value`，例如 `dep:opencode`、`topic:auth`、`kind:bug`；長度上限 64 字元；格式不符整批拒絕，`reason="invalid_label"`），與既有自由格式的 `tags` 完全獨立。`remagraph_search` 的 `cross_project_label` 透過 `project_registry` 逐一唯讀查詢「目前專案 + 所有已知專案」，合併結果並標註 `source_project_id`；fan-out 上限 20 個「其他」已知專案，超過上限時回應標記 `cross_project_fanout_capped: true`，不悄悄截斷佯裝完整。

### Changed
- **herdr 整合層級澄清**：PPLX Priority B 完成：recall/store 在所有 ACP 派工路徑強制；MemoryDispatcher / hooks 統一。
- 所有相關文件已對齊（dispatch_with_memory.py、README、task-memory-convention.md、DESIGN.md）。
- 跨專案溝通持續使用 ACP 直接協調；fleet 管理由 tower 透過 RemaGraph 記錄。
- 發行準備文件更新：反映 Herdr Bridge 真實運作現況，暫不 tag 發布。

### Fixed
- **`remagraph_search` / `remagraph_status` 回傳結果漏欄位**：`search._row_to_result()` 先前只組裝 `id`/`project_id`/`summary`/`agent_id`/`kind`/`task_id`/`timestamp`/`score` 8 個欄位，遺漏 `handoff_note`/`learnings`/`tags`/`status`/`created_at`/`updated_at`，即使資料庫裡確實存在。已補齊，且 `get_status()` 改為重用修好後的 `_row_to_result()`，避免同一類欄位遺漏日後在兩處各自重複發生。
- **CLI `store` / `search` / `status` 補上 `_get_conn()` 例外處理**：與 MCP 版本（`server.py`）對齊，版本相容性三層判斷中的完全拒絕情境下，CLI 不再顯示原始 `MigrationError` traceback，改為印出乾淨的錯誤訊息並以非零狀態碼結束。
- **`light_maintenance_on_connect` 內部連線未受唯讀降級標記管控**：`run_maintenance()` 內部另開的維護連線先前不知道外層連線是否已被標記唯讀；現在一取得連線（不論呼叫端傳入或內部自行開啟）就立刻檢查該標記，唯讀時完全不執行任何寫入。

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
