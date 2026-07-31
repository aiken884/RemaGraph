# RemaGraph

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](./LICENSE)
[![CI](https://github.com/aiken884/RemaGraph/actions/workflows/test.yml/badge.svg)](https://github.com/aiken884/RemaGraph/actions/workflows/test.yml)
[![Version](https://img.shields.io/github/v/tag/aiken884/RemaGraph?label=version&color=brightgreen)](https://github.com/aiken884/RemaGraph/tags)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](./pyproject.toml)

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2A6DB2?logo=python&logoColor=white)](https://mypy-lang.org/)
[![REUSE status](https://api.reuse.software/badge/github.com/aiken884/RemaGraph)](https://api.reuse.software/info/github.com/aiken884/RemaGraph)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](./CONTRIBUTING.md)

[![Ko-fi](https://img.shields.io/badge/Ko--fi-support-F16061?logo=ko-fi&logoColor=white)](https://ko-fi.com/aikenlin)
[![PayPal](https://img.shields.io/badge/PayPal-donate-00457C?logo=paypal&logoColor=white)](https://paypal.me/aikenlin)

**[English](#english) | [繁體中文](#繁體中文)**

> **Every agent leaves a trace.** RemaGraph is a lightweight MCP tool that captures what an AI coding agent leaves behind while it works, so whoever picks up next can follow the trail.
>
> **凡走過必留下痕跡。** RemaGraph 是一套輕量的 MCP 工具，讓每一個經手的 AI coding agent 自然留下軌跡，後面接手的人有跡可循。

---

## English

RemaGraph is a lightweight **MCP** (Model Context Protocol) tool built for AI coding agents that work across many tasks and hand-offs without a human in the loop for every step. Its job is simple: capture what an agent learned, decided, or ran into along the way, and make that trail retrievable later — by the same agent, or by a completely different one picking up the work. It complements **CodeGraph**: CodeGraph tracks what's structurally wrong with the code; RemaGraph tracks what happened while someone (or something) was working on it.

| Item | Status |
|------|--------|
| **Version** | `0.4.0-beta` (pre-1.0 beta; the repo itself is now public — the PyPI package is **not yet** published, install via the git tag pin below) |
| **Status** | v2: security / governance / reliability + cross-project collaboration + CLI (init/auto/store/search/status/maintain/link/migrate-project/install-hooks/serve) |
| **Task memory convention** | [`docs/task-memory-convention.md`](./docs/task-memory-convention.md) |
| **Release prep** | [`docs/reviews/v2-release-prep.md`](./docs/reviews/v2-release-prep.md) |
| **Design SOT** | [`DESIGN.md`](./DESIGN.md) |
| **v1 closeout status** | [`docs/reviews/v1-closeout-status.md`](./docs/reviews/v1-closeout-status.md) |
| **Architecture** | [`docs/architecture.md`](./docs/architecture.md) |
| **Audit contract** | [`docs/audit.md`](./docs/audit.md) |
| **Governance checklist** | [`docs/governance/checklist.md`](./docs/governance/checklist.md) |
| **Contributing** | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |
| **Changelog** | [`CHANGELOG.md`](./CHANGELOG.md) |

### Installation

**The repo is public; the package itself has not yet been published to PyPI — install via the git tag pin below.**

Recommended install — a one-liner pinned to the latest beta tag:

```bash
uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.4.0-beta
```

Without a version pin, this installs whatever is currently on `main`:

```bash
uv tool install git+https://github.com/aiken884/RemaGraph.git
```

Or install from source for local development:

```bash
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph
uv pip install -e .
```

Dependencies: Python ≥3.11, model2vec, mcp (FastMCP), pydantic.

### Quick Start (5 minutes, no coding required)

1. Install (see "Installation" above).
2. Initialize:
   ```bash
   remagraph init --project myproject
   source ~/.local/state/remagraph-myproject/env.sh
   ```
3. Run a task in one shot — reads memory, executes, writes memory back:
   ```bash
    remagraph auto --task-id fix-login-001 --agent-id my-ai -- echo "swap this for your actual command"
    ```
    or use the wrapper script:
    ```bash
    curl -O https://raw.githubusercontent.com/aiken884/RemaGraph/main/examples/simple/remagraph-task.sh
    chmod +x remagraph-task.sh
    TASK_ID=fix-login-001 AGENT_ID=my-ai ./remagraph-task.sh python my_agent.py
    ```

**If you just want to check memory first, with no execution and no writes:**
```bash
remagraph auto --recall-only --task-id fix-login-001 --agent-id my-ai
```

No code required. Full plain-language walkthrough: [`docs/task-memory-convention.md`](./docs/task-memory-convention.md).

New users may also find [`docs/internal/alpha-test-playbook.md`](./docs/internal/alpha-test-playbook.md) useful as an onboarding guide — it includes scenarios and a feedback template.

**Note**: this repository is now public. Two separate things are still true, though: this is a pre-1.0 beta (see [`BOUNDARIES.md`](./BOUNDARIES.md) — no frozen public API yet), and the package has not been published to PyPI — install via the git tag pin above.

### MCP Quick Start

#### 1. MCP client configuration

Point any MCP client at RemaGraph. Example configs for common clients follow.

**Note (since the BUG 1 fix)**: `remagraph serve` now **must** be bound to a single project at startup — via either the `--project <id>` argument or the `REMAGRAPH_PROJECT` environment variable. If neither is set, it fails fast and never enters the MCP stdio loop, which prevents a process from silently reading and writing across projects if it happens to inherit another project's environment variables. Each project maps to its own dedicated `remagraph serve` process; a single running process cannot switch between projects dynamically.

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "remagraph": {
      "command": "remagraph",
      "args": ["serve", "--project", "myproject"],
      "env": {
        "REMAGRAPH_STATE_DIR": "/home/user/.local/state/remagraph-myproject"
      }
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "remagraph": {
      "command": "remagraph",
      "args": ["serve"],
      "env": {
        "REMAGRAPH_PROJECT": "myproject"
      }
    }
  }
}
```

**OpenCode / Claude Code** — any client that supports stdio MCP works the same way as above.

#### 2. Environment variables

| Variable | Description | Default |
|------|------|--------|
| `REMAGRAPH_STATE_DIR` | Directory where the SQLite DB lives | `~/.local/state/remagraph/` |
| `REMAGRAPH_PROJECT` | The current project binding (`remagraph serve` requires either this env var or `--project` at startup) | none |
| `REMAGRAPH_FANOUT_CAP` | Max number of "other" project database connections `search --cross-project-label`/`--include-related` will open in a single call (`--fanout-cap` takes precedence over this env var) | `50` |
| `REMAGRAPH_FANOUT_HARD_CAP` | Hard ceiling for the cap above, only meant for explicit opt-in increases (rarely needs adjusting) | `200` |

Directories are created automatically when missing (mode `0700`); DB files are created at mode `0600`. Paths are checked against a security denylist that rejects system directories.

#### 3. CLI basics

```bash
# Start the stdio MCP server (must be bound to a single project via --project or REMAGRAPH_PROJECT)
remagraph serve --project myproject

# Initialize / run a task in one shot
remagraph init --project myproject
remagraph auto --task-id T001 --agent-id my-agent -- make test

# Query (task-id alone is fine)
remagraph search --task-id T001
remagraph search --query "FastMCP lifecycle" --top-k 5
remagraph status --limit 10
```

### MCP Tools

RemaGraph exposes five tools over MCP (stdio transport), compatible with Claude Desktop, Cursor, and other mainstream MCP clients: `remagraph_store`, `remagraph_search`, `remagraph_status`, `remagraph_maintain`, and `remagraph_migrate_project`.

#### `remagraph_store` — write memory

An agent writes a memory record; it must pass five arbitration rules before landing in SQLite + the FTS5 index.

| Parameter | Type | Description |
|------|------|------|
| `project_id` | `str` | Project identifier (same format rules as `task_id`, required) |
| `task_id` | `str` | Task identifier (alphanumeric plus `-_`, max 64 characters) |
| `agent_id` | `str` | Agent identifier (same format constraints as `task_id`) |
| `kind` | `"task_handoff" \| "status_update" \| "discovered_constraint" \| "fleet_member"` | Memory kind (`fleet_member` is recorded/recycled by the dispatching coordinator) |
| `summary` | `str` | One-line summary (indexed for FTS5 full-text search) |
| `learnings` | `list[str]` | Key takeaways |
| `handoff_note` | `str` | Hand-off note (required when `kind` is `task_handoff`) |
| `tags` | `list[str]` | Free-form classification tags (optional) |
| `invalidates` | `list[str]` | Memory IDs to invalidate (used with `discovered_constraint`) |
| `labels` | `list[str]` | Namespaced labels (optional), in `namespace:value` form (e.g. `dep:opencode`, `topic:auth`, `kind:bug`). Convention is a small, controlled set of namespace prefixes such as `dep:`/`topic:`/`kind:`; max length 64 characters. This is a distinct concept from `tags` — `tags` is free-form, `labels` is a controlled vocabulary, and a batch with any malformed label is rejected outright (`reason: "invalid_label"`). Used for exact matching by `remagraph_search`'s `cross_project_label`; see the "Cross-Project Collaboration" section of [`DESIGN.md`](./DESIGN.md) |

Behavior of the four `kind` values (PPLX Priority B):
- **`task_handoff`** — a task hand-off record, carries `handoff_note`
- **`status_update`** — a status update; automatically supersedes the prior record for the same `task_id`
- **`discovered_constraint`** — a newly discovered constraint; can `invalidates` existing, now-wrong memories
- **`fleet_member`** — owned by the dispatching coordinator, records/recycles fleet members (`task_id=fleet` auto-supersedes)

#### `remagraph_search` — query memory

FTS5 BM25 full-text search (trigram tokenizer, CJK-aware) plus tag/kind/agent_id/task_id filtering.

| Parameter | Type | Description |
|------|------|------|
| `query` | `str` | Search keywords (Chinese, English, Japanese, and Korean all supported) |
| `top_k` | `int` | Max results to return (default 20, max 100) |
| `kind` | `str` | Filter by memory kind (optional) |
| `status` | `"active" \| "superseded" \| "invalidated"` | Filter by status (optional) |
| `tags` | `list[str]` | Filter by tags (optional) |
| `project_id` | `str` | Restrict to a single project (optional) |
| `agent_id` | `str` | Filter by agent (optional) |
| `task_id` | `str` | Filter by task (optional) |
| `all_projects` | `bool` | Default `false`; when `true`, removes the `project_id` filter within "this one database file" — each project is its own independent SQLite file, and this flag never opens any other file |
| `cross_project_label` | `str` | Optional. When provided, this switches entirely to the cross-project label search path: via the shared project registry, it runs an exact label match across the current project plus every other known project, each its own independent database file (full-text/filter params like `query`/`kind`/`tags` don't apply on this path). Orthogonal to `all_projects` — the two are independent dimensions. Fan-out is capped at 50 "other" known projects; beyond that, the response is flagged `cross_project_fanout_capped: true` (see response fields below) instead of silently truncating and pretending the result set is complete. See the "Cross-Project Collaboration" section of [`DESIGN.md`](./DESIGN.md) |
| `include_related` | `bool` | Default `false`. A third, fully independent dimension from `cross_project_label`/`all_projects`: when `true`, fans out — in addition to the normal FTS query against the current project — to projects found within `related_hops` via a `project_edges` traversal (`db.recall_related()`), i.e. projects explicitly declared as related through the `remagraph link` CLI subcommand. Unlike `cross_project_label`, matching is still by the normal FTS query, not exact label match. Requires `project_id` as the traversal starting point; if `project_id` is omitted, this gracefully degrades to an ordinary search (no related fan-out) rather than raising an error |
| `related_hops` | `int` | Default `1` (only directly-declared relations). Max **5**. BFS traversal depth used when `include_related=true`; has no effect otherwise |

Short queries (≤2 characters) return an empty result set instead of raising an error. Beyond `results`/`has_more`, every response always carries `cross_project_fanout_capped` (`bool`; only meaningful when `cross_project_label` is used — ordinary queries always get `false`). When `cross_project_label` is used, each result also carries `source_project_id` marking which project it came from. Every result includes the full field set (`id`/`project_id`/`summary`/`agent_id`/`kind`/`task_id`/`timestamp`/`score`/`learnings`/`handoff_note`/`tags`/`status`/`created_at`/`updated_at` — everything except `embedding`).

#### `remagraph_status` — query a project's latest state

Returns all active `status_update` memories, deduplicated by `task_id` (only the newest record per task survives). Also includes version-compatibility handshake info, so callers don't have to wait for a `remagraph_store` write to fail before learning about a version mismatch.

| Parameter | Type | Description |
|------|------|------|
| `project_id` | `str` | Restrict to a single project (optional) |
| `limit` | `int` | Max results to return (default 20, max 100) |
| `all_projects` | `bool` | Default `false`; `true` removes the `project_id` filter |

Beyond the existing `latest` array, the response always includes these compatibility handshake fields:

| Field | Type | Description |
|------|------|------|
| `server_code_version` | `int` | Schema version of the code currently running |
| `db_schema_version` | `int \| null` | The schema version actually recorded in the database's `_meta` table (a defensive read) |
| `min_reader_version` | `int \| null` | Oldest code version allowed to read this database; `null` if the database predates this mechanism |
| `min_writer_version` | `int \| null` | Oldest code version allowed to write to this database; same `null` behavior as above |
| `upgrade_hint` | `str \| null` | Upgrade guidance text embedded in the database; `null` if absent |
| `read_only` | `bool` | Whether the current connection is in read-only degraded mode (see "Governance & Security" below) |

#### `remagraph_maintain` — run DB maintenance

Runs automatic database maintenance (WAL checkpoint, prune superseded/invalidated memories, FTS5 optimize, VACUUM, integrity check), gated by the same safety valve (`maintenance.safety_validate_project`) used by the CLI `maintain` subcommand.

| Parameter | Type | Description |
|------|------|------|
| `project_id` | `str` | Project to run maintenance on (required) |
| `force` | `bool` | Default `false`; when `true`, every maintenance step runs unconditionally instead of only the ones whose threshold has actually been crossed |

Note: unlike the CLI `maintain` subcommand, this MCP tool has no `dry_run` option — every call executes for real.

Response (success): `{"status": "ok", "stats": {...}}`. Response (failure): `{"status": "error", "reason": "..."}`.

`stats` is produced by `maintenance.run_maintenance()`; most fields only appear when the corresponding step actually ran:

| Field | Type | Description |
|------|------|------|
| `project_id` | `str` | Echoes the project maintained |
| `started_at` | `str` | ISO-8601 UTC timestamp when maintenance started |
| `wal_checkpoint` | `str` | `"done"` if `PRAGMA wal_checkpoint(TRUNCATE)` ran |
| `pruned_count` | `int` | Number of superseded/invalidated memories deleted (older than `prune_superseded_age_days`, default 90 days; capped per task by `prune_superseded_max_per_task`, default 5) |
| `fts_optimized` | `bool` | `true` if the FTS5 index was optimized |
| `vacuum` | `str` | `"done"` if `VACUUM` ran (triggered once DB file size exceeds `vacuum_threshold_mb`, default 50 MB) |
| `size_before_mb` | `float` | DB file size in MB immediately before VACUUM; only present when `vacuum` ran |
| `integrity` | `str` | Result of `PRAGMA quick_check`; expected `"ok"` — any other value raises and is recorded as an audit violation |
| `skipped` | `bool` | `true` when the connection was already downgraded to the read-only schema tier (see "Version Compatibility" in [`DESIGN.md`](./DESIGN.md)); every write step above is skipped entirely |
| `skip_reason` | `str` | `"read_only_schema_tier"` when `skipped` is `true` |

#### `remagraph_migrate_project` — one-time cross-project migration

One-time migration of memories from a source project to a target project's independent DB (e.g. `default` → `project-a`), marking the originals `invalidated` in the source. Performs a real migration — this tool and the CLI's `migrate-project` subcommand (`cli.cmd_migrate_project`) both call the same shared core implementation (`store.migrate_project_memories`), so they always produce the same end state for the same inputs.

| Parameter | Type | Description |
|------|------|------|
| `from_project` | `str` | Source project (required) |
| `to_project` | `str` | Target project (required) |
| `dry_run` | `bool` | Default `false`; when `true`, only computes and reports how many records *would* be migrated — no writes happen, and the count uses the exact same match query as a real run, so it always agrees with the count a subsequent real run reports |

How it works:
1. The target project is validated through the same `safety_validate_project(to_project, require_env_match=False)` safety valve used elsewhere (project-id naming-convention rules, `project.json` metadata consistency, etc).
2. The source project's `state_dir` is resolved via the shared project registry (`db.get_registered_state_dir(from_project)`) — **not** a hardcoded path. If `from_project` has never been registered (no prior `remagraph` command has resolved a state_dir for it), the call fails with a clear error rather than silently treating it as zero migratable records. `from_project == "default"` is the one exception: it resolves via the ambient `REMAGRAPH_STATE_DIR`/`REMAGRAPH_HOME` the same way any other "default"-project usage does, since `"default"` is deliberately never registered in the normal course of things.
3. Records that heuristically look like they belong to `to_project` (a `LIKE` match against `task_id`/`tags`/`agent_id`/`summary`) are copied into the target project's own DB with `project_id` forced to `to_project`, and the originals are marked `status='invalidated'` in the source with a `migrated-to:<to_project>` breadcrumb appended to `learnings`.
4. If either the source or target database is currently in the read-only degraded schema-compatibility tier (see "Version Compatibility" below), the migration is rejected with a clear error instead of failing silently or partially.

Response (success): `{"status": "ok" | "dry-run", "from": "...", "to": "...", "dry_run": true|false, "migrated_count": N, "skipped_ids": [...]}`. Response (failure): `{"status": "error", "reason": "..."}`.

### Governance & Security

- **Rate limiting** — per-agent token bucket (60 calls / 60 seconds) to prevent abuse
- **Input validation** — `task_id` / `agent_id` are checked against format rules via Pydantic validators
- **Path safety** — `REMAGRAPH_STATE_DIR` rejects system directory paths
- **Audit rotation** — `audit-YYYYMM.jsonl`, split automatically by month
- **DB size cap** — SQLite `max_page_count` set to a 100MB soft limit
- **Migration** — built-in schema version tracking and a migration chain
- **Version-compatibility degradation** — when a database's schema version is newer than the running code, the database's own `min_reader_version`/`min_writer_version` decide one of three outcomes: fully compatible (normal reads and writes), read-only degraded (writes rejected, reads unaffected), or refused outright. Callers can learn this ahead of time via `remagraph_status`'s compatibility handshake fields, without waiting for a write to fail. See the "Version Compatibility" section of [`DESIGN.md`](./DESIGN.md)
- **Cross-project registry** — `project_registry` automatically records known projects and their `state_dir`, backing `remagraph_search`'s `cross_project_label` read-only cross-project lookups. See the "Cross-Project Collaboration" section of [`DESIGN.md`](./DESIGN.md)
- **Stale-record cleanup** — `cleanup_superseded()` can purge non-active records older than 90 days

### CLI Subcommands (for headless agents)

Besides MCP mode, `remagraph` supports the following CLI subcommands (all with JSON output):

```bash
# One-shot, most recommended: read memory → run the command → write memory
remagraph auto --task-id task-001 --agent-id my-agent -- make test

# Initialize
remagraph init --project myproject

# Write memory
remagraph store \
  --task-id task-001 --agent-id my-agent --kind status_update \
  --summary "Task complete, all tests passing, no regressions found" \
  --learnings '["Watch out for FastMCP lifecycle handling"]' \
  --tags '["python","mcp"]'

# Query (task-id alone is fine, query is not required)
remagraph search --task-id task-001
remagraph search --query "FastMCP lifecycle" --top-k 5

# Query latest status
remagraph status --limit 10
```

Plain-language convention: [`docs/task-memory-convention.md`](./docs/task-memory-convention.md).
Full spec: [`DESIGN.md`](./DESIGN.md); audit contract: [`docs/audit.md`](./docs/audit.md).

### Development & Verification

```bash
# uv is recommended
uv sync --all-extras
uv run ruff check src tests
uv run mypy src/
uv run pytest -m 'not slow'
REMAGRAPH_STATE_DIR=$(mktemp -d) uv run pytest tests/smoke
```

- CI pipeline: smoke → adversarial → lint (ruff + mypy) → test (coverage ≥80%); plus gitleaks, pip-audit, and mutmut (non-blocking).
- Never let tests default to writing production state — smoke tests must use `REMAGRAPH_STATE_DIR` or pytest's `tmp_path`.

### Resources

- [`DESIGN.md`](./DESIGN.md) — the design source of truth: schema, arbitration rules, cross-project collaboration, version compatibility
- [`CHANGELOG.md`](./CHANGELOG.md) — version history
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how to contribute
- [`SECURITY.md`](./SECURITY.md) — how to report a vulnerability
- [`docs/architecture.md`](./docs/architecture.md) — system architecture
- [`docs/task-memory-convention.md`](./docs/task-memory-convention.md) — plain-language guide to the task-memory convention
- [`docs/audit.md`](./docs/audit.md) — the audit log contract
- [`docs/governance/checklist.md`](./docs/governance/checklist.md) — the governance checklist

### Support

If RemaGraph is quietly keeping your agents' memory straight, you can support its development on [Ko-fi](https://ko-fi.com/aikenlin) (card or PayPal) or directly via [PayPal](https://paypal.me/aikenlin). Entirely optional — RemaGraph is, and stays, free.

### License

[Apache-2.0](./LICENSE)

---

## 繁體中文

RemaGraph 是一套輕量的 MCP（Model Context Protocol）工具，設計給需要跨多個任務、多次交接、自主運作的 AI coding agent 使用。它做的事很單純：把 agent 一路上學到的、決定的、踩過的坑記下來，讓這條軌跡日後能被查回來——不管是同一個 agent，還是完全不同的另一個 agent 接手。與 CodeGraph 互補：CodeGraph 記的是「這段程式碼有什麼已知問題」，RemaGraph 記的是「處理過程中留下了什麼痕跡」。

| 項目 | 現況 |
|------|------|
| **版本** | `0.4.0-beta`(pre-1.0 beta 階段;repo 本身已公開;PyPI 套件**尚未**上架,安裝方式見下方的 git tag pin) |
| **狀態** | v2:安全 / 治理 / 可靠度 + 跨專案協作 + CLI(init/auto/store/search/status/maintain/link/migrate-project/install-hooks/serve) |
| **任務記憶慣例** | [`docs/task-memory-convention.md`](./docs/task-memory-convention.md) |
| **發行準備** | [`docs/reviews/v2-release-prep.md`](./docs/reviews/v2-release-prep.md) |
| **設計 SOT** | [`DESIGN.md`](./DESIGN.md) |
| **收斂狀態** | [`docs/reviews/v1-closeout-status.md`](./docs/reviews/v1-closeout-status.md) |
| **架構文件** | [`docs/architecture.md`](./docs/architecture.md) |
| **Audit 合約** | [`docs/audit.md`](./docs/audit.md) |
| **治理清單** | [`docs/governance/checklist.md`](./docs/governance/checklist.md) |
| **貢獻指南** | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |
| **變更日誌** | [`CHANGELOG.md`](./CHANGELOG.md) |

### 安裝

**repo 本身已公開，但套件尚未發布到 PyPI——請透過下方的 git tag pin 安裝。**

推薦安裝方式（一行指令，pin 到目前最新的 beta 版本標記）：

```bash
uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.4.0-beta
```

不指定版本則安裝 `main` 分支目前最新內容：

```bash
uv tool install git+https://github.com/aiken884/RemaGraph.git
```

或從原始碼開發安裝：

```bash
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph
uv pip install -e .
```

依賴：Python ≥3.11、model2vec、mcp（FastMCP）、pydantic。

### 快速開始（非技術使用者，5 分鐘上手）

1. 安裝（見上方「安裝」方式）。
2. 初始化：
   ```bash
   remagraph init --project myproject
   source ~/.local/state/remagraph-myproject/env.sh
   ```
3. 一鍵跑任務（自動讀記憶 → 執行 → 寫記憶）：
   ```bash
    remagraph auto --task-id fix-login-001 --agent-id my-ai -- echo "這裡換成你的真正指令"
    ```
    或用包裝腳本：
    ```bash
    curl -O https://raw.githubusercontent.com/aiken884/RemaGraph/main/examples/simple/remagraph-task.sh
    chmod +x remagraph-task.sh
    TASK_ID=fix-login-001 AGENT_ID=my-ai ./remagraph-task.sh python my_agent.py
    ```

**如果您想先只查記憶（不執行、不寫入）**：
```bash
remagraph auto --recall-only --task-id fix-login-001 --agent-id my-ai
```

不需要寫任何程式碼。完整白話說明見 [`docs/task-memory-convention.md`](./docs/task-memory-convention.md)。

新使用者可參考 [`docs/internal/alpha-test-playbook.md`](./docs/internal/alpha-test-playbook.md) 作為上手指南（含場景與回饋模板）。

**注意**：這個 repo 現在已經對外公開。但有兩件事仍然分開成立：這是 pre-1.0 beta（詳見 [`BOUNDARIES.md`](./BOUNDARIES.md)——尚無凍結的公開 API），而套件本身尚未發布到 PyPI，請透過上方的 git tag pin 安裝。

### MCP 快速開始

#### 1. MCP Client 設定

將 RemaGraph 掛到 MCP client 即可運作。以下為常見 client 設定範例：

**注意（自 BUG 1 修復起）**：`remagraph serve` 現在**必須**在啟動時明確綁定單一 project——透過 `--project <id>` 參數，或 `REMAGRAPH_PROJECT` 環境變數擇一提供，兩者皆缺席時會快速失敗、不會啟動 MCP stdio 迴圈（避免行程在繼承了「別的專案」環境變數的情況下悄悄跨專案讀寫）。每個 project 各自對應一個獨立的 `remagraph serve` 行程，不支援單一行程動態切換多個 project。

**Claude Desktop**（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "remagraph": {
      "command": "remagraph",
      "args": ["serve", "--project", "myproject"],
      "env": {
        "REMAGRAPH_STATE_DIR": "/home/user/.local/state/remagraph-myproject"
      }
    }
  }
}
```

**Cursor**（`.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "remagraph": {
      "command": "remagraph",
      "args": ["serve"],
      "env": {
        "REMAGRAPH_PROJECT": "myproject"
      }
    }
  }
}
```

**OpenCode / Claude Code** — 任何支援 stdio MCP 的 client 皆可，設定方式同上。

#### 2. 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `REMAGRAPH_STATE_DIR` | SQLite DB 存放目錄 | `~/.local/state/remagraph/` |
| `REMAGRAPH_PROJECT` | 目前 project 綁定（`remagraph serve` 啟動時必須提供此環境變數或 `--project` 其中之一） | 無 |
| `REMAGRAPH_FANOUT_CAP` | `search --cross-project-label`/`--include-related` 單次最多開幾個「其他」專案資料庫連線（`--fanout-cap` 優先於此環境變數） | `50` |
| `REMAGRAPH_FANOUT_HARD_CAP` | 上述 cap 的硬性上限，僅供明確 opt-in 提高（一般不需調整） | `200` |

目錄不存在時自動建立（權限 `0700`），DB 檔案權限 `0600`。路徑已加入安全性檢查（禁止系統目錄）。

#### 3. CLI 入門

```bash
# 啟動 stdio MCP server（必須以 --project 或 REMAGRAPH_PROJECT 綁定單一 project）
remagraph serve --project myproject

# 初始化 / 一鍵任務
remagraph init --project myproject
remagraph auto --task-id T001 --agent-id my-agent -- make test

# 查詢（可只帶 task-id）
remagraph search --task-id T001
remagraph search --query "FastMCP 生命週期" --top-k 5
remagraph status --limit 10
```

### MCP 工具

RemaGraph 透過 MCP（stdio transport）暴露五個 tool，相容 Claude Desktop、Cursor 等主流 MCP 客戶端：`remagraph_store`、`remagraph_search`、`remagraph_status`、`remagraph_maintain`、`remagraph_migrate_project`。

#### `remagraph_store` — 寫入記憶

agent 寫入記憶，通過五條仲裁規則後才會寫入 SQLite + FTS5 index。

| 參數 | 型別 | 說明 |
|------|------|------|
| `project_id` | `str` | 專案識別碼（格式同 task_id，必填） |
| `task_id` | `str` | 任務識別碼（格式：英數字 + `-_`，最多 64 字元） |
| `agent_id` | `str` | agent 識別碼（同 task_id 格式限制） |
| `kind` | `"task_handoff" \| "status_update" \| "discovered_constraint" \| "fleet_member"` | 記憶類型（fleet_member 由派工協調端 record/recycle） |
| `summary` | `str` | 一句話摘要（供 FTS5 全文檢索） |
| `learnings` | `list[str]` | 學到的要點 |
| `handoff_note` | `str` | 交接備註（`task_handoff` 時必填） |
| `tags` | `list[str]` | 分類標籤（選填，自由格式） |
| `invalidates` | `list[str]` | 要 invalidate 的 memory id（`discovered_constraint` 時用） |
| `labels` | `list[str]` | 命名空間化標籤（選填），格式 `namespace:value`（如 `dep:opencode`、`topic:auth`、`kind:bug`），慣例上 namespace 用 `dep:`/`topic:`/`kind:` 等一組小、受控字首；長度上限 64 字元。與 `tags` 是不同概念——`tags` 自由格式，`labels` 是受控詞彙，任一格式不符會整批拒絕（`reason: "invalid_label"`），供 `remagraph_search` 的 `cross_project_label` 精確比對用，詳見 [`DESIGN.md`](./DESIGN.md) 的「跨專案協作」章節 |

四種 `kind` 的行為（PPLX Priority B）：
- **`task_handoff`**：任務交接記錄，附 `handoff_note`
- **`status_update`**：狀態更新，同 `task_id` 自動 supersede 舊記錄
- **`discovered_constraint`**：發現的限制，可 `invalidates` 既有的錯誤記憶
- **`fleet_member`**：由派工協調端擁有，record/recycle 艦隊成員（task_id=fleet 自動 supersede）

#### `remagraph_search` — 查詢記憶

FTS5 BM25 全文檢索（trigram tokenizer，支援 CJK）+ tag/kind/agent_id/task_id 過濾。

| 參數 | 型別 | 說明 |
|------|------|------|
| `query` | `str` | 搜尋關鍵字（支援中英日韓） |
| `top_k` | `int` | 回傳筆數上限（預設 20，最大 100） |
| `kind` | `str` | 過濾記憶類型（選填） |
| `status` | `"active" \| "superseded" \| "invalidated"` | 過濾狀態（選填） |
| `tags` | `list[str]` | 過濾標籤（選填） |
| `project_id` | `str` | 限定單一專案（選填） |
| `agent_id` | `str` | 過濾 agent（選填） |
| `task_id` | `str` | 過濾任務（選填） |
| `all_projects` | `bool` | 預設 `false`；`true` 時移除「目前這一個資料庫檔案內」的 `project_id` 過濾（每個 project 各自是獨立 SQLite 檔案，此旗標從不開啟其他檔案） |
| `cross_project_label` | `str` | 選填。提供時完全改走跨專案標籤搜尋路徑：透過共用的 project registry，對「目前專案 + 所有已知專案」各自獨立的資料庫檔案，依 label 精確比對（`query`/`kind`/`tags` 等全文檢索/過濾參數不適用）。與 `all_projects` 是互不相干的兩個維度。fan-out 上限 50 個「其他」已知專案，超過時回應會標記 `cross_project_fanout_capped: true`（見下方回應欄位），不悄悄截斷佯裝完整。詳見 [`DESIGN.md`](./DESIGN.md) 的「跨專案協作」章節 |
| `include_related` | `bool` | 預設 `false`。與 `cross_project_label`/`all_projects` 完全獨立的第三個維度：`true` 時，除了對「目前專案」執行正常的 FTS 查詢外，會額外沿 `project_edges` traversal（`db.recall_related()`）fan out 到 `related_hops` 之內、透過 `remagraph link` CLI 子指令明確宣告為關聯的專案。與 `cross_project_label` 不同，比對方式仍是正常的 FTS 查詢，不是 label 精確比對。需要 `project_id` 作為 traversal 起點；`project_id` 為空時優雅退化為一般搜尋（不展開 related fan-out），不拋出例外 |
| `related_hops` | `int` | 預設 `1`（僅限直接宣告的關聯）。上限 **5**。`include_related=true` 時的 BFS traversal 深度；否則此欄位無意義 |

短查詢（≤2 字元）回傳空結果不拋錯。回應除 `results`/`has_more` 外，恆附加 `cross_project_fanout_capped`（`bool`，僅使用 `cross_project_label` 時有意義；一般查詢恆為 `false`）；使用 `cross_project_label` 時每筆結果另附 `source_project_id` 標示其來源專案。每筆結果涵蓋完整欄位（`id`/`project_id`/`summary`/`agent_id`/`kind`/`task_id`/`timestamp`/`score`/`learnings`/`handoff_note`/`tags`/`status`/`created_at`/`updated_at`，`embedding` 除外）。

#### `remagraph_status` — 查詢專案最新現況

回傳所有 active 的 `status_update` 記憶，以 `task_id` 去重（每個 task 只留最新一筆）。同時附上版本相容性 handshake 資訊，讓呼叫端不必等 `remagraph_store` 寫入失敗才第一次得知有版本落差。

| 參數 | 型別 | 說明 |
|------|------|------|
| `project_id` | `str` | 限定單一專案（選填） |
| `limit` | `int` | 回傳筆數上限（預設 20，最大 100） |
| `all_projects` | `bool` | 預設 `false`；`true` 時移除 `project_id` 過濾 |

回應除既有的 `latest` 陣列外，恆附加下列相容性 handshake 欄位：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `server_code_version` | `int` | 目前執行中程式碼的 schema 版本 |
| `db_schema_version` | `int \| null` | 資料庫 `_meta` 表實際存下的 schema 版本（防禦性讀取） |
| `min_reader_version` | `int \| null` | 資料庫允許被讀取的最舊程式碼版本；資料庫若建立於此機制導入之前則為 `null` |
| `min_writer_version` | `int \| null` | 資料庫允許被寫入的最舊程式碼版本；同上，缺漏時為 `null` |
| `upgrade_hint` | `str \| null` | 資料庫內建的升級指引文字；缺漏時為 `null` |
| `read_only` | `bool` | 目前連線是否處於唯讀降級模式（見下方「治理與安全」） |

#### `remagraph_maintain` — 執行 DB 維護

執行自動化資料庫維護（WAL checkpoint、清除 superseded/invalidated 記憶、FTS5 optimize、VACUUM、完整性檢查），受與 CLI `maintain` 子指令相同的安全閥門（`maintenance.safety_validate_project`）把關。

| 參數 | 型別 | 說明 |
|------|------|------|
| `project_id` | `str` | 要執行維護的專案（必填） |
| `force` | `bool` | 預設 `false`；`true` 時無條件執行每一項維護步驟，不受各自門檻是否已被觸發影響 |

注意：與 CLI `maintain` 子指令不同，此 MCP tool 沒有 `dry_run` 選項——每次呼叫都會實際執行。

回應（成功）：`{"status": "ok", "stats": {...}}`。回應（失敗）：`{"status": "error", "reason": "..."}`。

`stats` 由 `maintenance.run_maintenance()` 產生；多數欄位只在對應步驟實際執行時才會出現：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `project_id` | `str` | 回顯本次維護的專案 |
| `started_at` | `str` | 維護開始時間（ISO-8601 UTC） |
| `wal_checkpoint` | `str` | 若執行了 `PRAGMA wal_checkpoint(TRUNCATE)` 則為 `"done"` |
| `pruned_count` | `int` | 被刪除的 superseded/invalidated 記憶筆數（早於 `prune_superseded_age_days`，預設 90 天；每 task 上限 `prune_superseded_max_per_task`，預設 5） |
| `fts_optimized` | `bool` | 若 FTS5 index 已被 optimize 則為 `true` |
| `vacuum` | `str` | 若執行了 `VACUUM` 則為 `"done"`（DB 檔案大小超過 `vacuum_threshold_mb`，預設 50MB，時觸發） |
| `size_before_mb` | `float` | VACUUM 執行前的 DB 檔案大小（MB）；只在 `vacuum` 有執行時出現 |
| `integrity` | `str` | `PRAGMA quick_check` 的結果；預期為 `"ok"`，其餘任何值都會拋出例外並記一筆稽核違規 |
| `skipped` | `bool` | 連線若已被降級為唯讀 schema tier（見 [`DESIGN.md`](./DESIGN.md) 的「版本相容性」）則為 `true`；此時上述所有寫入步驟一律跳過 |
| `skip_reason` | `str` | `skipped` 為 `true` 時為 `"read_only_schema_tier"` |

#### `remagraph_migrate_project` — 一次性跨專案遷移

把記憶從來源 project 一次性遷移到目標 project 的獨立 DB（例如 `default` → `project-a`），並在來源標記 `invalidated`。這是真正會搬移資料的實作——此 tool 與 CLI 的 `migrate-project` 子指令（`cli.cmd_migrate_project`）共用同一個核心函式（`store.migrate_project_memories`），對同一組輸入必然產生一致的最終結果。

| 參數 | 型別 | 說明 |
|------|------|------|
| `from_project` | `str` | 來源專案（必填） |
| `to_project` | `str` | 目標專案（必填） |
| `dry_run` | `bool` | 預設 `false`；`true` 時只計算並回報「會遷移幾筆」，不做任何寫入——使用與實際執行完全相同的比對 SQL，因此回報的筆數必然與之後真的執行時一致 |

運作方式：
1. 目標專案透過與其他地方相同的 `safety_validate_project(to_project, require_env_match=False)` 安全閥門驗證（專案命名慣例規則、`project.json` metadata 一致性等）。
2. 來源專案的 `state_dir` 透過共用的 project registry 解析（`db.get_registered_state_dir(from_project)`）——**不是**寫死的路徑。若 `from_project` 從未被登記過（沒有任何 `remagraph` 指令曾經對它解析出 state_dir），呼叫會以清楚的錯誤失敗，而不是靜默當作 0 筆可遷移記錄。`from_project == "default"` 是唯一的例外：它比照一般「default」用法，透過目前環境的 `REMAGRAPH_STATE_DIR`/`REMAGRAPH_HOME` 解析，因為 `"default"` 在正常使用情境下本來就刻意不會被登記進 registry。
3. 依 `task_id`/`tags`/`agent_id`/`summary` 啟發式比對出「看起來屬於」`to_project` 的記錄，複製到目標專案自己的 DB（強制 `project_id` 為 `to_project`），並在來源標記 `status='invalidated'`，於 `learnings` 附加一筆 `migrated-to:<to_project>` 軌跡。
4. 若來源或目標資料庫目前處於唯讀降級的 schema 相容性分級（見下方「版本相容性」），遷移會以清楚的錯誤被拒絕，而不是靜默失敗或只搬移一半。

回應（成功）：`{"status": "ok" | "dry-run", "from": "...", "to": "...", "dry_run": true|false, "migrated_count": N, "skipped_ids": [...]}`。回應（失敗）：`{"status": "error", "reason": "..."}`。

### 治理與安全

- **Rate limiting**：per-agent token bucket（60 calls/60 秒），防止濫用
- **輸入驗證**：`task_id` / `agent_id` 經 Pydantic validator 檢核格式
- **路徑安全**：`REMAGRAPH_STATE_DIR` 禁止系統目錄路徑
- **Audit rotation**：`audit-YYYYMM.jsonl` 按月自動分檔
- **DB 容量**：SQLite `max_page_count` 設定 100MB soft limit
- **Migration**：內建 schema 版本追蹤與 migration chain
- **版本相容性降級**：資料庫 schema 版本比目前程式碼還新時，依資料庫內建的 `min_reader_version`/`min_writer_version` 分三層處理——完全相容（正常讀寫）、唯讀降級（拒絕寫入、讀取不受影響）、或完全拒絕開啟；呼叫端可透過 `remagraph_status` 的相容性 handshake 欄位提早得知，不必等寫入失敗。詳見 [`DESIGN.md`](./DESIGN.md) 的「版本相容性」章節
- **跨專案登記表**：`project_registry` 自動記錄已知 project 及其 state_dir，供 `remagraph_search` 的 `cross_project_label` 跨專案唯讀查詢使用，見 [`DESIGN.md`](./DESIGN.md) 的「跨專案協作」章節
- **超期清理**：`cleanup_superseded()` 可清理 90 天前的非 active 記錄

### CLI 子命令（headless agent 用）

除 MCP mode 外，`remagraph` 支援以下 CLI 子命令（JSON 輸出）：

```bash
# 一鍵（最推薦）：讀記憶 → 跑指令 → 寫記憶
remagraph auto --task-id task-001 --agent-id my-agent -- make test

# 初始化
remagraph init --project myproject

# 寫入記憶
remagraph store \
  --task-id task-001 --agent-id my-agent --kind status_update \
  --summary "任務完成，所有測試通過，已確認無回歸問題" \
  --learnings '["使用 FastMCP 要注意生命週期"]' \
  --tags '["python","mcp"]'

# 查詢（可只帶 task-id，不必 query）
remagraph search --task-id task-001
remagraph search --query "FastMCP 生命週期" --top-k 5

# 查詢最新現況
remagraph status --limit 10
```

白話慣例：[`docs/task-memory-convention.md`](./docs/task-memory-convention.md)
詳細規格：[`DESIGN.md`](./DESIGN.md)；Audit 合約：[`docs/audit.md`](./docs/audit.md)。

### 開發與驗證

```bash
# 建議使用 uv
uv sync --all-extras
uv run ruff check src tests
uv run mypy src/
uv run pytest -m 'not slow'
REMAGRAPH_STATE_DIR=$(mktemp -d) uv run pytest tests/smoke
```

- CI：smoke → adversarial → lint（ruff + mypy）→ test（coverage ≥80）；另有 gitleaks、pip-audit、mutmut（非 blocking）。
- 勿在測試中預設寫入生產 state；冒煙測試必須使用 `REMAGRAPH_STATE_DIR` 或 pytest 的 `tmp_path`。

### 資源

- [`DESIGN.md`](./DESIGN.md) — 設計 SOT：schema、仲裁規則、跨專案協作、版本相容性
- [`CHANGELOG.md`](./CHANGELOG.md) — 版本變更歷史
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — 貢獻指南
- [`SECURITY.md`](./SECURITY.md) — 安全漏洞回報方式
- [`docs/architecture.md`](./docs/architecture.md) — 系統架構文件
- [`docs/task-memory-convention.md`](./docs/task-memory-convention.md) — 任務記憶慣例白話說明
- [`docs/audit.md`](./docs/audit.md) — Audit 日誌合約
- [`docs/governance/checklist.md`](./docs/governance/checklist.md) — 治理檢查清單

### 贊助支持

如果 RemaGraph 默默幫您的 agent 群把記憶顧好了，歡迎到 [Ko-fi](https://ko-fi.com/aikenlin)（信用卡或 PayPal 皆可）或直接透過 [PayPal](https://paypal.me/aikenlin) 贊助它的後續開發。完全自由心證——RemaGraph 現在是、以後也會是免費的。

### 授權

[Apache-2.0](./LICENSE)
