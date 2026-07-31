# API Boundaries and Stability Contract

This document draws the line between what a consumer of RemaGraph can currently depend on and what is internal implementation that may change at any time. It exists so that anyone building on top of RemaGraph, whether as a standalone tool or embedded as a module inside a larger AI orchestration system, knows what's safe to rely on today, and what isn't.

**RemaGraph is currently pre-1.0 (now in beta; the current version series is `0.4.x`).** Nothing here is a semver-frozen guarantee. What follows is a record of the *current* interface surface and of the parts that are deliberately internal and not meant to be depended on — not a promise that any of it will stay byte-for-byte identical release to release. A future `1.0` will be the point at which a specific interface version actually gets frozen; until then, MINOR and PATCH releases during the pre-1.0 period may carry reviewed, CHANGELOG-documented behavioral changes, including breaking ones.

## Current Public Interface (intended, not semver-guaranteed)

RemaGraph exposes two surfaces: five MCP tools (stdio transport, via FastMCP) and a set of CLI subcommands. Both are documented in full, including parameter tables and response fields, in the "MCP Tools" and "CLI Subcommands" sections of [`README.md`](./README.md); what follows is a short index. **Parameters and response fields on any of these may change between MINOR/PATCH releases during the pre-1.0 period — every such change is recorded in [`CHANGELOG.md`](./CHANGELOG.md).**

### MCP tools (`src/remagraph/server.py`, `@mcp.tool`)

- **`remagraph_store`** — writes a memory record after it passes five arbitration rules (summary length, non-empty learnings, handoff_note length for `task_handoff`, model2vec cosine dedup, agent_id format); lands in SQLite + the FTS5 index.
- **`remagraph_search`** — FTS5 BM25 full-text search (trigram tokenizer, CJK-aware) with kind/status/tags/agent_id/task_id filtering, plus three independent fan-out dimensions: `all_projects`, `cross_project_label`, and `include_related`.
- **`remagraph_status`** — returns the latest active `status_update` memories (deduplicated by `task_id`), plus a version-compatibility handshake (`server_code_version`/`db_schema_version`/`min_reader_version`/`min_writer_version`/`upgrade_hint`/`read_only`).
- **`remagraph_maintain`** — runs DB auto-maintenance (WAL checkpoint, prune, FTS optimize, VACUUM, integrity checks), gated by safety valves.
- **`remagraph_migrate_project`** — one-time migration of memories from a source project to a target project's independent DB (e.g. `default` → `team-project`), marking the source `invalidated`.

### CLI subcommands (`src/remagraph/cli.py`, plus `serve` dispatched separately in `server.main()`)

- **`init`** — one-line project memory directory setup, generates a sourceable `env.sh`.
- **`auto`** — one-shot recall → run command → auto-store (the primary entry point for non-technical users); supports `--recall-only`.
- **`store` / `search` / `status`** — CLI equivalents of the three MCP tools above, JSON to stdout.
- **`maintain`** — CLI equivalent of `remagraph_maintain`.
- **`link`** — declares a `project_edges` relationship (`depends_on`/`sibling`/`shares_upstream`/`monorepo_member`) between two projects, consumed by `include_related`/`recall_related`.
- **`migrate-project`** — CLI equivalent of `remagraph_migrate_project`.
- **`install-hooks`** — installs the bundled git post-commit hook that writes commit summaries back into RemaGraph.
- **`serve [--project <id>]`** — the MCP stdio server entrypoint; not an argparse subcommand — dispatched directly by `main()`. Since the fix described below, it requires an explicit project binding at startup.

## Internal Implementation (explicitly not to be depended on)

The following are internal to RemaGraph's implementation. They are not exposed through the CLI or MCP tools above, and their shape, existence, and naming may change without any deprecation cycle:

- Functions and helpers inside `src/remagraph/db.py`, `maintenance.py`, `search.py`, `arbitration.py`, `store.py`, `audit.py` (and the other internal modules — `dedup.py`, `hooks_installer.py`, `models.py`) that are not reached through a CLI subcommand or an `@mcp.tool`-decorated function.
- Any function or attribute whose name starts with an underscore, anywhere in the codebase (e.g. `_get_conn`, `_bind_project`, `_check_project_binding`, `_cross_project_fanout`, `_row_to_result`).
- The actual key names inside the `_meta` table (`schema_version`, `min_reader_version`, `min_writer_version`, `upgrade_hint`, and any future keys) — consumers should read this information through `remagraph_status`'s handshake fields (or the CLI `status` subcommand), not by querying `_meta` directly.
- The `project_registry`, `memory_labels`, and `project_edges` table structures themselves. Consumers should go through the documented surface instead — `cross_project_label`/`include_related` on `remagraph_search`, and the `link` CLI subcommand/`remagraph_migrate_project` tool — rather than reading or writing the underlying SQLite schema directly.

