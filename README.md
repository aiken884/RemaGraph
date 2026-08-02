# RemaGraph

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](./LICENSE)
[![PyPI](https://img.shields.io/pypi/v/remagraph.svg)](https://pypi.org/project/remagraph/)
[![CI](https://github.com/aiken884/RemaGraph/actions/workflows/test.yml/badge.svg)](https://github.com/aiken884/RemaGraph/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](./pyproject.toml)

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2A6DB2?logo=python&logoColor=white)](https://mypy-lang.org/)
[![REUSE status](https://api.reuse.software/badge/github.com/aiken884/RemaGraph)](https://api.reuse.software/info/github.com/aiken884/RemaGraph)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](./CONTRIBUTING.md)

[![Ko-fi](https://img.shields.io/badge/Ko--fi-support-F16061?logo=ko-fi&logoColor=white)](https://ko-fi.com/aikenlin)
[![PayPal](https://img.shields.io/badge/PayPal-donate-00457C?logo=paypal&logoColor=white)](https://paypal.me/aikenlin)

> **Every agent leaves a trace.** RemaGraph is a lightweight MCP tool that captures what an AI coding agent leaves behind while it works, so whoever picks up next can follow the trail.

---

RemaGraph is a lightweight **MCP** (Model Context Protocol) tool built for AI coding agents that work across many tasks and hand-offs without a human in the loop for every step. Its job is simple: capture what an agent learned, decided, or ran into along the way, and make that trail retrievable later — by the same agent, or by a completely different one picking up the work. It complements **CodeGraph**: CodeGraph tracks what's structurally wrong with the code; RemaGraph tracks what happened while someone (or something) was working on it.

| Item | Status |
|------|--------|
| **Version** | `0.5.0-beta` (pre-1.0 beta; published on [PyPI](https://pypi.org/project/remagraph/) — `uv tool install remagraph` / `pip install remagraph`) |
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

## Installation

RemaGraph is published on [PyPI](https://pypi.org/project/remagraph/). Recommended install:

```bash
uv tool install remagraph
```

or with plain `pip`:

```bash
pip install remagraph
```

Prefer a specific tag, or want the bleeding edge from `main`? Install straight from the git repo:

```bash
# Pinned to a specific release tag
uv tool install git+https://github.com/aiken884/RemaGraph.git@v0.5.0-beta

# Whatever is currently on main
uv tool install git+https://github.com/aiken884/RemaGraph.git
```

Or install from source for local development:

```bash
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph
uv pip install -e .
```

Dependencies: Python ≥3.11, model2vec, mcp (FastMCP), pydantic.

## Quick Start (5 minutes, no coding required)

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

**Note**: this repository is public and the package is published on PyPI, but it's still a pre-1.0 beta (see [`BOUNDARIES.md`](./BOUNDARIES.md) — no frozen public API yet).

## MCP Quick Start

### 1. MCP client configuration

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

### 2. Environment variables

| Variable | Description | Default |
|------|------|--------|
| `REMAGRAPH_STATE_DIR` | Directory where the SQLite DB lives | `~/.local/state/remagraph/` |
| `REMAGRAPH_PROJECT` | The current project binding (`remagraph serve` requires either this env var or `--project` at startup) | none |
| `REMAGRAPH_FANOUT_CAP` | Max number of "other" project database connections `search --cross-project-label`/`--include-related` will open in a single call (`--fanout-cap` takes precedence over this env var) | `50` |
| `REMAGRAPH_FANOUT_HARD_CAP` | Hard ceiling for the cap above, only meant for explicit opt-in increases (rarely needs adjusting) | `200` |
| `REMAGRAPH_RESTRICTED_PREFIXES` | Comma-separated list of `project_id` prefixes (e.g. `"team-a-,team-b-"`) that a deployment wants to forbid from ever using the default DB; the safety valve raises `SafetyValveError` if a matching project_id resolves to the default state dir. Empty by default — RemaGraph imposes no naming convention of its own | none (no prefix restricted) |

Directories are created automatically when missing (mode `0700`); DB files are created at mode `0600`. Paths are checked against a security denylist that rejects system directories.

### 3. CLI basics

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

## MCP Tools

RemaGraph exposes five tools over MCP (stdio transport), compatible with Claude Desktop, Cursor, and other mainstream MCP clients: `remagraph_store`, `remagraph_search`, `remagraph_status`, `remagraph_maintain`, and `remagraph_migrate_project`.

### `remagraph_store` — write memory

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

### `remagraph_search` — query memory

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

### `remagraph_status` — query a project's latest state

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

### `remagraph_maintain` — run DB maintenance

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

### `remagraph_migrate_project` — one-time cross-project migration

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

## Governance & Security

- **Rate limiting** — per-agent token bucket (60 calls / 60 seconds) to prevent abuse
- **Input validation** — `task_id` / `agent_id` are checked against format rules via Pydantic validators
- **Path safety** — `REMAGRAPH_STATE_DIR` rejects system directory paths
- **Audit rotation** — `audit-YYYYMM.jsonl`, split automatically by month
- **DB size cap** — SQLite `max_page_count` set to a 100MB soft limit
- **Migration** — built-in schema version tracking and a migration chain
- **Version-compatibility degradation** — when a database's schema version is newer than the running code, the database's own `min_reader_version`/`min_writer_version` decide one of three outcomes: fully compatible (normal reads and writes), read-only degraded (writes rejected, reads unaffected), or refused outright. Callers can learn this ahead of time via `remagraph_status`'s compatibility handshake fields, without waiting for a write to fail. See the "Version Compatibility" section of [`DESIGN.md`](./DESIGN.md)
- **Cross-project registry** — `project_registry` automatically records known projects and their `state_dir`, backing `remagraph_search`'s `cross_project_label` read-only cross-project lookups. See the "Cross-Project Collaboration" section of [`DESIGN.md`](./DESIGN.md)
- **Stale-record cleanup** — `cleanup_superseded()` can purge non-active records older than 90 days

## CLI Subcommands (for headless agents)

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

## Development & Verification

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

## Resources

- [`DESIGN.md`](./DESIGN.md) — the design source of truth: schema, arbitration rules, cross-project collaboration, version compatibility
- [`CHANGELOG.md`](./CHANGELOG.md) — version history
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how to contribute
- [`SECURITY.md`](./SECURITY.md) — how to report a vulnerability
- [`docs/architecture.md`](./docs/architecture.md) — system architecture
- [`docs/task-memory-convention.md`](./docs/task-memory-convention.md) — plain-language guide to the task-memory convention
- [`docs/audit.md`](./docs/audit.md) — the audit log contract
- [`docs/governance/checklist.md`](./docs/governance/checklist.md) — the governance checklist

## License

[Apache-2.0](./LICENSE). RemaGraph is an independent project — its source code is entirely self-authored and does not contain or derive from any other project's code.

## Support

If RemaGraph is quietly keeping your agents' memory straight, you can support its development on [Ko-fi](https://ko-fi.com/aikenlin) (card or PayPal) or directly via [PayPal](https://paypal.me/aikenlin). Entirely optional — RemaGraph is, and stays, free.
