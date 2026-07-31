# Changelog

## English

All notable changes to RemaGraph will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

### [0.4.0-beta] - 2026-07-31

> **Note**: This release marks RemaGraph's move from alpha to **beta**, and the **first time this repository itself is made public** — until now it only ran internally as part of another project's internal tooling. This is **not** a 1.0 release: per [`BOUNDARIES.md`](./BOUNDARIES.md), pre-1.0 still means no frozen public API, and "beta" here means the feature set has stabilized enough for wider testing, not that MCP tool parameters or CLI subcommands are locked. The PyPI package itself is still not published — install via the git tag below (`uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.4.0-beta`).

#### Changed
- **Full pre-publication review cycle**: a dedicated documentation and hygiene pass ahead of making the repo public. Highlights:
  - Filled in missing README/DESIGN documentation for all 5 MCP tools (some had gaps in parameter tables and response fields).
  - `remagraph_migrate_project` was previously a stub that didn't actually migrate anything; it now performs a real migration, sharing the same core implementation as the CLI's `migrate-project` subcommand, plus a follow-up fix for a per-record atomicity gap.
  - All user-facing runtime strings across the project (CLI, core server/db/maintenance/arbitration modules, hooks installer, dedup, package metadata) were translated from Chinese to English, so RemaGraph now defaults to English at runtime.
  - A broader sweep caught and corrected several stale facts and small inconsistencies left over from earlier releases (outdated version references, leftover internal-only wording, and similar).

### [0.3.1-alpha] - 2026-07-25 (internal alpha)

> **Note**: This release is for internal use only and has not been published to PyPI. See the "Installation" section of the README for setup (`uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.3.1-alpha`).

#### Fixed
- **Security: `project_id` never actually drove `state_dir` resolution, so the safety valve never fired.** Real production incident: a real, actively-used project's actual project directory was mistakenly connected to and continuously written/maintained by a different project's `remagraph serve` process — the audit log accumulated 24,974 `maintenance_completed` events but only 294 genuine `remagraph_store` events, and the `memories` table got pruned down to single digits. Root cause: neither the CLI subcommands nor `remagraph serve` passed the caller's actual `project_id` through to `_db.connect()`, so the built-in `safety_validate_project()` safety valve was never invoked. Which physical SQLite file got connected to depended entirely on the process's ambient `REMAGRAPH_STATE_DIR`/`REMAGRAPH_PROJECT` environment variables at the time, completely decoupled from any explicitly supplied `--project`/`project_id`. Fixed per the PPLX architecture-review consensus: the CLI now passes `project_id` through to activate the existing safety valve; `remagraph serve` now enforces binding at startup — exactly one of `--project` / `REMAGRAPH_PROJECT` is required, and the process fails fast (never enters the MCP loop) if both are missing — and any tool call carrying a mismatched `project_id` is rejected outright. Dynamic multi-project routing within a single process is deliberately not supported (explicitly rejected by PPLX; see DESIGN.md for the rationale). Two rounds of independent adversarial review turned up and fixed further issues: the safety valve's core comparison logic was itself tautological — `resolve_project_state_dir()` echoed the env value back verbatim whenever the env var was already set, making the comparison always evaluate to False, so the real incident scenario would never have been caught — it is now wired to the existing `db.validate_project_metadata()` to perform genuine identity verification against `project.json`; a bug where `_record_violation`'s best-effort violation logging itself wrote into the victim directory; a liveness-check failure mode for "directory deleted externally" (POSIX unlinked-inode semantics); and a gap where the existing top-level CLI guard (`8edb739e`) correctly blocked the write but left no audit trail behind.
- **Fan-out cap truncation was indistinguishable from an empty result.** When `cross_project_label`/`include_related` hit the candidate-project cap (previously hardcoded at 20), the response came back as `results: []` plus `cross_project_fanout_capped: true`, but the exit code stayed 0 — a caller that only inspects `results` could easily read that as "no memory found." The cap is now configurable (`--fanout-cap` / `REMAGRAPH_FANOUT_CAP`, default raised to 50, hard ceiling of 200 unless raised further via `REMAGRAPH_FANOUT_HARD_CAP` — no unlimited escape hatch is provided), the response now includes `candidates_total` / `candidates_searched` / `candidates_skipped` (`total == searched + skipped` always holds), and the CLI now exits with code 2 on truncation (distinct from 0 = complete, 1 = genuine error). While fixing this, also found and fixed a bug in `_cross_project_fanout()` where results were duplicated whenever the caller's own project happened to be physically the same SQLite file as an already-registered project — it now compares physical paths via `PRAGMA database_list` instead of comparing `project_id` strings alone.

### [0.3.0-alpha] - 2026-07-25 (internal alpha)

> **Note**: This release is for internal use only and has not been published to PyPI. See the "Installation" section of the README for setup (`uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.3.0-alpha`).