## Two Breaking Changes That Already Happened Pre-1.0

To make concrete what "the interface can change during the pre-1.0 period" actually means, here are two breaking changes to external behavior that landed in this same development cycle, both released as `0.3.1-alpha` (see [`CHANGELOG.md`](./CHANGELOG.md) for full detail):

- **`remagraph serve` project binding.** Previously, which project a running `serve` process operated on was decided implicitly, on the first tool call. It is now required to bind to exactly one project at startup, via `--project <id>` or the `REMAGRAPH_PROJECT` environment variable — if neither is present, the process fails fast and never enters the MCP stdio loop.
- **Fan-out cap semantics.** The cross-project fan-out cap (used by `cross_project_label`/`include_related`) was previously hardcoded at 20. It is now configurable (`--fanout-cap` / `REMAGRAPH_FANOUT_CAP`, default 50, hard ceiling 200), the response gained `candidates_total`/`candidates_searched`/`candidates_skipped` fields, and the CLI's exit code on truncation changed from `0` to `2`.

Neither change was accidental — both went through PPLX architecture review and are fully documented in the changelog — but both are genuine breaking changes to external behavior, shipped in a `0.3.x` release. That is what "pre-1.0, no frozen guarantees" means in practice for this project.

## Path to Stability

Ahead of a `1.0` release, the three MCP tools most central to RemaGraph's purpose — **`remagraph_store`**, **`remagraph_search`**, and **`remagraph_status`** — are the leading candidates to become the long-term stable surface. That intent is not a guarantee yet: their parameters and response shapes may still change (as the fan-out cap change above shows) until a `1.0` tag actually freezes a specific version of the interface.

## Dependency Boundary

At runtime, RemaGraph depends on `model2vec` (dedup embeddings), `mcp` (FastMCP, the MCP server framework), and `pydantic` (input validation). All three are under permissive open-source licenses; see [`pyproject.toml`](./pyproject.toml) for the exact packages and version constraints in use.

---

# API 邊界與穩定性契約

這份文件劃出「使用 RemaGraph 的人現在可以依賴什麼」與「內部實作、隨時可能變」之間的界線。存在的目的是讓在它之上建構的人——不論是把它當作獨立工具使用，還是當作模組嵌入更大的 AI orchestration 系統——知道現況下哪些東西可以放心依賴，哪些不行。

**RemaGraph 目前是 pre-1.0（現已進入 beta；現行版本序列為 `0.4.x`）。** 這份文件記錄的不是任何 semver 凍結保證，而是「目前的介面現況」，以及「刻意設計為內部、不該被依賴」的部分——不是承諾這些東西會逐版本、逐位元組維持不變。等到專案正式發行 `1.0` 時，才會真正凍結某個具體版本的介面；在那之前，pre-1.0 期間的 MINOR/PATCH 版本都可能包含經過審查、在 CHANGELOG 記錄過的行為調整，包括破壞性調整。

## 目前的公開介面現況（intended，非 semver 保證）

RemaGraph 對外暴露兩個介面面：5 個 MCP tools（stdio transport，透過 FastMCP）以及一組 CLI 子指令。兩者的完整說明（含參數表與回應欄位）都在 [`README.md`](./README.md) 的「MCP 工具」與「CLI 子命令」章節；以下只是一份簡短索引。**pre-1.0 期間，這些工具的參數與回應欄位都可能隨 MINOR/PATCH 版本調整——任何這類調整都會記錄在 [`CHANGELOG.md`](./CHANGELOG.md)。**

### MCP tools（`src/remagraph/server.py`，`@mcp.tool` 裝飾）

- **`remagraph_store`** — 寫入記憶，需先通過五條仲裁規則（summary 長度、learnings 非空、`task_handoff` 的 handoff_note 長度、model2vec cosine 去重、agent_id 格式），才會落入 SQLite + FTS5 index。
- **`remagraph_search`** — FTS5 BM25 全文檢索（trigram tokenizer，支援 CJK）+ kind/status/tags/agent_id/task_id 過濾，另有三個互相獨立的 fan-out 維度：`all_projects`、`cross_project_label`、`include_related`。
- **`remagraph_status`** — 回傳最新的 active `status_update` 記憶（依 `task_id` 去重），並附上版本相容性 handshake 資訊（`server_code_version`/`db_schema_version`/`min_reader_version`/`min_writer_version`/`upgrade_hint`/`read_only`）。
- **`remagraph_maintain`** — 執行 DB 自動維護（WAL checkpoint、prune、FTS optimize、VACUUM、完整性檢查），受安全閥門把關。
- **`remagraph_migrate_project`** — 把記憶從來源 project 一次性遷移到目標 project 的獨立 DB（例如 `default` → `team-project`），並在來源標記 `invalidated`。

### CLI 子指令（`src/remagraph/cli.py`，另外 `serve` 由 `server.main()` 獨立分派）

- **`init`** — 一行搞定的專案記憶目錄初始化，產生可 source 的 `env.sh`。
- **`auto`** — 一鍵：讀取記憶 → 執行指令 → 自動儲存（非技術使用者的主要進入點）；支援 `--recall-only`。
- **`store` / `search` / `status`** — 對應上方三個 MCP tool 的 CLI 版本，JSON 輸出到 stdout。
- **`maintain`** — 對應 `remagraph_maintain` 的 CLI 版本。
- **`link`** — 宣告兩個 project 之間的 `project_edges` 關聯（`depends_on`/`sibling`/`shares_upstream`/`monorepo_member`），供 `include_related`/`recall_related` 使用。
- **`migrate-project`** — 對應 `remagraph_migrate_project` 的 CLI 版本。
- **`install-hooks`** — 安裝套件自帶的 git post-commit hook，讓 commit 自動把摘要寫回 RemaGraph。
- **`serve [--project <id>]`** — MCP stdio server 進入點；不是 argparse 子指令，由 `main()` 直接分派。自下方所述修復起，啟動時需要明確的 project 綁定。

## 內部實作（明確不可依賴）

以下屬於 RemaGraph 的內部實作，不透過上述 CLI 或 MCP tool 對外暴露，其形狀、存在與否、命名都可能在沒有任何棄用週期的情況下改變：

- `src/remagraph/db.py`、`maintenance.py`、`search.py`、`arbitration.py`、`store.py`、`audit.py` 內部（以及其他內部模組——`dedup.py`、`hooks_installer.py`、`models.py`）中，沒有被任何 CLI 子指令或 `@mcp.tool` 裝飾函式碰觸到的函式與 helper。
- 程式庫中任何底線開頭的函式或屬性（例如 `_get_conn`、`_bind_project`、`_check_project_binding`、`_cross_project_fanout`、`_row_to_result`）。
- `_meta` 表內部實際使用的 key 名稱（`schema_version`、`min_reader_version`、`min_writer_version`、`upgrade_hint`，以及未來可能新增的 key）——消費者應該透過 `remagraph_status` 的 handshake 欄位（或 CLI `status` 子指令）取得這些資訊，不要直接查詢 `_meta` 表。
- `project_registry`、`memory_labels`、`project_edges` 這些表結構本身。消費者應該透過既有文件化的介面使用——`remagraph_search` 的 `cross_project_label`/`include_related`，以及 `link` CLI 子指令 / `remagraph_migrate_project` tool——而不是直接讀寫底層 SQLite schema。

## Pre-1.0 期間已經發生過的兩個破壞性調整

為了讓「pre-1.0 期間介面會變」這句話有具體對照，以下兩個對外部行為的破壞性調整都在同一次開發週期內發生，一併發行於 `0.3.1-alpha`（完整細節見 [`CHANGELOG.md`](./CHANGELOG.md)）：

- **`remagraph serve` 的 project 綁定機制。** 過去一個執行中的 `serve` 行程要服務哪個 project，是在第一次工具呼叫時才隱性決定。現在啟動時必須透過 `--project <id>` 或 `REMAGRAPH_PROJECT` 環境變數明確綁定單一 project，兩者皆缺席時行程會 fail-fast，不會進入 MCP stdio 迴圈。
- **fan-out cap 語意調整。** 跨專案 fan-out 上限（供 `cross_project_label`/`include_related` 使用）原本寫死為 20，現在改為可設定（`--fanout-cap` / `REMAGRAPH_FANOUT_CAP`，預設 50，硬上限 200），回應多了 `candidates_total`/`candidates_searched`/`candidates_skipped` 欄位，CLI 在截斷時的 exit code 也從 `0` 改成 `2`。

這兩個改動都不是意外——都經過 PPLX 架構審查、且完整記錄在 changelog 裡——但兩者都是貨真價實的外部行為破壞性變更，而且是在 `0.3.x` 版本裡出貨的。這就是「pre-1.0、沒有凍結保證」對這個專案實際的意思。

## 邁向穩定的路徑

在正式發行 `1.0` 之前，與 RemaGraph 核心目的最相關的三個 MCP tool——**`remagraph_store`**、**`remagraph_search`**、**`remagraph_status`**——是成為長期穩定介面的優先候選。但這個意圖目前還不是保證：在 `1.0` tag 真正凍結某個具體版本的介面之前，它們的參數與回應形狀仍可能調整（如上方 fan-out cap 的例子所示）。

## 依賴邊界

RemaGraph 目前 runtime 依賴 `model2vec`（去重用的 embedding）、`mcp`（FastMCP，MCP server 框架）、`pydantic`（輸入驗證）。三者均為寬鬆開源授權，實際套件與版本見 [`pyproject.toml`](./pyproject.toml)。