#### Added
- **`fleet_member` kind**: new `"fleet_member"` kind, fully owned by the tower with record/recycle support. Supersede logic extended to cover `fleet_member`. Handled automatically by the schema v4 migration.
- **LightCommander / AcpRouter integration hardening**: in `dispatch_with_memory.py`, recall/store are now mandatory before/after-prompt hooks; `_recall`/`_store` calls unified; added `record_fleet_member` / `recycle_fleet_member`.
- Support for `project_id` passthrough for cross-space ack verification.
- DB auto-maintenance mechanism (WAL, prune, FTS optimize, vacuum, integrity checks + safety valves).

##### Cross-project version compatibility + label search (PPLX architecture improvement plan, schema v4→v6)
- **Forward-compatible `_meta` fields (schema v4→v5)**: added `min_reader_version` / `min_writer_version` / `upgrade_hint`, so the database itself now carries its own upgrade guidance instead of relying solely on whatever hardcoded string constants happen to live in the consuming code at the time; the downgrade-rejection message now defensively reads and appends the database's own built-in `upgrade_hint` (seeded on both brand-new databases and existing v4→v5 upgrade paths).
- **Three-tier version-compatibility check + read-only degradation instead of blanket blocking**: `db.connect()` now uses the two version fields above to split behavior into fully compatible / read-only degraded / fully rejected. In read-only degraded mode the connection no longer raises — `remagraph_search` / `remagraph_status` are completely unaffected, only `remagraph_store` is rejected up front (`status="rejected"`, `reason="read_only_mode"`) before ever entering a transaction. Auto-maintenance (`light_maintenance_on_connect()` / `remagraph_maintain`) detects the same read-only flag and skips every write operation (WAL checkpoint/prune/FTS optimize/VACUUM/ANALYZE/integrity check), even when the caller passes `force=True`, and logs a `maintenance_skipped_read_only` audit event.
- **`remagraph_status` / CLI `status` expanded into a version-compatibility handshake**: the response now includes `server_code_version` / `db_schema_version` / `min_reader_version` / `min_writer_version` / `upgrade_hint` / `read_only` fields (via `db.get_compat_status()`), letting callers learn the compatibility tier before attempting a write and hitting a wall.
- **Cross-project shared registry (`project_registry`)**: added `db.list_known_projects()` / `db.connect_foreign_project_readonly()`, recording known `project_id`s and their respective state_dirs as the foundation for cross-project queries; `maintenance.resolve_project_state_dir()` now best-effort auto-registers on every resolution, with no explicit call required. Read-only cross-project connections use the SQLite URI `mode=ro` + `PRAGMA query_only=1` to avoid accidentally spawning a blank new database under a TOCTOU race.
- **`memory_labels` shared label table (schema v5→v6) + `cross_project_label` parameter on `remagraph_search`**: `remagraph_store` gained a `labels` parameter supporting namespaced labels (`namespace:value`, e.g. `dep:opencode`, `topic:auth`, `kind:bug`; 64-char max; a malformed label rejects the whole batch with `reason="invalid_label"`), fully independent from the existing free-form `tags`. `remagraph_search`'s `cross_project_label` queries "the current project plus every known project" read-only via `project_registry`, merges the results, and tags each one with `source_project_id`; fan-out is capped at 20 "other" known projects, and the response flags `cross_project_fanout_capped: true` when the cap is hit rather than silently truncating and pretending completeness.
- **`project_edges` relationship table + `recall_related` cross-project traversal**: added four edge types — `depends_on`/`sibling`/`shares_upstream`/`monorepo_member` — accessible via `db.declare_project_edge()`/`get_project_edges()`; `db.recall_related(project_id, hops)` performs an undirected, cycle-safe BFS traversal. `remagraph_search`/CLI search gained `include_related`/`related_hops` parameters, and the CLI gained a `remagraph link` subcommand to declare edges. Cross-project fan-out shares the same `_cross_project_fanout()` mechanism with `cross_project_label` (item above) rather than duplicating it.
- **`remagraph install-hooks [--global] [--force]`**: wraps the "auto-write commit summaries back into RemaGraph" git post-commit hook into a bundled CLI subcommand — any project with remagraph installed can enable it with a single command, no manual file copying required. Covers conflict detection (an existing non-remagraph hook is rejected by default; `--force` backs it up and then overwrites), correct symlink detection and backup (backs up the symlink itself rather than following it), correct installation into the main repo from a linked worktree, correct handling of `core.hooksPath` (including relative paths and existing third-party setups such as husky), and `--global` using git's native `init.templateDir` so future newly created repos automatically pick up the hook.
- **`REMAGRAPH_HOME` environment variable**: lets external subprocess consumers (e.g. integration tests driving the actually-installed remagraph CLI) also isolate where the shared `project_registry`/`project_edges` registries land, closing a gap that was previously only coverable via Python-level monkeypatching (limited to a single process); independent of, and non-interfering with, the existing `REMAGRAPH_STATE_DIR` (a single project's own state dir).

#### Changed
- **Downstream integration tier clarified**: PPLX Priority B complete: recall/store enforced across every ACP dispatch path; MemoryDispatcher / hooks unified.
- All related docs aligned (`dispatch_with_memory.py`, README, `task-memory-convention.md`, DESIGN.md).
- Cross-project communication continues to go through direct ACP coordination; fleet management is recorded by the tower via RemaGraph.
- Release-readiness docs updated to reflect the real-world state of downstream integration operations; no tag cut yet.

#### Fixed
- **`remagraph_search` / `remagraph_status` responses were missing fields**: `search._row_to_result()` previously assembled only 8 fields — `id`/`project_id`/`summary`/`agent_id`/`kind`/`task_id`/`timestamp`/`score` — omitting `handoff_note`/`learnings`/`tags`/`status`/`created_at`/`updated_at` even though they existed in the database. Now included, and `get_status()` reuses the fixed `_row_to_result()` so the same class of field omission can't recur independently in two places.
- **CLI `store` / `search` / `status` were missing `_get_conn()` exception handling**: brought in line with the MCP path (`server.py`) — in the fully-rejected tier of the three-way version-compatibility check, the CLI no longer dumps a raw `MigrationError` traceback, and instead prints a clean error message and exits non-zero.
- **`light_maintenance_on_connect`'s internal connection ignored the read-only degradation flag**: the separate maintenance connection opened internally by `run_maintenance()` previously had no way of knowing whether the outer connection had already been flagged read-only; it now checks the flag immediately upon obtaining a connection (whether caller-supplied or internally opened) and performs no writes whatsoever when read-only.
- **Genuine legacy v1 databases crashed inside `connect()`**: `connect()` previously called `_init_schema()` unconditionally before `_run_migrations()`, so a genuine v1 database (where the `project_id` column doesn't yet exist) crashed directly with `sqlite3.OperationalError: no such column: project_id` while `_init_schema()` was building an index — the migration chain never got a chance to run. Fixed by swapping the call order; in the process, also caught and fixed a data-corruption bug that had never actually been exercised in practice: `_migrate_v3_to_v4` moves rows via `INSERT INTO memories_new SELECT * FROM memories`, a purely positional column mapping, while `_migrate_v2_to_v3`'s `ALTER TABLE ADD COLUMN` appends new columns at the end of the table — any ordering mismatch between the two would silently shift all the data into the wrong columns.
- **Test suite leaked writes into the real `~/.local/state/remagraph/` `project_registry` table**: `resolve_project_state_dir()` auto-calls `register_known_project()`, which unconditionally used the real `DEFAULT_STATE_DIR` module constant, unaffected by any test's `REMAGRAPH_STATE_DIR`/`tmp_path` isolation. Fixed by adding an autouse fixture in `tests/conftest.py` that closes this off for every test.
- **Empty-string queries incorrectly hit the FTS5 trigram short-query empty-result path**: `search_memories()` previously took the short-query empty-result branch whenever given an empty (or whitespace-only) query, returning no results — even with matching records in the database — if no other filters were supplied. An empty string is now always treated as "list recent memories" rather than entering the full-text-search short-circuit path; the case where sanitization empties the query afterward (the original query was pure special characters) is unchanged.
- **Bundled post-commit hook misjudged `learnings` as empty for root commits and merge commits**: `git diff-tree --no-commit-id --name-only -r HEAD` returns empty for both a repo's very first commit (no parent) and a merge commit (standard git behavior), which triggered the existing fallback that stuffed placeholder text into `learnings`. Fixed by adding `--root` to handle the root-commit case, and using `-m` plus `sort -u` for merge commits (`--first-parent` doesn't work with `diff-tree`, and `-c`/`--cc` print nothing for a clean, conflict-free merge — neither approach was viable).

### [0.2.0-alpha] - 2026-07-22 (internal alpha)

> **Note**: This release is for internal use only and has not been published to PyPI. Used solely for internal downstream integration testing and standalone headless-agent testing.

#### Added

##### Minimal task memory / headless CLI
- **`remagraph init`**: one-line project memory directory setup, generating a sourceable `env.sh`
- **`remagraph auto`**: one-shot recall → run command → auto store (primary entry point for non-technical users)
  - added **`--recall-only`**: lets a tower read memory before dispatch without executing or storing anything
- **`remagraph store` / `search` / `status`**: CLI subcommands, JSON to stdout (argparse, zero new dependencies); MCP mode remains `remagraph serve`
- **`search`** now supports passing **only `--task-id`** (no `--query` required), for reviewing a task's trajectory
- **Minimal wrapper script**: `examples/simple/remagraph-task.sh`
- **Downstream integration examples**: `examples/herdr-bridge/dispatch_with_memory.py`, `simple-memory-helper.sh`
- **Plain-language conventions doc**: `docs/task-memory-convention.md`
- **Internal test playbook**: `docs/internal/alpha-test-playbook.md` (test scenarios, naming rules, feedback template)
- **Tower automation prompt doc**: `docs/internal/指揮塔自動化提示詞.md` (for another agent's implementation)
- **One-key install script**: `scripts/one-key-install.sh`
- init and quickstart substantially expanded with guidance for non-technical users and downstream integration usage

##### Security / governance / reliability (v2 Phase 1-2)
- **Path traversal defense (A3)**: `REMAGRAPH_STATE_DIR` regex validation + forbidden-system-directory check after `resolve()`
- **Rate limiting (A1)**: per-agent thread-safe token bucket (60 calls/60s)
- **Input validation (A2)**: `task_id` / `agent_id` checked via Pydantic `@field_validator`
- **mypy CI gate (D1)**: strict mode + CI `mypy src/`
- **Migration framework (O5)**: schema version `1→2`
- **Audit rotation (O1)**: `audit.jsonl` → `audit-YYYYMM.jsonl`, monthly rollover
- **DB size cap (O3)**: `PRAGMA max_page_count` 100MB soft limit
- **Superseded cleanup (O2)**: `cleanup_superseded(conn, max_age_days=90)`
- **CONTRIBUTING.md**, **PR template**, **PyPI publish workflow** (tag `v*`), **ADR 0001**, **architecture docs**

#### Security
- `_RateLimiter` uses `threading.Lock` to guarantee atomic operations
- Dual-layer path defense: `pathlib.Path.resolve()` + forbidden-prefix checks
- PPLX adversarial review completed; all findings fixed

#### Notes
- CI workflows may be paused due to Actions quota limits; local gate: `ruff` / `mypy` / `pytest` (≥224 tests)
- PyPI publish requires HITL sign-off: confirm, then tag `v0.2.0` to trigger `.github/workflows/publish.yml`
- `task_id` / `agent_id` allow only alphanumerics plus `_`/`-` (`agent_id` additionally must be lowercase, length 3-64)

### [0.1.0] - 2026-07-21

#### Added

- **`remagraph_store`** — MCP tool for writing memories with five arbitration rules and model2vec deduplication. Supports three kinds: `task_handoff`, `status_update` (auto-supersede), `discovered_constraint` (invalidate existing).
- **`remagraph_search`** — MCP tool for full-text search via FTS5 BM25 with trigram tokenizer (CJK support). Filterable by kind, status, tags, agent_id, task_id. Short queries (≤2 chars) return empty results with warning path.
- **`remagraph_status`** — MCP tool for querying latest project status; returns active `status_update` memories deduplicated by `task_id`.
- **`remagraph serve`** — stdio MCP server entrypoint via FastMCP. Compatible with Claude Desktop, Cursor, OpenCode, and any stdio MCP client.
- **SQLite + FTS5** storage layer with WAL mode, foreign keys, schema versioning, and migration chain (`tokenize='trigram'`).
- **Audit log** — append-only **`audit.jsonl`** under the state directory (default `~/.local/state/remagraph/`); written after successful store commit (see `docs/audit.md`).
- **Five arbitration rules**: (1) summary length ≥30, (2) learnings non-empty, (3) handoff_note ≥20 for `task_handoff` only, (4) model2vec cosine dedup ≥0.90, (5) agent_id format `^[a-z0-9_-]+$` length 3-64.
- **Dedup model** — `potion-multilingual-128M`; `EMBEDDING_DIM=256` locked by measurement; embeddings stored as float32 little-endian BLOB.
- **Environment variable `REMAGRAPH_STATE_DIR`** to customize state/DB/audit location (default: `~/.local/state/remagraph/`).
- **Engineering baseline**: ruff (lint + format), pytest with coverage ≥80%, mypy as optional dev dependency, pre-commit (ruff + gitleaks), CI matrix (Python 3.11-3.13) with smoke → lint → test, pip-audit, gitleaks, Dependabot, mutmut workflow (non-blocking) for arbitration + dedup.
- **Apache-2.0** license and SPDX headers on `src/remagraph/*.py`.
- **Test suite**: unit tests for models, DB, arbitration, dedup, store, search, audit, server; smoke tests under `tests/smoke/` with isolated temp state.

[Unreleased]: https://github.com/aiken884/RemaGraph/compare/v0.4.0-beta...HEAD
[0.4.0-beta]: https://github.com/aiken884/RemaGraph/compare/v0.3.1-alpha...v0.4.0-beta
[0.3.1-alpha]: https://github.com/aiken884/RemaGraph/compare/v0.3.0-alpha...v0.3.1-alpha
[0.3.0-alpha]: https://github.com/aiken884/RemaGraph/compare/v0.2.0-alpha...v0.3.0-alpha
[0.2.0-alpha]: https://github.com/aiken884/RemaGraph/compare/v0.1.0...v0.2.0-alpha
[0.1.0]: https://github.com/aiken884/RemaGraph/releases/tag/v0.1.0

## 繁體中文

本檔案記錄 RemaGraph 所有重大變更。

格式依循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
版本號依循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

### [0.4.0-beta] — 2026-07-31

> **注意**：此版本是 RemaGraph 從 alpha 進入 **beta** 的里程碑，也是**這個 repo 本身第一次對外公開**——在此之前僅作為其他專案內部工具的一部分真實運作使用。這**不是** 1.0 發行：依 [`BOUNDARIES.md`](./BOUNDARIES.md)，pre-1.0 期間依然沒有凍結的公開 API，這裡的「beta」指的是功能集已相對穩定、可開始更廣泛測試，不代表 MCP tool 參數或 CLI 子指令已經鎖定不變。PyPI 套件本身仍未發布——安裝方式請透過下方 git tag（`uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.4.0-beta`）。

#### Changed
- **一次完整的發行前審查週期**：正式公開前的文件與清理專項審查，重點如下：
  - 補齊 README/DESIGN 中 5 個 MCP tool 原本缺漏的文件內容（部分參數表與回應欄位先前有缺口）。
  - `remagraph_migrate_project` 先前是空殼、實際上不會真的遷移任何資料，現在已改為真正的實作，與 CLI `migrate-project` 子指令共用同一套核心邏輯，並補上後續發現的單筆遷移原子性缺口修復。
  - 全專案使用者可見的執行期訊息（CLI、核心 server/db/maintenance/arbitration 模組、hooks installer、dedup、套件 metadata）已從中文全面改為英文，RemaGraph 執行期預設語言現在是英文。
  - 更大範圍的檢視另外抓到並修正了幾處前幾版遺留下來的過時事實與小處不一致（過時版本號引用、殘留的「僅限內部」措辭等）。

### [0.3.1-alpha] — 2026-07-25（內部 Alpha 測試版）

> **注意**：此版本僅供內部使用，尚未對外發布 PyPI。安裝方式見 README「安裝」章節（`uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.3.1-alpha`）。

#### Fixed
- **安全性：`project_id` 未真正驅動 state_dir 解析，安全閥從未被觸發**：真實生產事故——一個真實使用中的專案目錄被另一個專案的 `remagraph serve` 行程誤連並持續寫入/維護，audit log 累積 24974 次 `maintenance_completed` 但只有 294 次真正的 `remagraph_store`，`memories` 表被清到只剩個位數。根因：CLI 各子命令與 `remagraph serve` 呼叫 `_db.connect()` 時皆未傳入呼叫端實際拿到的 `project_id`，導致內建的 `safety_validate_project()` 安全閥從未被觸發，實際連到哪個實體 SQLite 檔案只看 process 環境當下的 `REMAGRAPH_STATE_DIR`/`REMAGRAPH_PROJECT`，與明確傳入的 `--project`/`project_id` 完全脫鉤。依 PPLX 架構審查共識修復：CLI 補傳 `project_id` 啟用既有安全閥；`remagraph serve` 新增啟動時強制綁定（`--project`/`REMAGRAPH_PROJECT` 二擇一，皆缺席即 fail-fast，不進入 MCP 迴圈），任何 tool call 帶不同 `project_id` 一律拒絕；刻意不支援單一 process 動態多專案路由（PPLX 明確否決，理由見 DESIGN.md）。兩輪獨立對抗式審查發現並修復：安全閥核心比較邏輯原是套套邏輯（`resolve_project_state_dir()` 在 env 已設定時逐字回傳該值，導致比較恆為 False，真實事故情境完全沒被擋下）——已接上既有的 `db.validate_project_metadata()` 讀取 `project.json` 做真正身分比對；`_record_violation` 的 best-effort 違規記錄本身誤寫進受害目錄的問題；liveness check 對「目錄被外部刪除」場景失效的問題（POSIX unlinked-inode 語意）；CLI 頂層既有守衛（`8edb739e`）正確擋下寫入但未留 audit trail 的缺口。
- **fan-out cap 截斷語意誤讀為空結果**：`cross_project_label`/`include_related` 達到候選專案上限（原寫死 20）時，回應 `results: []` + `cross_project_fanout_capped: true` 但 exit code 仍是 0，容易被只看 `results` 的呼叫端誤判為「查無此記憶」。改為 cap 可設定（`--fanout-cap`/`REMAGRAPH_FANOUT_CAP`，預設提高為 50，硬上限 200，`REMAGRAPH_FANOUT_HARD_CAP` 才可再提高，不提供無上限逃生口），回應新增 `candidates_total`/`candidates_searched`/`candidates_skipped`（`total == searched + skipped` 恆成立），CLI 於截斷時 exit code 改為 2（有別於 0=完整、1=真正錯誤）。修復過程一併發現並修好 `_cross_project_fanout()` 對「呼叫端自己與某個已註冊專案物理上是同一份 SQLite 檔案」時重複回傳結果的 bug（改用 `PRAGMA database_list` 取得實體路徑比對，而非僅比對 `project_id` 字串）。

### [0.3.0-alpha] — 2026-07-25（內部 Alpha 測試版）

> **注意**：此版本僅供內部使用，尚未對外發布 PyPI。安裝方式見 README「安裝」章節（`uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.3.0-alpha`）。

#### Added
- **fleet_member kind**：新增 "fleet_member"，由 tower 擁有完整 record/recycle 支援。supersede 邏輯擴展至 fleet_member。schema v4 migration 自動處理。
- **LightCommander / AcpRouter 強化整合**：dispatch_with_memory.py 中 recall/store 成為 before/after prompt hooks 的 mandatory 路徑；統一 _recall/_store 呼叫；新增 record_fleet_member / recycle_fleet_member。
- 支援 project_id 透傳 cross-space ack 驗證。
- DB 自動維護機制（WAL、prune、FTS optimize、vacuum、integrity + safety valves）。

##### 跨專案版本相容性 + 標籤搜尋（PPLX 架構改善計畫，schema v4→v6）
- **前向相容 `_meta` 欄位（schema v4→v5）**：新增 `min_reader_version` / `min_writer_version` / `upgrade_hint`，讓資料庫本身攜帶升級指引，不再只依賴消費端當時執行的那份舊程式碼字串常數；降級拒絕訊息改為防禦性讀取並附加顯示資料庫內建的 `upgrade_hint`（全新資料庫與既有 v4→v5 升級路徑皆會種下這三個欄位）。
- **三層版本相容性判斷 + 唯讀降級取代全面阻斷**：`db.connect()` 依上述兩個版本欄位拆出完全相容／唯讀降級／完全拒絕三層。唯讀降級時連線不再拋出例外——`remagraph_search` / `remagraph_status` 完全不受影響，只有 `remagraph_store` 會在最前面被拒絕（`status="rejected"`, `reason="read_only_mode"`），不進入 transaction；自動維護（`light_maintenance_on_connect()` / `remagraph_maintain`）也會偵測同一個唯讀標記並跳過所有寫入操作（WAL checkpoint/prune/FTS optimize/VACUUM/ANALYZE/完整性檢查），即使呼叫端要求 `force=True` 也一律跳過，並記一筆 `maintenance_skipped_read_only` audit 事件。
- **`remagraph_status` / CLI `status` 擴充為版本相容性 handshake**：回應新增 `server_code_version` / `db_schema_version` / `min_reader_version` / `min_writer_version` / `upgrade_hint` / `read_only` 欄位（`db.get_compat_status()`），讓呼叫端能在真正嘗試寫入、撞牆失敗之前就先掌握相容性等級。
- **跨專案共用登記表（`project_registry`）**：新增 `db.list_known_projects()` / `db.connect_foreign_project_readonly()`，記錄已知的 `project_id` 及各自的 state_dir，作為跨專案查詢的地基；`maintenance.resolve_project_state_dir()` 每次解析都會 best-effort 自動登記，無需顯式呼叫。唯讀跨專案連線使用 SQLite URI `mode=ro` + `PRAGMA query_only=1`，避免 TOCTOU 競態下意外生出空白新資料庫。
- **`memory_labels` 共享標籤表（schema v5→v6）+ `remagraph_search` 的 `cross_project_label` 參數**：`remagraph_store` 新增 `labels` 參數，支援命名空間化標籤（`namespace:value`，例如 `dep:opencode`、`topic:auth`、`kind:bug`；長度上限 64 字元；格式不符整批拒絕，`reason="invalid_label"`），與既有自由格式的 `tags` 完全獨立。`remagraph_search` 的 `cross_project_label` 透過 `project_registry` 逐一唯讀查詢「目前專案 + 所有已知專案」，合併結果並標註 `source_project_id`；fan-out 上限 20 個「其他」已知專案，超過上限時回應標記 `cross_project_fanout_capped: true`，不悄悄截斷佯裝完整。
- **`project_edges` 關聯表 + `recall_related` 跨專案追溯**：新增 `depends_on`/`sibling`/`shares_upstream`/`monorepo_member` 四種關聯型別，`db.declare_project_edge()`/`get_project_edges()` 存取，`db.recall_related(project_id, hops)` 做 BFS 追溯（無向、防環）。`remagraph_search`/CLI search 新增 `include_related`/`related_hops` 參數，CLI 新增 `remagraph link` 子指令宣告關聯。跨專案 fan-out 邏輯與 `cross_project_label`（item 4b）共用同一套 `_cross_project_fanout()` 機制，不重複實作。
- **`remagraph install-hooks [--global] [--force]`**：把「commit 自動把摘要寫回 RemaGraph」的 git post-commit hook 包裝成套件自帶的 CLI 子指令，任何裝了 remagraph 的專案一行指令即可啟用，不必手動複製檔案。涵蓋衝突偵測（既有非 remagraph hook 預設拒絕覆蓋、`--force` 才備份+覆蓋）、symlink 偵測與正確備份（備份符號連結本身，不 follow）、linked worktree 下正確裝到主 repo、`core.hooksPath`（含相對路徑、既有第三方設定如 husky）正確處理、`--global` 透過 git 原生 `init.templateDir` 讓之後新建立的 repo 自動帶有此 hook。
- **`REMAGRAPH_HOME` 環境變數**：讓外部 subprocess 消費端（例如透過真正安裝的 remagraph CLI 做整合測試）也能隔離共用 `project_registry`/`project_edges` 登記表的落地位置，補齊過去只有 Python 層級 monkeypatch（僅限同一 process 內）才能隔離的缺口，與既有 `REMAGRAPH_STATE_DIR`（單一專案自己的 state dir）是獨立、互不干擾的兩個機制。

#### Changed
- **下游整合層級澄清**：PPLX Priority B 完成：recall/store 在所有 ACP 派工路徑強制；MemoryDispatcher / hooks 統一。
- 所有相關文件已對齊（dispatch_with_memory.py、README、task-memory-convention.md、DESIGN.md）。
- 跨專案溝通持續使用 ACP 直接協調；fleet 管理由 tower 透過 RemaGraph 記錄。
- 發行準備文件更新：反映下游整合真實運作現況，暫不 tag 發布。

#### Fixed
- **`remagraph_search` / `remagraph_status` 回傳結果漏欄位**：`search._row_to_result()` 先前只組裝 `id`/`project_id`/`summary`/`agent_id`/`kind`/`task_id`/`timestamp`/`score` 8 個欄位，遺漏 `handoff_note`/`learnings`/`tags`/`status`/`created_at`/`updated_at`，即使資料庫裡確實存在。已補齊，且 `get_status()` 改為重用修好後的 `_row_to_result()`，避免同一類欄位遺漏日後在兩處各自重複發生。
- **CLI `store` / `search` / `status` 補上 `_get_conn()` 例外處理**：與 MCP 版本（`server.py`）對齊，版本相容性三層判斷中的完全拒絕情境下，CLI 不再顯示原始 `MigrationError` traceback，改為印出乾淨的錯誤訊息並以非零狀態碼結束。
- **`light_maintenance_on_connect` 內部連線未受唯讀降級標記管控**：`run_maintenance()` 內部另開的維護連線先前不知道外層連線是否已被標記唯讀；現在一取得連線（不論呼叫端傳入或內部自行開啟）就立刻檢查該標記，唯讀時完全不執行任何寫入。
- **真正的 v1 舊資料庫在 `connect()` 崩潰**：`connect()` 過去無條件先呼叫 `_init_schema()` 才呼叫 `_run_migrations()`，對貨真價實的 v1 資料庫（`project_id` 欄位尚不存在）會在 `_init_schema()` 內建索引時直接以 `sqlite3.OperationalError: no such column: project_id` 崩潰，migration chain 永遠沒機會執行。已對調兩者呼叫順序修復；過程中另外抓到並修復一個從未被真正跑過的資料損毀 bug：`_migrate_v3_to_v4` 用 `INSERT INTO memories_new SELECT * FROM memories` 純位置對應搬資料，但 `_migrate_v2_to_v3` 的 `ALTER TABLE ADD COLUMN` 把新欄位加在表最後面，順序不一致會讓資料整組錯位。
- **測試套件洩漏寫入真實 `~/.local/state/remagraph/` 的 `project_registry` 表**：`resolve_project_state_dir()` 會自動呼叫 `register_known_project()`，而該函式一律直接使用真實的 `DEFAULT_STATE_DIR` 模組常數，不受任何測試的 `REMAGRAPH_STATE_DIR`/`tmp_path` 隔離影響。已在 `tests/conftest.py` 加入 autouse fixture 統一堵住。
- **空字串 query 誤觸發 FTS5 trigram 短查詢空結果**：`search_memories()` 收到空字串（或僅空白）query 時，先前會命中短查詢空路徑、若無其他過濾條件則直接回傳空結果，即使資料庫內有記錄。已改為空字串一律視為「列出最近記憶」，不進入全文檢索短路徑；sanitize 後才變空（純特殊字元）的情況維持原行為不變。
- **bundled post-commit hook 對 root commit/merge commit 誤判 `learnings` 為空**：`git diff-tree --no-commit-id --name-only -r HEAD` 對 repo 的第一個 commit（無父提交）與 merge commit 皆回傳空結果（標準 git 行為），觸發既有 fallback 讓 `learnings` 誤植為佔位文字。已加 `--root` 處理 root commit，並改用 `-m` + `sort -u` 處理 merge commit（`--first-parent` 對 `diff-tree` 無效、`-c`/`--cc` 對無衝突的乾淨 merge 會印不出內容，皆不適用）。

### [0.2.0-alpha] — 2026-07-22（內部 Alpha 測試版）

> **注意**：此版本僅供內部使用，尚未對外發布 PyPI。僅用於下游整合內部測試與獨立 headless agent 測試。

#### Added

##### 極簡任務記憶 / headless CLI
- **`remagraph init`**：一行初始化專案記憶目錄，並產生可 `source` 的 `env.sh`
- **`remagraph auto`**：一鍵 recall → 執行指令 → 自動 store（非技術使用者主入口）
  - 新增 `--recall-only`：指揮塔派工前可只讀取記憶、不執行、不儲存
- **`remagraph store` / `search` / `status`**：CLI 子命令，JSON 輸出到 stdout（argparse、零新依賴）；MCP 模式維持 `remagraph serve`
- **`search` 支援只帶 `--task-id`**（不必 `--query`），方便任務軌跡回顧
- **極簡包裝腳本**：`examples/simple/remagraph-task.sh`
- **下游整合範例**：`examples/herdr-bridge/dispatch_with_memory.py`、`simple-memory-helper.sh`
- **白話慣例文件**：`docs/task-memory-convention.md`
- **內部測試 Playbook**：`docs/internal/alpha-test-playbook.md`（含測試場景、命名規則、回饋模板）
- **指揮塔自動化提示詞**：`docs/internal/指揮塔自動化提示詞.md`（供另一 Agent 實作）
- **一鍵安裝腳本**：`scripts/one-key-install.sh`
- init 與 quickstart 大幅強化非技術使用者與下游整合使用說明

##### 安全 / 治理 / 可靠度（v2 Phase 1–2）
- **路徑穿越防禦 (A3)**：`REMAGRAPH_STATE_DIR` 字元正則驗證 + `resolve()` 後禁止系統目錄
- **Rate limiting (A1)**：per-agent thread-safe token bucket（60 calls/60 秒）
- **輸入驗證 (A2)**：`task_id` / `agent_id` 經 Pydantic `@field_validator` 檢核
- **mypy CI gate (D1)**：strict mode + CI `mypy src/`
- **Migration 框架 (O5)**：schema 版本 `1→2`
- **Audit rotation (O1)**：`audit.jsonl` → `audit-YYYYMM.jsonl` 按月分檔
- **DB 容量上限 (O3)**：`PRAGMA max_page_count` 100MB soft limit
- **Superseded 清理 (O2)**：`cleanup_superseded(conn, max_age_days=90)`
- **CONTRIBUTING.md**、**PR template**、**PyPI publish workflow**（tag `v*`）、**ADR 0001**、**架構文件**

#### Security
- `_RateLimiter` 使用 `threading.Lock` 確保原子操作
- `pathlib.Path.resolve()` + forbidden prefixes 雙層路徑防禦
- PPLX 對抗式審查執行完畢，發現問題已全數修復

#### Notes
- CI workflows 可能因 Actions 額度暫停；本地 gate：`ruff` / `mypy` / `pytest`（≥224 tests）
- PyPI 發布需 HITL：確認後打 `v0.2.0` tag 觸發 `.github/workflows/publish.yml`
- `task_id` / `agent_id` 僅允許英數與 `_`/`-`（agent_id 另須小寫、長度 3–64）

### [0.1.0] — 2026-07-21

#### Added

- **`remagraph_store`**：MCP tool，用於寫入記憶，具備五項仲裁規則與 model2vec 去重。支援三種 kind：`task_handoff`、`status_update`（自動 supersede）、`discovered_constraint`（使既有記憶失效）。
- **`remagraph_search`**：MCP tool，透過 FTS5 BM25 + trigram tokenizer（支援中日韓文）做全文搜尋。可依 kind、status、tags、agent_id、task_id 過濾。短查詢（≤2 字元）回傳空結果並附警告。
- **`remagraph_status`**：MCP tool，查詢專案最新狀態；回傳依 `task_id` 去重後的 active `status_update` 記憶。
- **`remagraph serve`**：透過 FastMCP 提供的 stdio MCP server 進入點。相容 Claude Desktop、Cursor、OpenCode 及任何 stdio MCP client。
- **SQLite + FTS5** 儲存層，具備 WAL 模式、外鍵、schema 版本管理與 migration chain（`tokenize='trigram'`）。
- **Audit log**——state 目錄下（預設 `~/.local/state/remagraph/`）的 append-only **`audit.jsonl`**；於 store 成功 commit 後寫入（詳見 `docs/audit.md`）。
- **五項仲裁規則**：(1) summary 長度 ≥30、(2) learnings 不可為空、(3) `task_handoff` 專屬 handoff_note ≥20、(4) model2vec cosine 去重 ≥0.90、(5) agent_id 格式 `^[a-z0-9_-]+$` 長度 3–64。
- **去重模型**——`potion-multilingual-128M`；`EMBEDDING_DIM=256` 經實測鎖定；embedding 以 float32 little-endian BLOB 儲存。
- **環境變數 `REMAGRAPH_STATE_DIR`**：自訂 state/DB/audit 存放位置（預設：`~/.local/state/remagraph/`）。
- **工程基礎**：ruff（lint + format）、pytest 搭配覆蓋率 ≥80%、mypy 為選用開發依賴、pre-commit（ruff + gitleaks）、CI matrix（Python 3.11–3.13）含 smoke → lint → test、pip-audit、gitleaks、Dependabot、mutmut workflow（非阻斷）針對仲裁 + 去重邏輯。
- **Apache-2.0** 授權與 `src/remagraph/*.py` 的 SPDX headers。
- **測試套件**：models、DB、arbitration、dedup、store、search、audit、server 的單元測試；`tests/smoke/` 下的獨立 temp state smoke tests。

[Unreleased]: https://github.com/aiken884/RemaGraph/compare/v0.4.0-beta...HEAD
[0.4.0-beta]: https://github.com/aiken884/RemaGraph/compare/v0.3.1-alpha...v0.4.0-beta
[0.3.1-alpha]: https://github.com/aiken884/RemaGraph/compare/v0.3.0-alpha...v0.3.1-alpha
[0.3.0-alpha]: https://github.com/aiken884/RemaGraph/compare/v0.2.0-alpha...v0.3.0-alpha
[0.2.0-alpha]: https://github.com/aiken884/RemaGraph/compare/v0.1.0...v0.2.0-alpha
[0.1.0]: https://github.com/aiken884/RemaGraph/releases/tag/v0.1.0
