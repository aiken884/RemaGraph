# RemaGraph — Design Specification / 設計規格書

This document exists in two full, structurally-parallel versions: [English](#english) and [繁體中文](#繁體中文).

---

## English

> **Nothing passes without leaving a trace.** RemaGraph is a lightweight MCP tool that captures the residue any AI coding agent naturally leaves behind after working on a task, so whoever picks up the work next can follow the trail. It complements CodeGraph: CodeGraph records "what's known to be wrong with this code"; RemaGraph records "what was left behind while working on it."

---

### Project Overview

| Item | Details |
|------|------|
| **Name** | RemaGraph |
| **Etymology** | Remanent — the external force is gone, the trace remains |
| **Positioning** | Independent open-source MCP server, one tool in an agent's toolbox |
| **Owner** | Aiken Lin's personal side project |
| **GitHub** | Personal account (private `aiken884/RemaGraph`) |
| **License** | Apache-2.0 (same as CodeGraph) |
| **PyPI** | Target `pip install remagraph`; **v1 not yet published** (currently `pip install -e .` / install from source) |
| **Package version** | `0.3.1` (see `pyproject.toml`); implementation closeout tracked in [`docs/reviews/v1-closeout-status.md`](docs/reviews/v1-closeout-status.md) |
| **Python** | 3.11+, dependencies managed with uv |
| **Relationship to external projects** | Fully independent (no code coupling), and fully usable standalone by any AI coding agent today. It also exposes one stable external contract (the Audit Contract, see "External Boundaries" below) so that a higher-level orchestration or scheduling system covering multiple agents/projects can integrate with it without RemaGraph needing any project-specific knowledge; an example integration is provided under `examples/` for reference. |

---

### External Boundaries

RemaGraph is an independent project; it is not aware of any external system. This section defines how it relates to any project that might build on top of it, to prevent future maintainers from mistakenly assuming a dependency direction.

#### RemaGraph does not know its consumers

RemaGraph **does not know**, and must never encode, the identity of any project that consumes it — whether that's a single calling agent, a fleet of coding agents sharing one memory store, or a higher-level orchestration/scheduling layer sitting on top of several agents or projects. This holds no matter how tightly, or how officially, a given deployment happens to integrate with RemaGraph in practice: no consumer's name **may appear** in RemaGraph's code, README, CHANGELOG, or API documentation.

This isn't a hypothetical concern — RemaGraph's own memory model exists precisely because this kind of multi-agent, multi-project setup is common: several agents (or several related projects) may want one agent's discoveries to become visible to another, or a higher-level scheduler may want to confirm a memory write actually happened before treating a task as complete. RemaGraph supports these use cases through its own generic, stable contracts (below), never through project-specific integration code.

#### How external scheduling systems consume RemaGraph

RemaGraph exposes exactly one stable external contract: the **Audit Contract** (see the "Audit Contract" subsection under "Audit" below). Any scheduling system built on top of RemaGraph only needs to know two things:

1. The audit file path: `~/.local/state/remagraph/audit.jsonl`
2. Look up, keyed by `task_id`, records where `action="remagraph_store"` and `status="stored"`

This document is the single source of truth (SOT) for the contract. If RemaGraph changes the audit schema, it will announce the change in its release notes. A consuming external scheduling system's config should point at RemaGraph's audit path, with a comment referencing this document.

#### Prohibited coupling

The following explicitly violate RemaGraph's boundary design:

- ❌ Importing any downstream/consumer project's code inside RemaGraph
- ❌ Describing RemaGraph in its README as "designed specifically for [some particular downstream project]"
- ❌ Naming or describing RemaGraph inside a downstream project's own README or API documentation as though it were a built-in component of that project
- ❌ Giving any RemaGraph MCP tool a name prefixed with a downstream project's brand

---

### Complementary Positioning with CodeGraph

| | CodeGraph | RemaGraph |
|---|---|---|
| **Memory subject** | Code symbols | Agent activity |
| **What it records** | Known issues with a piece of code, ADRs, conventions | What was learned while working on it, limitations discovered, handoff clues |
| **Metaphor** | The structure itself (the skeleton) | The residue left after the structure operated (a byproduct) |
| **Example** | "`AcpxAdapter` — note that `OPENCODE_CONFIG` is not the final authority in the config merge chain" | "While fixing bug #5, discovered a race condition in acpx; whoever picks this up next should first check the upstream PR status" |
| **Query trigger** | Fetched automatically when a file is opened | Actively searched by the agent before starting work |

---

### Deployment Model

- v1 primarily uses the **stdio transport** (aligned with the mainstream MCP client ecosystem)
- `pip install remagraph`, ready to use with a single line of MCP config
- A Unix socket daemon is an advanced mode (on the vN roadmap)
- v1 is **single-process** (PID lock); it does not support multiple instances sharing a DB under concurrency
- State directory: `~/.local/state/remagraph/`
- A single SQLite file: `~/.local/state/remagraph/remagraph.db`
  - This file (the one under `DEFAULT_STATE_DIR`) additionally carries a cross-project shared `project_registry` table, sharing the same file with the `"default"` project's own memories (see "Cross-Project Collaboration" below)
- Audit file: `~/.local/state/remagraph/audit.jsonl` (mode 0600)

---

### MCP Interface

Five tools, called directly by the agent over MCP (v1 uses the stdio transport): `remagraph_store`, `remagraph_search`, `remagraph_status`, `remagraph_maintain`, `remagraph_migrate_project`.

#### `remagraph_store`

The agent writes a memory. This triggers five arbitration rules; once they all pass, the write goes into SQLite and is synced to the FTS5 index.

**Request:**
```json
{
  "project_id": "myproject",
  "task_id": "task-2026-07-21-003",
  "agent_id": "oc-dspro",
  "kind": "task_handoff",
  "summary": "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤",
  "learnings": [
    "錯誤發生在 opencode task tool 生成 child session 之後",
    "acpx 0.12.0 在 child session 生命週期管理上有 race condition"
  ],
  "handoff_note": "接手者：此錯誤與 G1 不同。G1 是 child session 未被註冊；這個新錯誤是 acpx transport 層誤判連線已斷。兩者根因不同。",
  "tags": ["acpx", "subagent", "deny-all", "bug"],
  "labels": ["dep:acpx", "topic:subagent"]
}
```
- `labels` (optional): namespaced tags (`namespace:value`). This is a separate concept from `tags` — `tags` is free-form with no format requirements; `labels` is a controlled vocabulary with explicit format rules and a length cap, used for `remagraph_search`'s `cross_project_label` exact-match lookups — see "Cross-Project Collaboration" below

**Response (success):**
```json
{
  "status": "stored",
  "id": "mem-20260721-001",
  "superseded": [],
  "invalidated_count": 0
}
```
- `superseded`: if this write is a `status_update`, lists the ids of existing memories automatically marked superseded; an empty array for any other kind
- `invalidated_count`: if the request includes an `invalidates` parameter, the actual count of memories marked invalidated

**Response (rejected by arbitration):**
```json
{ "status": "rejected", "reason": "summary_too_short", "detail": "summary must be >= 30 characters, currently 12" }
```

**Response (label format violation):**
```json
{ "status": "rejected", "reason": "invalid_label", "detail": "label 'Dep:acpx' does not match the namespace format ^[a-z]+:[a-zA-Z0-9_-]+\\Z (e.g. 'dep:opencode', 'topic:auth', 'kind:bug')" }
```

**Response (read-only degradation rejection):**
```json
{ "status": "rejected", "reason": "read_only_mode", "detail": "This connection is currently in read-only mode (the database schema has been upgraded beyond this code's write-compatible version); this write has been rejected. Please upgrade the remagraph package and retry." }
```
- `read_only_mode`: triggered when the connection has been flagged read-only by the three-tier judgment described under "Version Compatibility" below, and this check happens before the five arbitration rules and before model2vec dedup — it never enters a transaction at all

#### `remagraph_search`

The agent queries memories. FTS5 BM25 full-text search + tag/kind filtering + time ordering.

**Request:**
```json
{
  "query": "subagent deny-all 連線錯誤",
  "top_k": 20,
  "kind": "task_handoff",
  "status": "active",
  "project_id": "myproject"
}
```
- `project_id` (optional): restricts the query to a single project; when omitted and `all_projects=true`, this filter is removed (see below)
- `all_projects` (`bool`, optional, default `false`): when `true`, removes the `project_id` filter "within the current database file" — but each project is already its own separate SQLite file, so this flag never opens any other file
- `cross_project_label` (optional): when provided, switches entirely to the cross-project label search path — full-text/filter parameters such as `query`/`kind`/`tags` no longer apply, matching is purely by exact label match. This is an orthogonal dimension from `all_projects`; see "Cross-Project Collaboration" below.

**Response:**
```json
{
  "results": [
    {
      "id": "mem-20260721-001",
      "project_id": "myproject",
      "summary": "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤",
      "agent_id": "oc-dspro",
      "kind": "task_handoff",
      "task_id": "task-2026-07-21-003",
      "timestamp": "2026-07-21T14:30:00Z",
      "score": 0.87,
      "learnings": ["acpx 0.12.0 在 child session 生命週期管理上有 race condition"],
      "handoff_note": "接手者：此錯誤與 G1 不同……",
      "tags": ["acpx", "subagent", "deny-all", "bug"],
      "status": "active",
      "created_at": "2026-07-21T14:30:00.123Z",
      "updated_at": "2026-07-21T14:30:00.123Z"
    }
  ],
  "has_more": false,
  "cross_project_fanout_capped": false
}
```
- `top_k` defaults to **20**, max **100**
- `has_more`: `true` means there are more results (`LIMIT top_k + 1` fetches k+1 rows), so the agent can narrow the query; v1 does not provide an exact `total_matches`
- `query=""` (empty string): returns empty `results` + `has_more=false`, never raises, logs a warning
- FTS5 query input must be sanitized server-side before use (stripping/escaping special characters such as `*`, `"`, `AND`, `OR`, `NOT`) to prevent unexpected syntax errors
- Every `results` item covers the full field set of the memories table (except `embedding`) — `learnings`/`handoff_note`/`tags`/`status`/`created_at`/`updated_at` are all returned in full (these were once dropped by `_row_to_result()`; this has since been fixed and a regression test added — see CHANGELOG)
- `cross_project_fanout_capped`: only meaningful when `cross_project_label` is used; `false` for every other query. `true` means the number of known projects exceeds the fan-out cap, so this search did not cover every known project — see "Cross-Project Collaboration" below
- When using `cross_project_label`, each result additionally carries a `source_project_id` field indicating its originating project

#### `remagraph_status`

Queries a project's current state. Returns all active `status_update` memories, deduplicated by `task_id` (only the latest per `task_id` is kept).

**Request:**
```json
{ "limit": 20, "project_id": "myproject" }
```
- `project_id` (optional): restricts to a single project; removed when `all_projects=true` (same semantics as `remagraph_search`'s `all_projects`)

**Response:**
```json
{
  "latest": [
    {
      "task_id": "task-2026-07-21-003",
      "summary": "subagent 委派 bug 正在修，等待上游 PR 審查",
      "agent_id": "oc-dspro",
      "timestamp": "2026-07-21T14:30:00Z"
    }
  ],
  "server_code_version": 6,
  "db_schema_version": 6,
  "min_reader_version": 1,
  "min_writer_version": 6,
  "upgrade_hint": null,
  "read_only": false
}
```
- `limit` defaults to **20**, max **100**
- **Version compatibility handshake** (from this field onward, `latest` is always accompanied by the fields below, reusing `db.get_compat_status()`): lets a caller learn its compatibility tier via `remagraph_status` before actually attempting a write and hitting a wall — it no longer has to wait for `remagraph_store` to fail first
  - `server_code_version`: the `SCHEMA_VERSION` of the currently running code
  - `db_schema_version`: the `schema_version` actually stored in the database's `_meta` table (read defensively)
  - `min_reader_version` / `min_writer_version`: forward-compatibility fields stored in the database (see "Version Compatibility" below); if the database predates this mechanism and hasn't run the corresponding migration, both always return `null` rather than raising
  - `upgrade_hint`: upgrade guidance text stored in the database; `null` when missing
  - `read_only`: whether the current connection is in read-only degraded mode (see "Version Compatibility" below)
  - These fields only appear on a successful response; in a tier-3 scenario (not even safe to read), `remagraph_status` cannot even open the connection, so it still returns the existing clean `{"status": "error", "reason": ...}` without any of the fields above mixed in

#### `remagraph_maintain`

Runs automatic DB maintenance (WAL checkpoint, prune superseded/invalidated memories, FTS5 optimize, VACUUM, integrity check). Gated by `maintenance.safety_validate_project()` — the same single-authority safety valve the CLI `maintain` subcommand uses — so calling this tool for a project whose `state_dir` doesn't resolve correctly, or that fails the naming-convention check blocking certain reserved project-id prefixes from using the default DB, fails closed rather than silently maintaining the wrong database.

**Request:**
```json
{ "project_id": "myproject", "force": false }
```
- `project_id` (required): the project to run maintenance on
- `force` (`bool`, optional, default `false`): when `true`, every maintenance step below runs unconditionally, ignoring its own threshold check (`_should_checkpoint`/`_should_prune`/`_should_optimize_fts`/`_should_vacuum`)
- Unlike the CLI `maintain` subcommand, this MCP tool exposes no `dry_run` — every call is a real maintenance run

**Response (success):**
```json
{
  "status": "ok",
  "stats": {
    "project_id": "myproject",
    "started_at": "2026-07-21T14:30:00+00:00",
    "wal_checkpoint": "done",
    "pruned_count": 3,
    "fts_optimized": true,
    "vacuum": "done",
    "size_before_mb": 62.4,
    "integrity": "ok"
  }
}
```
`stats` is produced directly by `maintenance.run_maintenance()`; each field is only present when the corresponding step actually executed:
- `wal_checkpoint`: `"done"` when `PRAGMA wal_checkpoint(TRUNCATE)` ran (triggered by `force` or by crossing `policy.wal_checkpoint_interval_ops`, default 1000 ops)
- `pruned_count`: number of `status != 'active'` memories deleted, scoped to `project_id`; only those older than `policy.prune_superseded_age_days` (default 90 days) are eligible, and the delete is `LIMIT`-capped per task by `policy.prune_superseded_max_per_task` (default 5) as a throttle
- `fts_optimized`: `true` when `INSERT INTO memories_fts(memories_fts) VALUES('optimize')` ran
- `vacuum`/`size_before_mb`: `VACUUM` runs once the DB file size (measured before the run) exceeds `policy.vacuum_threshold_mb` (default 50 MB); `size_before_mb` is the pre-VACUUM size, and the post-VACUUM size is separately recorded as the baseline for the next `_should_vacuum` growth check
- `integrity`: result of `PRAGMA quick_check` (runs when `policy.integrity_check_on_startup` — default `true` — or `force`); any value other than `"ok"` raises `RuntimeError` and is recorded as an `integrity_failed` audit violation via `_record_violation`

**Response (skipped — read-only schema tier):**
```json
{
  "project_id": "myproject",
  "started_at": "2026-07-21T14:30:00+00:00",
  "skipped": true,
  "skip_reason": "read_only_schema_tier"
}
```
- Maintenance is fundamentally a batch of write operations (WAL checkpoint, the prune `DELETE`, `VACUUM`, `ANALYZE`), none of which are safe to run against a database whose schema the currently running code doesn't fully understand yet. This check happens unconditionally — deliberately **not** overridable by `force`, since `force` expresses caller intent while this guards against a schema-compatibility risk; the two are orthogonal. See "Version Compatibility" below for how a connection gets flagged read-only in the first place.

**Response (error):**
```json
{ "status": "error", "reason": "..." }
```
- Raised e.g. when `safety_validate_project()` rejects the project (mismatched `state_dir`, a reserved-prefix project pointed at the default DB, `project.json` metadata mismatch, etc.)

#### `remagraph_migrate_project`

One-time migration of memories from a source project to a target project's independent DB (e.g. `default` → `otherproject`), marking the originals `invalidated` in the source. This tool and the CLI's `migrate-project` subcommand (`cli.cmd_migrate_project`) both call the same shared core implementation — `store.migrate_project_memories(from_project, to_project, dry_run=...)` — so the two entry points always produce the same end state for the same inputs; neither has its own separate copy of the migration logic.

**Request:**
```json
{ "from_project": "default", "to_project": "otherproject", "dry_run": false }
```

**Response:**
```json
{
  "status": "ok",
  "from": "default",
  "to": "otherproject",
  "dry_run": false,
  "migrated_count": 3,
  "skipped_ids": []
}
```

**What it actually does:**
1. Validates the target project via `safety_validate_project(to_project, require_env_match=False)` — the same safety valve used everywhere else (reserved-prefix rules, `project.json` metadata consistency, etc).
2. Resolves the *source* project's `state_dir` through the shared project registry, `db.get_registered_state_dir(from_project)` — not a hardcoded path. If `from_project` has never been registered (no prior `remagraph` invocation ever resolved a state_dir for it), the call raises `store.ProjectNotRegisteredError` rather than silently treating it as zero migratable records — this closes the historical bug where `from_project` was implicitly assumed to always be `"default"`. `from_project == "default"` is the one deliberate exception: it resolves via `db.get_state_dir()` (respecting the ambient `REMAGRAPH_STATE_DIR`/`REMAGRAPH_HOME`), because `"default"` is, by design, never registered during normal use (see `cli._project_id_for_conn`).
3. Reads `from_project`'s rows with a heuristic match on `task_id`/`tags`/`agent_id`/`summary` containing the target project name, `INSERT OR IGNORE`-ing matches into the target project's own DB with `project_id` forced to `to_project`, and marks the originals `invalidated` in the source (with a `migrated-to:<to_project>` breadcrumb appended to `learnings`). Both connections are opened via `db.connect_at_state_dir()`, which bypasses `REMAGRAPH_STATE_DIR` environment-variable resolution entirely and operates on the already-resolved, explicit paths — necessary because a long-lived MCP server process is bound to its own project's `state_dir` (see `server._bind_project`), and going through the ordinary env-var-driven `connect()` path here would silently open the *server's own* project DB instead of the actual migration source/target.
4. `dry_run=True` runs the exact same match query used by a real migration and reports the resulting count in `migrated_count` without writing anything — so a dry run and a subsequent real run always agree on the count, for the same underlying data.
5. If either the source or target database is currently in the read-only degraded schema-compatibility tier (see "Version Compatibility" below — its schema is newer than this code's write-compatible version), the migration raises `store.MigrationReadOnlyError` and is rejected cleanly rather than failing partway through or silently doing nothing.
6. If `from_project` and `to_project` happen to resolve to the *same physical directory* (e.g. because the caller's ambient `REMAGRAPH_STATE_DIR` coincidentally matches both), the call raises `ValueError` rather than opening two conflicting write transactions against the same database file.

**Response (error):**
```json
{ "status": "error", "reason": "..." }
```
- Raised when `safety_validate_project()` rejects the target project, when `from_project` was never registered, when the source database file doesn't exist, or when either side is read-only degraded.

---

### Memory Schema

Three `kind`s, each record containing: `id`, `task_id`, `agent_id`, `timestamp`, `kind`, `summary`, `learnings[]`, `handoff_note`, `tags[]`, `status`.

| kind | Purpose | Lifecycle | Example |
|------|------|----------|------|
| `task_handoff` | What was done, what was learned, handoff notes | Always active | "While fixing bug #5, discovered a race condition in acpx" |
| `status_update` | Project status (PR merged, bug discovered, awaiting a decision) | Auto-superseded by same `task_id` | "PR #4 merged, subagent bug being fixed" |
| `discovered_constraint` | A discovered limitation or pitfall | Always active; agent can explicitly `invalidates=[id]` | "`OPENCODE_CONFIG` is not the final authority in the config merge chain" |

The `status_update` supersede rule: when a new `status_update` is written, all prior `status_update`s with the **same `task_id`** are automatically marked `superseded`. `task_id` is an exact structured key — there's no semantic matching involved.

---

### Lightweight Arbitration (write side, zero LLM, zero human involvement)

Every `remagraph_store` request must pass all five rules below; any failure is rejected with a reason:

| # | Rule | Notes |
|---|---|---|
| 1 | `summary` ≥ 30 characters (`len(summary.strip())`) | Prevents vacuous entries ("fixed a bug") |
| 2 | `learnings` has at least one entry | If nothing was learned, it shouldn't become a memory |
| 3 | `handoff_note` ≥ 20 characters | Only enforced for `kind=task_handoff`; other kinds may leave it empty |
| 4 | model2vec dedup | `potion-multilingual-128M` (supports 101 languages including Chinese); cosine similarity ≥ 0.90 rejects the write (pending calibration against a Chinese-language dataset) and returns the id of the most similar existing memory. Model load failure is **fail-fast**, never silently degraded. |
| 5 | `agent_id` format + Lazy Registration | Format `^[a-z0-9_-]+$`, length **3–64** characters; auto-registered on first write |

#### Dedup supplementary notes

- Dedup only compares active memories of the same `kind`
- The dedup threshold in v1 is uniformly **0.90** (flagged "pending calibration against a Chinese-language dataset")
- Same-kind active count ≤ 2,000: full cosine comparison; above that, only the most recent 2,000 are compared
- Per-kind thresholds are optionally configurable (`task_handoff: 0.90`, `status_update: 0.88`, `discovered_constraint: 0.92`) — this is a suggestion only, not enforced
- `status_update` supersede is **strictly same-`task_id`**; v1 does not cross task boundaries
- `discovered_constraint` invalidation does **not** track bidirectionally (no `invalidated_by` back-reference field)

---

### Storage Layer: SQLite + FTS5

A single file, zero stdlib dependencies. Currently `SCHEMA_VERSION = 6` (migration chain v1→v6; v5→v6 added the `memory_labels` table below; v4→v5 added the forward-compatibility fields described in "Version Compatibility" below).

#### Schema (SQL)

```sql
-- 主表
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('task_handoff', 'status_update', 'discovered_constraint', 'fleet_member')),
    task_id    TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    timestamp  TEXT NOT NULL,                -- MCP 回傳用（精確到秒），與 created_at 語意不同
    summary    TEXT NOT NULL,
    learnings  TEXT NOT NULL DEFAULT '[]',   -- JSON array
    handoff_note TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]',   -- JSON array
    status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'invalidated')),
    embedding  BLOB,                         -- model2vec vector (np.float32 little-endian '<f4')，v1 只存不查
    created_at TEXT NOT NULL,                -- ISO 8601 UTC（內部審計用，精確到毫秒）
    updated_at TEXT NOT NULL
);

-- FTS5 虛擬表（BM25 全文檢索，trigram tokenizer 支援中文 CJK）
-- 若 runtime SQLite < 3.34 不支援 trigram，降級方案為手動 bigram 前處理
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    summary,
    learnings,
    handoff_note,
    tags,
    content='memories',
    content_rowid='rowid',
    tokenize='trigram'
);

-- INSERT 自動同步 FTS5
CREATE TRIGGER IF NOT EXISTS memories_ai
AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;

-- UPDATE 自動同步 FTS5（防止 UPDATE 後 index 失步）
CREATE TRIGGER IF NOT EXISTS memories_au
AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;

-- DELETE 自動同步 FTS5
CREATE TRIGGER IF NOT EXISTS memories_ad
AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
END;

-- 效能 indexes
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(task_id);
CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC);

-- 版本追蹤（自 v4→v5 起額外存放前向相容性欄位，見下方「版本相容性」小節）
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 每個記憶可掛上多個命名空間化標籤（schema v5→v6），供跨專案標籤搜尋使用
-- （見下方「跨專案協作」章節）。標籤格式（namespace:value）由應用層
-- （arbitration.validate_labels()）驗證，本表不對 label 內容加 CHECK 約束
-- （與 tags 欄位一致）。
CREATE TABLE IF NOT EXISTS memory_labels (
    memory_id TEXT NOT NULL REFERENCES memories(id),
    label     TEXT NOT NULL,
    PRIMARY KEY (memory_id, label)
);
CREATE INDEX IF NOT EXISTS idx_memory_labels_label ON memory_labels(label);
```

#### Version Compatibility (`_meta` forward-compatible fields + three-tier judgment)

Background: an independently pinned, older consumer that opens a database whose `schema_version` is newer than its own code previously had no option but to refuse to open it at all (`MigrationError`), with the error message hardcoded into that old code — so even if the message text were improved later, the old consumer would never see it, because it's still running its own old source snapshot (two real-world cases, MegaNote and Meshtastic, actually hit "schema_version newer than the code, cannot downgrade" and gave up on writing). The fix: store the upgrade guidance and compatibility boundaries inside the database's own `_meta` table — which a consumer will always open and read — rather than only in code string constants.

**New `_meta` fields** (introduced starting from schema v4→v5 by `_migrate_v4_to_v5()`; written immediately when a brand-new database is created, without waiting for the migration chain to reach that point):

| Field | Description |
|------|------|
| `min_reader_version` | The oldest code `SCHEMA_VERSION` this database allows to be "read." Defaults to `"1"`. |
| `min_writer_version` | The oldest code `SCHEMA_VERSION` this database allows to "write." Every migration that touches fields/CHECK constraints updates this to the `SCHEMA_VERSION` in effect at that time (e.g., v4→v5 writes `"5"`; v5→v6 only adds the `memory_labels` table without modifying `memories`' own fields/CHECK constraints, so `min_writer_version` is deliberately left unchanged). |
| `upgrade_hint` | Self-contained, code-constant-independent upgrade guidance text (in English), shown alongside rejection/degradation messages. |

Reading these three fields always goes through defensive reads (`_read_meta_int_defensively()` / `_read_upgrade_hint_defensively()`): any failure — missing table, missing field, type mismatch, etc. — returns `None` rather than raising, and never interrupts the existing reject/degrade flow.

**`db.connect()`'s three-tier version compatibility judgment** (`_handle_newer_than_code_schema()`, triggered only when the database's `schema_version` is newer than the code's `SCHEMA_VERSION`):

| Tier | Condition | Behavior |
|------|------|------|
| Tier 1: Fully compatible | `SCHEMA_VERSION >= min_writer_version` | Normal read/write, identical to the previous `schema_version <= SCHEMA_VERSION` behavior — nothing special happens |
| Tier 2: Read-only degradation | `min_reader_version <= SCHEMA_VERSION < min_writer_version` | `connect()` **no longer raises** — it returns a usable connection, but flags it read-only on the connection object (see "What read-only mode means to the caller" below) |
| Tier 3: Full rejection | `SCHEMA_VERSION < min_reader_version` | Preserves existing behavior: `connect()` raises `MigrationError` (one of three static messages + the defensively-read `upgrade_hint`) |

If reading any version field fails or is missing (e.g., the database predates this mechanism and hasn't run the v4→v5 migration), `min_reader_version`/`min_writer_version` are always treated as equal to the database's own `schema_version` — falling back to the strict all-or-nothing behavior that existed before this mechanism was introduced; a lenient default is never substituted.

**What read-only mode means to the caller:**

- The read-only flag lives on the connection object (`db.READ_ONLY_ATTR` / `db.READ_ONLY_DETAIL_ATTR`), because the native `sqlite3.Connection` is a pure C-extension type that doesn't support arbitrary attribute assignment; `connect()` always uses `_MarkedConnection` (an empty subclass of `sqlite3.Connection`) as its `factory=`, so the connection object can safely carry the flag while still being a complete `sqlite3.Connection` instance (existing `isinstance` checks and type annotations are unaffected)
- `remagraph_search` / `remagraph_status` (`search_memories()` / `get_status()`) are entirely unaffected — queries on a read-only connection run exactly as normal
- `remagraph_store` (`process_store()`) checks this flag at the very top of the function — before the safety valve, before the five arbitration rules, before model2vec dedup; if read-only, it returns `status="rejected"` / `reason="read_only_mode"` immediately, never entering a transaction
- Automatic maintenance (`light_maintenance_on_connect()` → `run_maintenance()`, including the `remagraph_maintain` MCP tool) likewise checks the read-only flag as soon as it obtains a connection (whether passed in by the caller or opened internally); if read-only, it skips **all** write operations — WAL checkpoint, prune, FTS optimize, VACUUM, ANALYZE, integrity check — and returns `stats={"skipped": true, "skip_reason": "read_only_schema_tier"}` along with a `maintenance_skipped_read_only` audit event; this protection still applies even if the caller passed `force=True` — read-only degradation guards against schema-compatibility risk, which is orthogonal to whether the caller wants to force execution

#### Query example

FTS5 query input must be sanitized server-side before use (stripping/escaping FTS5 special characters such as `*`, `"`, `AND`, `OR`, `NOT`) to prevent unexpected syntax errors.

```sql
-- BM25 全文檢索 + kind 過濾 + tag 過濾 + 時間排序
SELECT m.*, fts.rank
FROM memories_fts fts
JOIN memories m ON fts.rowid = m.rowid
WHERE memories_fts MATCH 'subagent deny-all error'
  AND m.kind = 'task_handoff'
  AND m.status = 'active'
ORDER BY fts.rank, m.created_at DESC
LIMIT 20;
```

#### `embedding` field strategy

- v1 only stores the model2vec embedding as a BLOB; it performs no vector querying
- Format: `np.float32` little-endian (write: `.astype('<f4').tobytes()`, read back: `np.frombuffer(b, dtype='<f4')`)
- stdio mode: **lazy-loads** the model (process lifetime is short, so this avoids cold-start latency)
- Model load failure: **fail-fast** (fails at startup or on the first call, with a clear error) — never silently degraded
- sqlite-vec is not added
- If a future v2 needs semantic search, `pip install remagraph[vector]` builds a sqlite-vec index on top of the existing BLOBs, without recomputing every embedding

#### `pyproject.toml` (zero dependencies)

```toml
[project]
name = "remagraph"
requires-python = ">=3.11"
dependencies = [
    "model2vec>=0.1.0",   # potion-multilingual-128M（支援中文 CJK）
    "mcp>=1.0",           # MCP Python SDK（stdio transport）
]
# sqlite3 是 stdlib，不列

[project.optional-dependencies]
vector = ["sqlite-vec>=0.1.0"]
```

---

### Cross-Project Collaboration

Each `project_id` in RemaGraph maps to a fully independent state_dir / SQLite file (see "Deployment Model"), and by default they are unaware of one another — each is its own island. This section describes the two-layer mechanism that lets an agent "see" that other projects exist when it needs to, and precisely query their labels — this is the foundation for later cross-project query capabilities such as `recall_related`, and does not itself include full-text search or a relationship graph.

#### Cross-project registry (`project_registry`)

A lightweight, shared "registry" recording which `project_id`s exist and where each one's state_dir is:

| Field | Description |
|------|------|
| `project_id` | Primary key |
| `state_dir` | The absolute path currently resolved for this project |
| `first_seen` / `last_seen` | UTC timestamp (ISO 8601, second precision) of first / most recent registration |

- The `remagraph.db` that lands under `DEFAULT_STATE_DIR` (`~/.local/state/remagraph/`) shares the same file with the `"default"` project's own memories — because this is the one location that can be resolved without any extra configuration, for any project, at any time
- `CREATE TABLE IF NOT EXISTS`, idempotent, deliberately independent of the existing per-project migration chain (it does not advance alongside `SCHEMA_VERSION` bumps): that chain runs once against **each** project's own database; folding the registry into it would add a table unrelated to that project into every project's private DB, polluting the existing "islands unrelated to each other" design
- **Automatically registered, no explicit call required**: `maintenance.resolve_project_state_dir()` (called by any operation carrying a `project_id`, including the safety valve `safety_validate_project()`) calls `db.register_known_project()` for a best-effort upsert every time it resolves a state_dir — normal usage gets recorded automatically; any failure (directory can't be created, DB locked, insufficient permissions, ...) is always swallowed and never affects the caller's main flow. `first_seen` is only written the first time a project appears; existing rows only ever update `state_dir` (if changed) and `last_seen`
- `db.list_known_projects()`: reads every row of the registry, always pointing at the real `DEFAULT_STATE_DIR`, unaffected by the caller's current `REMAGRAPH_STATE_DIR` / `REMAGRAPH_PROJECT` environment variables; any read failure always returns an empty list rather than raising
- `db.connect_foreign_project_readonly(project_id)`: opens a genuinely read-only connection to another already-registered project (SQLite URI `file:<path>?mode=ro` + `PRAGMA query_only=1`), completely bypassing `db.connect()` / `get_state_dir()` / `safety_validate_project()` / `light_maintenance_on_connect()` (this is architectural — it never goes through those code paths at all, it isn't merely skipped via a flag); for an unregistered project, or one whose state_dir/db file no longer exists (e.g., already deleted), it always returns `None` and never accidentally creates a blank new database — `mode=ro` makes SQLite raise `OperationalError` immediately at `connect()` time when the file doesn't exist, replacing the TOCTOU race window that an "`exists()` pre-check, then a normal-mode `connect()`" approach would leave open (the file being deleted between the check and the connection would otherwise silently create a new, normal-looking-but-blank database)

#### Labels (`memory_labels`) and cross-project label search

Every memory can additionally carry multiple "namespaced" labels (the `memory_labels` table introduced in schema v5→v6, DDL shown under "Storage Layer" above). This is a separate concept from the existing `tags` field, deliberately not merged with it: `tags` is the existing free-form field with no format requirements, used for the existing tag-filtered search; `labels` is a new controlled vocabulary with explicit format rules, dedicated to the exact-match cross-project use case described in this section.

**Label format**: `namespace:value`, e.g. `dep:opencode`, `topic:auth`, `kind:bug`.

- Full rule: `^[a-z]+:[a-zA-Z0-9_-]+$` (see `arbitration.LABEL_REGEX`; the implementation anchors with `\Z` rather than `$`, to avoid the Python regex quirk where `$` also matches "immediately before a trailing newline" — which would otherwise let a string with a trailing newline pass as valid)
- `namespace` must be lowercase letters only; the rule itself doesn't restrict which specific prefixes may be used, but convention recommends a small, controlled set — e.g. `dep:` (dependency), `topic:` (topic), `kind:` (category) — the goal being to prevent labels from gradually devolving into a fragmented, inconsistent free-form string
- `value` allows upper/lowercase alphanumerics, underscores, and hyphens, consistent with the existing character-set convention for `project_id` / `task_id` / `agent_id`
- Length cap of **64 characters** (for the entire `namespace:value` string), consistent with the existing 64-character cap convention for `project_id` / `task_id` / `agent_id`
- The `labels` parameter of `remagraph_store`: if any single label fails the format check (including being too long), the **entire batch is rejected** (`StoreResponse(status="rejected", reason="invalid_label")`) — it does not silently drop the bad ones and keep the valid ones. The whole value of "labels" as a controlled vocabulary rests on this: silently skipping bad ones would only mean the caller never finds out its format is wrong, which over time just encourages label fragmentation.
- Labels are written in the same transaction as the memory's INSERT — they either commit together or roll back together; duplicate labels are automatically deduplicated (no error results from a `(memory_id, label)` composite primary key conflict)

**`remagraph_search`'s `cross_project_label` parameter:**

- When provided, it takes a query path entirely independent of full-text search — matching purely by exact label match; other full-text/filter parameters such as `query` / `kind` / `tags` do not apply. `status` filtering defaults to `active`, and can be overridden by the caller.
- Query scope: (a) the current connection's own project's `memory_labels`, plus (b) opening a read-only connection, one by one via the registry, to "other" known projects, merging the results and annotating each result with `source_project_id` to indicate its originating project
- This is a completely independent dimension from the existing `all_projects` flag, and neither replaces the other: `all_projects` only removes the `project_id` filter "within the current database file" (each project is its own separate file, so this flag never opens any other file); `cross_project_label` is the one that actually opens other projects' independent database files, via the registry
- **Fan-out cap defaults to 50, is configurable, hard cap 200** (`search._CROSS_PROJECT_FANOUT_CAP`, originally hardcoded at 20; adjusted following PPLX adversarial architecture review consensus): the maximum number of "other" known projects' databases a single search will open (not counting the connection's own project, which is queried directly and doesn't count against the cap). Overridable via the CLI `--fanout-cap` flag or the `REMAGRAPH_FANOUT_CAP` environment variable, both clamped within the hard cap of 200 (only `REMAGRAPH_FANOUT_HARD_CAP` can raise it further) — deliberately not offering an "unlimited" escape hatch, since the number of known projects only grows monotonically over time (there is currently no automatic cleanup mechanism); without a cap, a single fan-out could open too many concurrent SQLite connections, risking OOM in resource-constrained environments such as CI/containers. When the cap is exceeded, it does **not** silently truncate and pretend full coverage: `SearchResponse.cross_project_fanout_capped` is set to `true`, accompanied by three counters — `candidates_total`/`candidates_searched`/`candidates_skipped` (`total == searched + skipped` always holds; both exclude the caller's own project to avoid an off-by-one), and the CLI exits with code `2` on truncation (distinct from `0` = complete, `1` = genuine error), letting the caller clearly distinguish "complete results," "incomplete results," and "the tool itself errored," rather than misreading a truncation as an empty result.
- Registered but currently unreachable projects (e.g., directory deleted, or that project's database has not yet been upgraded to a schema version that has the `memory_labels` table) are gracefully skipped, so a single project's failure does not fail the entire search
- Results are deduplicated by `(source_project_id, id)`: even when the caller does not supply `project_id` (and thus cannot pre-determine and skip its own project within the fan-out loop), the same memory is still guaranteed never to be returned twice. This dedup key has one already-fixed edge case: if the caller's own connection and some registered candidate project are **physically the same SQLite file** (e.g., the local `default` state dir happens to point at the same path as some registered project), the two appearances would carry different `source_project_id` strings, and this key alone would not catch the duplicate — so the fan-out loop additionally uses `PRAGMA database_list` to get the absolute physical file path each side is actually connected to, and skips whenever they're physically identical, rather than relying solely on `project_id` string comparison

#### Project isolation safety valve (`safety_validate_project`) and `remagraph serve`'s single-project binding

`project_id` itself is just a label field on a data row — **what actually determines which physical SQLite file gets connected is the `REMAGRAPH_STATE_DIR`/`REMAGRAPH_PROJECT` environment variables** (or a `state_dir` explicitly passed to `connect()`). `db.connect(project_id=...)` has a built-in safety valve, `maintenance.safety_validate_project(project_id)`: it computes, via `resolve_project_state_dir(project_id)`, the authoritative state_dir this `project_id` should map to, and reads the `project.json` in that directory (`db.validate_project_metadata()`) to confirm the `project_id` recorded there matches the one currently being requested — a mismatch (that directory was previously legitimately used by a different project) always raises `SafetyValveError`, logs a `project_metadata_mismatch` audit violation, and blocks before any write can happen.

This safety valve only takes effect when the caller explicitly passes `project_id` into `connect()`; the CLI subcommands and `remagraph serve` now both do so (before the 2026-07-25 fix, both called `_db.connect()` with zero arguments, so the safety valve was never triggered at all — which file actually got connected purely depended on whatever the process's environment happened to be at that moment. This was the root cause of an actual production incident: one project's `serve` process inherited another project's environment variables and silently wrote its data into the latter's real database.)

`remagraph serve`'s project-binding model (PPLX architecture review consensus, see the pending-decisions record below): **a single `serve` process is strictly bound to a single project, and fails fast at startup** — it is not "the first call determines the binding":
- At startup, either `--project <id>` or the `REMAGRAPH_PROJECT` environment variable must be provided; if both are absent, it exits non-zero immediately, without entering the MCP stdio loop
- On a successful binding, it prints a diagnostic message (the actually-bound `project_id` and the resolved state_dir); if the connection is detected to be in read-only degraded mode, it also warns upfront
- After that, any tool call (`remagraph_store`/`search`/`status`) that passes a `project_id` different from the bound one and non-`None` always returns a structured error — it never silently reuses or switches connections
- **Deliberately does not support a single process dynamically routing across multiple projects** (explicitly rejected by PPLX): under SQLite WAL mode, checkpoint timing across multiple long-lived connections would interfere with each other; connection-cache eviction/close timing management becomes complicated; and the safety valve itself assumes "the current process's environment corresponds to exactly one project_id" — dynamic routing would break that assumption, requiring the safety valve's semantics to be redesigned from scratch. When multiple projects need to be served at once, the MCP host layer should start one `remagraph serve --project <id>` process per project, rather than having a single server route across projects — this is also the division of responsibility the MCP spec itself recommends

---

### Audit

#### Design principles

RemaGraph manages its own audit trail and does not depend on any external system. An external scheduling system, if one exists, verifies whether an agent completed a memory write by reading this file.

#### Path

`~/.local/state/remagraph/audit.jsonl` (mode 0600, directory mode 0700)

#### Schema

```jsonl
{"ts":"2026-07-21T14:23:01.234Z","actor_id":"agent_id/task_id","action":"remagraph_store","mem_id":"mem-20260721-001","task_id":"task-2026-07-21-003","status":"stored","error":null}
```

| Field | Description |
|------|------|
| `ts` | ISO 8601 UTC (`Z` suffix, local time not supported), consistent with common external audit-log timestamp conventions |
| `actor_id` | Composite form `{agent_id}/{task_id}` |
| `action` | Fixed to `remagraph_store` for a `remagraph_store` transaction (see the publicly announced Audit Contract below — this value does not change); the same `audit-YYYYMM.jsonl` file is also written to by `append_event` for maintenance/lifecycle events with different `action` values (e.g., `safety_violation`, `maintenance_completed`, `maintenance_light_failed`) — these records have a different, simpler structure (no `task_id`, `agent_id`, `kind`, `status`, `mem_id` fields) |
| `mem_id` | The memory id after a successful write, for external systems to cross-reference |
| `task_id` | An explicit index key; external systems can grep it directly |
| `status` | `"stored"` or `"error"` |
| `error` | On failure, the exception class name (not the traceback or message, per minimal-disclosure principle) |

- v1 does not rotate audit.jsonl (a single append-only file, deferred to v2)

#### Audit Contract (for external scheduling systems)

The contract RemaGraph publishes externally (this subsection is citable independently):

- **Path**: `~/.local/state/remagraph/audit.jsonl`
- **Verification method**: look up the audit keyed by `task_id`, find the record where `action="remagraph_store"` and `status="stored"`
- **Behavior when not written**: if no record is found, the scheduling system decides its own handling strategy (e.g., sending a follow-up prompt to remind the agent, or logging `memory_write_failed`)
- **Schema changes**: if RemaGraph changes the audit schema, it will announce this in its release notes

---

### CI/CD Quality Gates

Follows a standard, widely-adopted open-source CI/CD gate set:

| Gate | Configuration |
|------|------|
| **Tests** | pytest (unit tests + MCP integration tests) |
| **Coverage** | `pytest --cov=src/remagraph --cov-fail-under=80` |
| **Mutation testing** | mutmut (scoped to `arbitration.py` + `dedup.py`, `runner = "pytest"`, non-blocking but continuously tracked) |
| **Secret scanning** | gitleaks (every push / PR, full Git history) |
| **Sign-off** | DCO (`git commit -s`) |
| **CI** | GitHub Actions: ubuntu × macos × Python 3.11–3.13 |

---

### Project Structure

```
remagraph/
├── pyproject.toml
├── README.md
├── DESIGN.md                       # 本文件
├── LICENSE                          # Apache-2.0
├── .github/
│   └── workflows/
│       ├── test.yml
│       ├── gitleaks.yml
│       ├── pip-audit.yml
│       ├── mutmut.yml
│       └── publish.yml
├── src/
│   └── remagraph/
│       ├── __init__.py
│       ├── server.py               # MCP server entrypoint（stdio transport）
│       ├── store.py                # SQLite + FTS5 讀寫
│       ├── search.py               # BM25 查詢邏輯
│       ├── dedup.py                # model2vec 去重
│       ├── arbitration.py          # 五條仲裁規則
│       ├── audit.py                # 自管 audit writer
│       ├── models.py               # Pydantic schema
│       └── db.py                   # SQLite 連線管理與 migration
├── tests/
│   ├── test_store.py
│   ├── test_search.py
│   ├── test_dedup.py
│   ├── test_arbitration.py
│   └── test_audit.py
└── docs/
    └── audit.md                    # Audit Contract（外部系統引用本節即可）
```

---

### Design Decision History

Full planning discussion records are kept in the project's internal design-review archive (not part of this public specification), including:

1. Requirements clarification: multi-agent shared memory, agents writing and reading their own memories, with no centralized orchestrator gating individual writes
2. Technology selection: four rounds of PPLX adversarial review (dedup approach, lifecycle management, behavior guidance, audit architecture, storage layer evaluation)
3. Naming iteration: five rounds of PPLX discussion, ultimately settling on RemaGraph (Remanent)
4. Architectural positioning: moving from being conceived as a sub-tool inside a larger, closed multi-project ecosystem, to a fully independent, general-purpose MCP server usable by any AI coding agent

---

### Future Upgrade Path (out of v1 scope)

```
v1: SQLite + FTS5 + trigram tokenizer（零依賴，BM25 全文檢索支援中文，stdio transport）
  ↓
v2: SQLite + FTS5 + sqlite-vec（語意搜尋，pip install remagraph[vector]）
  ↓
vN: Unix socket daemon（長駐 process，減少冷啟動延遲）│ DuckDB（百萬級資料，複雜分析查詢）
  ↓
vN+1: PostgreSQL + pgvector（多人協作，雲端服務）
```

Each stage's trigger is actual usage and user feedback, not pre-planning.

---

### PPLX-CONSENSUS-APPLIED

> 2026-07-21 PPLX adversarial review (`docs/design/reviews/pplx-design-review-2026-07-21.md`)
> Consensus action list (`docs/design/reviews/pplx-consensus-actions-2026-07-21.md`)

- [x] B1: dedup model `potion-base-8M` → `potion-multilingual-128M`, declares v1 supports Chinese, fail-fast
- [x] B2: FTS5 DDL changed to `tokenize='trigram'`, corrected the CJK tokenizer description, added a fallback explanation
- [x] B3: deployment model changed to v1 primarily stdio, Unix socket daemon moved to the vN roadmap
- [x] C1: `handoff_note` rule #3 limited to enforcement only for `task_handoff`
- [x] C2: FTS5 CJK tokenization description already corrected alongside B2
- [x] C3: `remagraph_status` limit defaults to 20, max 100
- [x] C4: `remagraph_search` top_k defaults to 20, max 100
- [x] C5: added the `memories_au` AFTER UPDATE trigger
- [x] C6: DDL adds the `timestamp` field (distinct semantics from `created_at`)
- [x] C7: same as B3
- [x] C8: `StoreResponse` extended with `superseded` / `invalidated_count` fields
- [x] R1–R9: all design write-backs (model name, Chinese-language support, trigram, agent_id length, has_more, sanitize, mcp dependency, etc.)
- [x] Q1–Q8 decisions: dedup threshold 0.90, same-`task_id` supersede, no bidirectional invalidate, 2,000-record cap, float32 LE, UTC Z, error class name only, PID lock, empty query doesn't raise, `len(strip())`, stdio lazy load
- [x] N4: the precision difference between memory `timestamp` (seconds) vs. audit `ts` (milliseconds) is noted in the DDL comment
- [x] N9: FTS5 sanitize is documented in the query example and search description
- [x] N10: the `mcp>=1.0` dependency is documented in the pyproject.toml snippet
- [x] No occurrence anywhere of `potion-base-8M`; no leftover "v1 primarily Unix socket" description

---

## 繁體中文

> **凡走過必留下痕跡。** RemaGraph 是一把輕量的 MCP 工具，任何 AI coding agent 走過後自然留下的殘跡，後人可循跡。與 CodeGraph 互補：CodeGraph 記「這段程式碼有什麼已知問題」，RemaGraph 記「處理時留下了什麼痕跡」。

---

### 專案基本資訊

| 項目 | 內容 |
|------|------|
| **名稱** | RemaGraph |
| **詞源** | Remanent（殘磁）——外部力量走了，痕跡還在 |
| **定位** | 獨立開源 MCP server，agent 工具箱裡的一把工具 |
| **擁有者** | Aiken Lin 個人 side project |
| **GitHub** | 個人帳號（private `aiken884/RemaGraph`） |
| **授權** | Apache-2.0（與 CodeGraph 相同） |
| **PyPI** | 目標 `pip install remagraph`；**v1 尚未 publish**（目前 `pip install -e .`／原始碼安裝） |
| **套件版本** | `0.3.1`（見 `pyproject.toml`）；實作收斂 [`docs/reviews/v1-closeout-status.md`](docs/reviews/v1-closeout-status.md) |
| **Python** | 3.11+，uv 管理依賴 |
| **與外部專案的關係** | 完全獨立（無程式碼耦合），今天就能被任何 AI coding agent 直接、獨立使用。同時對外暴露一個穩定合約（Audit Contract，見下方「對外邊界」），讓涵蓋多個 agent／專案的上層協調或排程系統可以與它整合，而不需要 RemaGraph 具備任何特定專案的知識；範例整合見 `examples/` |

---

### 對外邊界

RemaGraph 是獨立專案，不認識任何外部系統。本節界定它與任何架構在它之上的專案的關係，防止未來維護者誤設依賴方向。

#### RemaGraph 不認識自己的使用方

RemaGraph **不知道**、也絕不應該寫死任何消費它的專案身分——不論那是單一呼叫 agent、一組共用同一份記憶庫的 agent 艦隊，還是架在多個 agent／專案之上的上層協調或排程系統。不論實務上某個部署與 RemaGraph 整合得多緊密、多正式，這件事都成立：任何使用方的名稱**都不應出現**在 RemaGraph 的程式碼、README、CHANGELOG、API 文件中。

這不是憑空的顧慮——RemaGraph 自己的記憶模型之所以存在，正是因為這種多 agent、多專案的情境很常見：多個 agent（或多個相關專案）可能希望一個 agent 的發現能被另一個看見，或者上層排程系統可能想在把任務判定為完成之前，先確認記憶確實被寫入了。RemaGraph 是透過自己通用、穩定的合約（見下方）支援這些情境，而不是靠針對特定專案的整合程式碼。

#### 外部排程系統如何消費 RemaGraph

RemaGraph 對外只暴露一個穩定的合約：**Audit Contract**（詳見下方「審計」章節的「Audit Contract」小節）。任何架在 RemaGraph 之上的排程系統只需要知道兩件事：

1. audit 檔案路徑：`~/.local/state/remagraph/audit.jsonl`
2. 以 `task_id` 為 key 查 `action="remagraph_store"` 且 `status="stored"` 的記錄

合約的單一真相來源（SOT）是本文件。RemaGraph 若修改 audit schema，會在 release note 中公告。消費方（外部排程系統）的 config 應指向 RemaGraph 的 audit 路徑，並附註解指向本文件。

#### 禁止的耦合

以下行為明確違反 RemaGraph 的邊界設計：

- ❌ 在 RemaGraph 的程式碼中 import 任何下游／消費方專案的程式碼
- ❌ 在 RemaGraph 的 README 中提及「專為〔某個特定下游專案〕設計」
- ❌ 在某個下游專案自己的 README 或 API 文件中，把 RemaGraph 描述成該專案的內建元件
- ❌ 讓 RemaGraph 的 MCP tool 名稱帶有下游專案品牌前綴

---

### 與 CodeGraph 的互補定位

| | CodeGraph | RemaGraph |
|---|---|---|
| **記憶主體** | 程式碼符號 | agent 活動 |
| **記什麼** | 這段程式碼有什麼已知問題、ADR、慣例 | 處理時學到了什麼、發現了什麼限制、交接線索 |
| **隱喻** | 結構本身（骨架） | 結構運作後留下的殘跡（副產品） |
| **範例** | 「`AcpxAdapter` 要注意 OPENCODE_CONFIG 不是設定合併鏈最終權威」 | 「修 bug #5 時發現 acpx 有 race condition，接手者請先確認上游 PR 狀態」 |
| **查詢觸發** | 開啟檔案時自動撈 | agent 開工前主動搜 |

---

### 部署形態

- **v1 主要使用 stdio transport**（符合 MCP 主流 client 生態）
- `pip install remagraph`，一行 MCP config 即可用
- Unix socket daemon 為進階模式（vN 路線圖）
- v1 **單 process**（PID 鎖），不支援多實例共用 DB 與 concurrency
- state 目錄：`~/.local/state/remagraph/`
- 單一 SQLite 檔案：`~/.local/state/remagraph/remagraph.db`
  - 此檔案（`DEFAULT_STATE_DIR` 底下那一份）額外承載一張跨專案共用的 `project_registry` 表，與 `"default"` 專案自己的 memories 共用同一份檔案（見下方「跨專案協作」章節）
- 審計檔案：`~/.local/state/remagraph/audit.jsonl`（0600）

---

### MCP 介面

五個 tool，agent 透過 MCP（v1 使用 stdio transport）直接呼叫：`remagraph_store`、`remagraph_search`、`remagraph_status`、`remagraph_maintain`、`remagraph_migrate_project`。

#### `remagraph_store`

agent 寫入記憶。觸發五條仲裁規則，通過後寫入 SQLite + 同步 FTS5 index。

**Request：**
```json
{
  "project_id": "myproject",
  "task_id": "task-2026-07-21-003",
  "agent_id": "oc-dspro",
  "kind": "task_handoff",
  "summary": "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤",
  "learnings": [
    "錯誤發生在 opencode task tool 生成 child session 之後",
    "acpx 0.12.0 在 child session 生命週期管理上有 race condition"
  ],
  "handoff_note": "接手者：此錯誤與 G1 不同。G1 是 child session 未被註冊；這個新錯誤是 acpx transport 層誤判連線已斷。兩者根因不同。",
  "tags": ["acpx", "subagent", "deny-all", "bug"],
  "labels": ["dep:acpx", "topic:subagent"]
}
```
- `labels`（選填）：命名空間化標籤（`namespace:value`），與 `tags` 是兩個獨立概念——`tags` 自由格式、無格式要求；`labels` 是受控詞彙，格式規則與長度上限、以及供 `remagraph_search` 的 `cross_project_label` 精確比對用途，詳見下方「跨專案協作」章節

**Response（成功）：**
```json
{
  "status": "stored",
  "id": "mem-20260721-001",
  "superseded": [],
  "invalidated_count": 0
}
```
- `superseded`：若本次寫入為 `status_update`，列出被自動標記為 superseded 的既有 memory id；若非 `status_update` 則為空陣列
- `invalidated_count`：若 request 含 `invalidates` 參數，回傳實際被標記為 invalidated 的數量

**Response（被仲裁拒絕）：**
```json
{ "status": "rejected", "reason": "summary_too_short", "detail": "需 ≥ 30 字，目前 12 字" }
```

**Response（labels 格式不符）：**
```json
{ "status": "rejected", "reason": "invalid_label", "detail": "label 'Dep:acpx' 不符合命名空間格式 ..." }
```

**Response（唯讀降級拒絕）：**
```json
{ "status": "rejected", "reason": "read_only_mode", "detail": "此連線目前為唯讀模式（資料庫 schema 已升級到超出本程式碼的寫入相容版本），已拒絕本次寫入。請升級 remagraph 套件後再重試。" }
```
- `read_only_mode`：連線因下方「版本相容性」章節所述的三層判斷被標記為唯讀時觸發，且此檢查發生在五條仲裁規則、model2vec 去重之前——完全不會進入 transaction

#### `remagraph_search`

agent 查詢記憶。FTS5 BM25 全文檢索 + tag/kind 過濾 + 時間排序。

**Request：**
```json
{
  "query": "subagent deny-all 連線錯誤",
  "top_k": 20,
  "kind": "task_handoff",
  "status": "active",
  "project_id": "myproject"
}
```
- `project_id`（選填）：限定查詢單一專案；未提供且 `all_projects=true` 時移除此過濾（見下方）
- `all_projects`（`bool`，選填，預設 `false`）：`true` 時移除「目前這一個資料庫檔案內」的 `project_id` 過濾——但每個 project 本來就是各自獨立的 SQLite 檔案，此旗標從不開啟其他檔案
- `cross_project_label`（選填）：提供時完全改走跨專案標籤搜尋路徑，`query`/`kind`/`tags` 等全文檢索/過濾參數不適用，只依 label 精確比對；與 `all_projects` 是互不相干的兩個維度，詳見下方「跨專案協作」章節

**Response：**
```json
{
  "results": [
    {
      "id": "mem-20260721-001",
      "project_id": "myproject",
      "summary": "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤",
      "agent_id": "oc-dspro",
      "kind": "task_handoff",
      "task_id": "task-2026-07-21-003",
      "timestamp": "2026-07-21T14:30:00Z",
      "score": 0.87,
      "learnings": ["acpx 0.12.0 在 child session 生命週期管理上有 race condition"],
      "handoff_note": "接手者：此錯誤與 G1 不同……",
      "tags": ["acpx", "subagent", "deny-all", "bug"],
      "status": "active",
      "created_at": "2026-07-21T14:30:00.123Z",
      "updated_at": "2026-07-21T14:30:00.123Z"
    }
  ],
  "has_more": false,
  "cross_project_fanout_capped": false
}
```
- `top_k` 預設 **20**，最大 **100**
- `has_more`：`true` 表示還有更多結果（`LIMIT top_k + 1` 取 k+1 筆），agent 可縮小查詢範圍再查；v1 不提供精確 `total_matches`
- `query=""`（空字串）：回傳空 `results` + `has_more=false`，不拋錯，記錄 warning log
- FTS5 query 輸入前需在 server 端 sanitize（移除/跳脫特殊字元如 `*`、`"`、`AND`、`OR`、`NOT`），防止非預期語法錯誤
- 每筆 `results` 項目涵蓋 memories 表完整欄位集合（`embedding` 除外）——`learnings`/`handoff_note`/`tags`/`status`/`created_at`/`updated_at` 皆完整回傳（曾一度被 `_row_to_result()` 遺漏，已修復並補上回歸測試，見 CHANGELOG）
- `cross_project_fanout_capped`：只在使用 `cross_project_label` 時有意義，其餘查詢恆為 `false`；`true` 表示已知專案數超過 fan-out 上限，本次搜尋未涵蓋全部已知專案，詳見下方「跨專案協作」章節
- 使用 `cross_project_label` 時，每筆結果額外附加 `source_project_id` 欄位標示其來源專案

#### `remagraph_status`

查專案最新現況。回傳所有 active 的 `status_update` 型記憶，以 task_id 去重（只留每 task_id 最新一筆）。

**Request：**
```json
{ "limit": 20, "project_id": "myproject" }
```
- `project_id`（選填）：限定單一專案；`all_projects=true` 時移除此過濾（語意與 `remagraph_search` 的 `all_projects` 一致）

**Response：**
```json
{
  "latest": [
    {
      "task_id": "task-2026-07-21-003",
      "summary": "subagent 委派 bug 正在修，等待上游 PR 審查",
      "agent_id": "oc-dspro",
      "timestamp": "2026-07-21T14:30:00Z"
    }
  ],
  "server_code_version": 6,
  "db_schema_version": 6,
  "min_reader_version": 1,
  "min_writer_version": 6,
  "upgrade_hint": null,
  "read_only": false
}
```
- `limit` 預設 **20**，最大 **100**
- **版本相容性 handshake**（自本項起，`latest` 之外一律附加下列欄位，重用 `db.get_compat_status()`）：讓呼叫端能在真正嘗試寫入、撞牆失敗之前，就先透過 `remagraph_status` 得知自己的相容性等級，不必等 `remagraph_store` 失敗才第一次得知
  - `server_code_version`：目前執行中程式碼的 `SCHEMA_VERSION`
  - `db_schema_version`：資料庫 `_meta` 表實際存下的 `schema_version`（防禦性讀取）
  - `min_reader_version` / `min_writer_version`：資料庫存下的前向相容性欄位（見下方「版本相容性」章節）；若資料庫是該機制導入前建立、尚未跑過對應 migration，一律回傳 `null`，不拋例外
  - `upgrade_hint`：資料庫內建的升級指引文字，缺漏時為 `null`
  - `read_only`：目前連線是否處於唯讀降級模式（見下方「版本相容性」章節）
  - 這些欄位只在成功回應中出現；tier-3（連讀都不安全）情境下 `remagraph_status` 連線都開不起來，仍維持既有行為回傳乾淨的 `{"status": "error", "reason": ...}`，不會混入上述欄位

#### `remagraph_maintain`

執行 DB 自動維護（WAL checkpoint、清除 superseded/invalidated 記憶、FTS5 optimize、VACUUM、完整性檢查）。受 `maintenance.safety_validate_project()` 把關——與 CLI `maintain` 子指令使用同一個單一權威安全閥門——因此對一個 `state_dir` 解析不正確、或違反「特定保留前綴專案不得使用 default DB」這條命名慣例檢查的專案呼叫此 tool，會直接 fail closed，而不是悄悄維護錯的資料庫。

**Request：**
```json
{ "project_id": "myproject", "force": false }
```
- `project_id`（必填）：要執行維護的專案
- `force`（`bool`，選填，預設 `false`）：`true` 時下方每一項維護步驟都無條件執行，忽略各自的門檻檢查（`_should_checkpoint`/`_should_prune`/`_should_optimize_fts`/`_should_vacuum`）
- 與 CLI `maintain` 子指令不同，此 MCP tool 未提供 `dry_run`——每次呼叫都是真實維護執行

**Response（成功）：**
```json
{
  "status": "ok",
  "stats": {
    "project_id": "myproject",
    "started_at": "2026-07-21T14:30:00+00:00",
    "wal_checkpoint": "done",
    "pruned_count": 3,
    "fts_optimized": true,
    "vacuum": "done",
    "size_before_mb": 62.4,
    "integrity": "ok"
  }
}
```
`stats` 直接由 `maintenance.run_maintenance()` 產生；每個欄位只在對應步驟實際執行時才會出現：
- `wal_checkpoint`：執行了 `PRAGMA wal_checkpoint(TRUNCATE)` 時為 `"done"`（由 `force` 或超過 `policy.wal_checkpoint_interval_ops`，預設 1000 次操作，觸發）
- `pruned_count`：被刪除的 `status != 'active'` 記憶筆數，限定 `project_id`；只有早於 `policy.prune_superseded_age_days`（預設 90 天）的記錄才符合資格，且刪除動作以 `LIMIT` 依 `policy.prune_superseded_max_per_task`（預設 5）做每 task 節流上限
- `fts_optimized`：執行了 `INSERT INTO memories_fts(memories_fts) VALUES('optimize')` 時為 `true`
- `vacuum`/`size_before_mb`：DB 檔案大小（執行前量測）超過 `policy.vacuum_threshold_mb`（預設 50MB）時執行 `VACUUM`；`size_before_mb` 為 VACUUM 前的大小，VACUUM 後的大小會另外記錄作為下次 `_should_vacuum` 成長幅度判斷的基準
- `integrity`：`PRAGMA quick_check` 的結果（在 `policy.integrity_check_on_startup`，預設 `true`，或 `force` 時執行）；任何非 `"ok"` 的值都會拋出 `RuntimeError` 並透過 `_record_violation` 記一筆 `integrity_failed` 稽核違規

**Response（跳過——唯讀 schema tier）：**
```json
{
  "project_id": "myproject",
  "started_at": "2026-07-21T14:30:00+00:00",
  "skipped": true,
  "skip_reason": "read_only_schema_tier"
}
```
- 維護本質上是一整組寫入操作（WAL checkpoint、prune 的 `DELETE`、`VACUUM`、`ANALYZE`），對一個「目前執行中的程式碼尚未完全理解其 schema」的資料庫執行，沒有一項是安全的。此檢查無條件生效——刻意不受 `force` 影響，因為 `force` 表達的是呼叫端意願，而這裡防範的是 schema 相容性風險，兩者是正交的。見下方「版本相容性」了解連線如何一開始就被標記唯讀。

**Response（錯誤）：**
```json
{ "status": "error", "reason": "..." }
```
- 例如 `safety_validate_project()` 拒絕該專案時觸發（`state_dir` 不符、某個保留前綴專案指向 default DB、`project.json` metadata 不符等）

#### `remagraph_migrate_project`

把記憶從來源 project 一次性遷移到目標 project 的獨立 DB（例如 `default` → `otherproject`），並在來源標記 `invalidated`。此 tool 與 CLI 的 `migrate-project` 子指令（`cli.cmd_migrate_project`）共用同一個核心實作——`store.migrate_project_memories(from_project, to_project, dry_run=...)`——因此兩個入口對同一組輸入必然產生一致的最終結果，彼此都沒有各自獨立的一份遷移邏輯。

**Request：**
```json
{ "from_project": "default", "to_project": "otherproject", "dry_run": false }
```

**Response：**
```json
{
  "status": "ok",
  "from": "default",
  "to": "otherproject",
  "dry_run": false,
  "migrated_count": 3,
  "skipped_ids": []
}
```

**實際運作方式：**
1. 透過 `safety_validate_project(to_project, require_env_match=False)` 驗證目標專案——與其他地方使用的是同一個安全閥門（保留前綴規則、`project.json` metadata 一致性等）。
2. 透過共用的 project registry——`db.get_registered_state_dir(from_project)`——解析**來源**專案的 `state_dir`，而不是寫死的路徑。若 `from_project` 從未被登記過（沒有任何一次 `remagraph` 呼叫曾對它解析出 state_dir），會拋出 `store.ProjectNotRegisteredError`，而不是靜默當作 0 筆可遷移記錄——這正是修復了「隱含假設 `from_project` 永遠是 `"default"`」這個歷史 bug。`from_project == "default"` 是唯一刻意保留的例外：改用 `db.get_state_dir()`（尊重目前環境的 `REMAGRAPH_STATE_DIR`/`REMAGRAPH_HOME`）解析，因為 `"default"` 在正常使用情境下本來就刻意不會被登記進 registry（見 `cli._project_id_for_conn`）。
3. 依 `task_id`/`tags`/`agent_id`/`summary` 是否包含目標專案名稱做啟發式比對，取出 `from_project` 的記錄，以 `INSERT OR IGNORE` 強制 `project_id` 為 `to_project` 寫入目標專案自己的 DB，並在來源標記 `invalidated`（在 `learnings` 附加一筆 `migrated-to:<to_project>` 軌跡）。兩邊連線都透過 `db.connect_at_state_dir()` 開啟，完全繞過 `REMAGRAPH_STATE_DIR` 環境變數解析，直接對已經解析好的明確路徑操作——這是必要的，因為長駐的 MCP server 行程會綁定自己專案的 `state_dir`（見 `server._bind_project`），若這裡沿用一般、走環境變數的 `connect()` 路徑，會悄悄打開「server 自己的專案資料庫」，而不是真正的遷移來源/目標。
4. `dry_run=True` 會執行與真正遷移完全相同的比對查詢，把結果筆數放進 `migrated_count`、不寫入任何資料——因此在資料未變動的前提下，dry run 與之後真的執行時的筆數必然一致。
5. 若來源或目標資料庫目前處於唯讀降級的 schema 相容性分級（見下方「版本相容性」——其 schema 版本比目前程式碼的可寫相容版本新），會拋出 `store.MigrationReadOnlyError`，乾淨地拒絕遷移，而不是搬到一半失敗或悄悄什麼都沒做。
6. 若 `from_project` 與 `to_project`剛好解析到**同一個物理目錄**（例如呼叫端目前的 `REMAGRAPH_STATE_DIR` 剛好同時符合兩者的解析結果），會拋出 `ValueError`，而不是對同一份資料庫檔案開兩條互相衝突的寫入 transaction。

**Response（錯誤）：**
```json
{ "status": "error", "reason": "..." }
```
- `safety_validate_project()` 拒絕目標專案、`from_project` 從未被登記過、來源資料庫檔案不存在，或任一方處於唯讀降級狀態時觸發

---

### 記憶 Schema

三種 `kind`，每條記錄包含：`id`、`task_id`、`agent_id`、`timestamp`、`kind`、`summary`、`learnings[]`、`handoff_note`、`tags[]`、`status`。

| kind | 用途 | 生命週期 | 範例 |
|------|------|----------|------|
| `task_handoff` | 做了什麼、學到什麼、交接筆記 | 永遠 active | 「修 bug #5 時發現 acpx 有 race condition」 |
| `status_update` | 專案現況（PR merged、bug 發現、等待決策） | 同 `task_id` 自動 supersede | 「PR #4 merged，subagent bug 正在修」 |
| `discovered_constraint` | 發現的限制或陷阱 | 永遠 active，agent 可顯式 `invalidates=[id]` | 「OPENCODE_CONFIG 不是設定合併鏈最終權威」 |

`status_update` 的 supersede 規則：寫入新 `status_update` 時，自動將**同 `task_id`** 的所有舊 `status_update` 標記為 `superseded`。`task_id` 是精確的結構化鍵，不做語意判斷。

---

### 輕量仲裁（寫入端，零 LLM、零人類介入）

每筆 `remagraph_store` 請求必須通過全部五條規則，任一失敗即拒絕並回傳原因：

| # | 規則 | 說明 |
|---|---|---|
| 1 | `summary` ≥ 30 字（`len(summary.strip())`） | 防止空洞（「修了一個 bug」） |
| 2 | `learnings` 至少一筆 | 沒學到東西不該寫記憶 |
| 3 | `handoff_note` ≥ 20 字 | 僅對 `kind=task_handoff` 強制；其他 kind 可空 |
| 4 | model2vec 去重 | `potion-multilingual-128M`（支援 101 語言含中文），cosine similarity ≥ 0.90 拒絕（待中文資料集校準），回傳最相似的既有記憶 ID。模型載入失敗 **fail-fast**，不靜默降級 |
| 5 | `agent_id` 格式 + Lazy Registration | 格式 `^[a-z0-9_-]+$`，長度 **3–64** 字元；首次寫入時自動註冊 |

#### 去重補充說明

- 去重僅比對同 `kind` 的 active 記憶
- 去重門檻 v1 統一 **0.90**（標記「待中文資料集校準」）
- 同 kind active ≤ 2,000 筆：全量 cosine 比對；超過時取最新 2,000 筆比對
- 可選按 kind 分設門檻（`task_handoff: 0.90`、`status_update: 0.88`、`discovered_constraint: 0.92`），僅為建議非強制
- `status_update` supersede **嚴格同 task_id**，v1 不跨 task
- `discovered_constraint` invalidate **不做雙向**追溯（不設 `invalidated_by` 回指欄位）

---

### 儲存層：SQLite + FTS5

單一檔案，stdlib 零依賴。目前 `SCHEMA_VERSION = 6`（migration chain v1→v6；v5→v6 新增下方 `memory_labels` 表，v4→v5 新增下方「版本相容性」小節所述的前向相容欄位）。

#### Schema（SQL）

```sql
-- 主表
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('task_handoff', 'status_update', 'discovered_constraint', 'fleet_member')),
    task_id    TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    timestamp  TEXT NOT NULL,                -- MCP 回傳用（精確到秒），與 created_at 語意不同
    summary    TEXT NOT NULL,
    learnings  TEXT NOT NULL DEFAULT '[]',   -- JSON array
    handoff_note TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]',   -- JSON array
    status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'invalidated')),
    embedding  BLOB,                         -- model2vec vector (np.float32 little-endian '<f4')，v1 只存不查
    created_at TEXT NOT NULL,                -- ISO 8601 UTC（內部審計用，精確到毫秒）
    updated_at TEXT NOT NULL
);

-- FTS5 虛擬表（BM25 全文檢索，trigram tokenizer 支援中文 CJK）
-- 若 runtime SQLite < 3.34 不支援 trigram，降級方案為手動 bigram 前處理
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    summary,
    learnings,
    handoff_note,
    tags,
    content='memories',
    content_rowid='rowid',
    tokenize='trigram'
);

-- INSERT 自動同步 FTS5
CREATE TRIGGER IF NOT EXISTS memories_ai
AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;

-- UPDATE 自動同步 FTS5（防止 UPDATE 後 index 失步）
CREATE TRIGGER IF NOT EXISTS memories_au
AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;

-- DELETE 自動同步 FTS5
CREATE TRIGGER IF NOT EXISTS memories_ad
AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
END;

-- 效能 indexes
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(task_id);
CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC);

-- 版本追蹤（自 v4→v5 起額外存放前向相容性欄位，見下方「版本相容性」小節）
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 每個記憶可掛上多個命名空間化標籤（schema v5→v6），供跨專案標籤搜尋使用
-- （見下方「跨專案協作」章節）。標籤格式（namespace:value）由應用層
-- （arbitration.validate_labels()）驗證，本表不對 label 內容加 CHECK 約束
-- （與 tags 欄位一致）。
CREATE TABLE IF NOT EXISTS memory_labels (
    memory_id TEXT NOT NULL REFERENCES memories(id),
    label     TEXT NOT NULL,
    PRIMARY KEY (memory_id, label)
);
CREATE INDEX IF NOT EXISTS idx_memory_labels_label ON memory_labels(label);
```

#### 版本相容性（`_meta` 前向相容欄位 + 三層判斷）

背景：獨立釘版的舊消費端一旦打開一個 `schema_version` 比自己程式碼還新的資料庫，過去只能整個拒絕開啟（`MigrationError`），且錯誤訊息寫死在舊版程式碼裡——之後即使改善訊息文字，舊消費端也永遠讀不到，因為它執行的是自己那份舊 source（已有 MegaNote、Meshtastic 兩個真實案例撞到「schema_version 比程式碼新，無法降級」而放棄寫入）。解法：把升級指引與相容性邊界存進資料庫本身的 `_meta` 表（消費端一定會開、一定會讀到），而不是只寫在程式碼字串常數裡。

**`_meta` 新增欄位**（schema v4→v5 起，`_migrate_v4_to_v5()` 種下；全新資料庫建立時同步寫入，不必等 migration chain 跑到）：

| 欄位 | 說明 |
|------|------|
| `min_reader_version` | 這個資料庫允許被「讀取」的最舊程式碼 `SCHEMA_VERSION`。目前預設 `"1"` |
| `min_writer_version` | 這個資料庫允許被「寫入」的最舊程式碼 `SCHEMA_VERSION`。每次涉及欄位/CHECK 變動的 migration 都會更新為當時的 `SCHEMA_VERSION`（例如 v4→v5 時寫入 `"5"`；v5→v6 純新增 `memory_labels` 表，不修改 `memories` 本身欄位/CHECK，刻意維持 `min_writer_version` 不變） |
| `upgrade_hint` | 自我完整、不依賴任何程式碼常數的英文升級指引文字，供拒絕/降級訊息附加顯示 |

讀取這三個欄位一律走防禦性讀取（`_read_meta_int_defensively()` / `_read_upgrade_hint_defensively()`）：表不存在、欄位缺漏、型別不符等任何失敗都回傳 `None`，絕不拋出例外中斷既有的拒絕/降級流程。

**`db.connect()` 的三層版本相容性判斷**（`_handle_newer_than_code_schema()`，僅在資料庫 `schema_version` 比程式碼的 `SCHEMA_VERSION` 還新時觸發）：

| 層級 | 條件 | 行為 |
|------|------|------|
| Tier 1：完全相容 | `SCHEMA_VERSION >= min_writer_version` | 正常讀寫，與過去 `schema_version <= SCHEMA_VERSION` 完全相同，不做任何事 |
| Tier 2：唯讀降級 | `min_reader_version <= SCHEMA_VERSION < min_writer_version` | `connect()` **不再拋出例外**，回傳可用連線，但在連線物件上標記唯讀（見下方「唯讀模式對呼叫端的意義」）|
| Tier 3：完全拒絕 | `SCHEMA_VERSION < min_reader_version` | 維持既有行為：`connect()` 拋出 `MigrationError`（三選項靜態訊息 + 防禦性讀取的 `upgrade_hint`）|

任一版本欄位讀取失敗或缺漏（例如資料庫是此機制導入前建立、尚未跑過 v4→v5 migration），`min_reader_version`/`min_writer_version` 一律視為等於資料庫的 `schema_version` 本身——退回機制導入前的嚴格全有全無行為，絕不套用寬鬆預設值。

**唯讀模式對呼叫端的意義：**

- 唯讀標記掛在連線物件上（`db.READ_ONLY_ATTR` / `db.READ_ONLY_DETAIL_ATTR`），因為原生 `sqlite3.Connection` 是純 C extension 型別、不支援任意屬性賦值；`connect()` 一律以 `_MarkedConnection`（`sqlite3.Connection` 的空子類別）作為 `factory=`，讓連線物件能安全掛標記，同時仍是完整的 `sqlite3.Connection` 實例（既有的 `isinstance` 檢查、型別標註皆不受影響）
- `remagraph_search` / `remagraph_status`（`search_memories()` / `get_status()`）完全不受影響，唯讀連線上的查詢一律正常執行
- `remagraph_store`（`process_store()`）在函式最前面（早於安全閥門、早於五條仲裁規則、早於 model2vec 去重）就檢查此標記；若唯讀，直接回傳 `status="rejected"` / `reason="read_only_mode"`，完全不進入 transaction
- 自動維護（`light_maintenance_on_connect()` → `run_maintenance()`，含 `remagraph_maintain` MCP tool）同樣一取得連線（不論是呼叫端傳入的、還是內部自行另開的連線）就檢查唯讀標記；若唯讀，跳過 WAL checkpoint／prune／FTS optimize／VACUUM／ANALYZE／完整性檢查等**所有**寫入操作，回傳 `stats={"skipped": true, "skip_reason": "read_only_schema_tier"}` 並記一筆 `maintenance_skipped_read_only` audit 事件；此保護對呼叫端要求的 `force=True` 依然生效——唯讀降級要防的是 schema 相容性風險，與呼叫端是否要求強制執行無關

#### 查詢範例

FTS5 query 輸入前需在 server 端 sanitize（移除/跳脫 FTS5 特殊字元如 `*`、`"`、`AND`、`OR`、`NOT`），防止非預期語法錯誤。

```sql
-- BM25 全文檢索 + kind 過濾 + tag 過濾 + 時間排序
SELECT m.*, fts.rank
FROM memories_fts fts
JOIN memories m ON fts.rowid = m.rowid
WHERE memories_fts MATCH 'subagent deny-all error'
  AND m.kind = 'task_handoff'
  AND m.status = 'active'
ORDER BY fts.rank, m.created_at DESC
LIMIT 20;
```

#### embedding 欄位策略

- v1 只將 model2vec embedding 存為 BLOB，不做向量查詢
- 格式：`np.float32` little-endian（寫入：`.astype('<f4').tobytes()`，讀回：`np.frombuffer(b, dtype='<f4')`）
- stdio 模式：**lazy load** 模型（process 生命週期短，避免冷啟動延遲）
- 模型載入失敗：**fail-fast**（啟動失敗或第一次呼叫時回傳明確錯誤），不靜默降級
- sqlite-vec 不加
- 未來 v2 若要語意搜尋，`pip install remagraph[vector]` → 對既有 BLOB 建 sqlite-vec index，不用重算全量 embedding

#### pyproject.toml（零依賴）

```toml
[project]
name = "remagraph"
requires-python = ">=3.11"
dependencies = [
    "model2vec>=0.1.0",   # potion-multilingual-128M（支援中文 CJK）
    "mcp>=1.0",           # MCP Python SDK（stdio transport）
]
# sqlite3 是 stdlib，不列

[project.optional-dependencies]
vector = ["sqlite-vec>=0.1.0"]
```

---

### 跨專案協作（Cross-Project Collaboration）

RemaGraph 每個 `project_id` 對應完全獨立的 state_dir / SQLite 檔案（見「部署形態」），彼此原本互不知道對方存在，各自是一座孤島。本節描述讓 agent 能在需要時「看見」其他專案存在、並精確查詢其標籤的兩層機制——這是後續 `recall_related` 等跨專案查詢能力的地基，本身不含全文檢索或關聯圖功能。

#### 跨專案登記表（`project_registry`）

一個輕量、共用的「登記簿」，記錄哪些 `project_id` 存在、各自的 state_dir 在哪裡：

| 欄位 | 說明 |
|------|------|
| `project_id` | 主鍵 |
| `state_dir` | 該 project 目前解析出的絕對路徑 |
| `first_seen` / `last_seen` | 首次 / 最近一次被登記的 UTC 時間（ISO 8601，秒精度） |

- 落在 `DEFAULT_STATE_DIR`（`~/.local/state/remagraph/`）的 `remagraph.db`，與 `"default"` 專案自己的 memories 共用同一份檔案——因為這是唯一一個「任何專案、任何時候都不需要額外設定就能解析出來」的位置
- `CREATE TABLE IF NOT EXISTS`，冪等，刻意獨立於既有的 per-project migration chain（不隨 `SCHEMA_VERSION` 升版走）：該 chain 對**每一個**專案自己的資料庫執行一次，若把 registry 併入其中，會讓每個專案的私有 DB 都多出一張與己無關的表，弄髒既有的「孤島互不相干」設計
- **自動登記，無需顯式呼叫**：`maintenance.resolve_project_state_dir()`（任何帶 `project_id` 的操作都會呼叫到，含安全閥門 `safety_validate_project()`）每次解析出 state_dir 後，都會呼叫 `db.register_known_project()` 做 best-effort upsert——正常使用就會自動被記錄；任何失敗（目錄無法建立、DB 鎖定、權限不足……）一律吞下，絕不影響呼叫端主流程。`first_seen` 只在該 project 第一次出現時寫入，已存在的列只更新 `state_dir`（若已改變）與 `last_seen`
- `db.list_known_projects()`：讀出登記表所有列，永遠指向真正的 `DEFAULT_STATE_DIR`，不受呼叫端當下的 `REMAGRAPH_STATE_DIR` / `REMAGRAPH_PROJECT` 環境變數影響；任何讀取失敗一律回傳空清單，不拋例外
- `db.connect_foreign_project_readonly(project_id)`：對已登記的另一個 project 開一條**真正唯讀**的連線（SQLite URI `file:<path>?mode=ro` + `PRAGMA query_only=1`），完全繞過 `db.connect()` / `get_state_dir()` / `safety_validate_project()` / `light_maintenance_on_connect()`（架構上就不會經過這些路徑，不是靠旗標略過）；未登記的 project、或其 state_dir/db 檔案已不存在（例如已被刪除），一律回傳 `None`，絕不會意外生出一個空白新資料庫——`mode=ro` 讓 SQLite 在檔案不存在時於 `connect()` 呼叫當下就直接拋出 `OperationalError`，取代了「先 `exists()` 預檢查、再一般模式 `connect()`」會留下的 TOCTOU 競態窗口（檔案在檢查之後、連線之前才被刪除，一般模式會悄悄建立一個看似正常、實則空白的新資料庫）

#### 標籤（`memory_labels`）與跨專案標籤搜尋

每筆記憶可另外掛上多個「命名空間化」標籤（schema v5→v6 的 `memory_labels` 表，DDL 見上方「儲存層」章節）。這與既有的 `tags` 欄位是兩個獨立概念，刻意不合併：`tags` 是自由格式、無格式要求的既有欄位，供既有的 tag 過濾搜尋使用；`labels` 是新增的受控詞彙，有明確格式要求，專供本節的跨專案精確比對使用。

**標籤格式**：`namespace:value`，例如 `dep:opencode`、`topic:auth`、`kind:bug`。

- 完整規則：`^[a-z]+:[a-zA-Z0-9_-]+$`（見 `arbitration.LABEL_REGEX`；實作上錨點用 `\Z` 而非 `$`，避免 Python regex 的 `$` 對「結尾前恰有一個換行字元」的例外放行，讓帶結尾換行的字串誤判為合法）
- `namespace` 一律小寫字母；規則本身不限制具體是哪些字首，但慣例上建議使用一組小、受控的字首，例如 `dep:`（依賴）、`topic:`（主題）、`kind:`（分類），目的是避免標籤長期演變成破碎、不一致的自由格式字串
- `value` 允許大小寫英數字、底線、連字號，與既有 `project_id` / `task_id` / `agent_id` 的字元集慣例一致
- 長度上限 **64 字元**（整個 `namespace:value` 字串），與既有 `project_id` / `task_id` / `agent_id` 的 64 字元上限慣例一致
- `remagraph_store` 的 `labels` 參數：任一標籤格式不符（含超長），**整批拒絕**（`StoreResponse(status="rejected", reason="invalid_label")`），不靜默跳過壞的、只留合法的——標籤存在的價值就是「受控詞彙」，靜默跳過只會讓呼叫端永遠不知道自己格式錯了，久了反而助長標籤破碎化
- labels 與該筆 memory 的 INSERT 在同一個 transaction 內一起寫入，要嘛一起 commit、要嘛一起 rollback；重複標籤自動去重（不會因 `(memory_id, label)` 複合主鍵衝突而報錯）

**`remagraph_search` 的 `cross_project_label` 參數：**

- 提供此參數時，走完全獨立於全文檢索的查詢路徑——只依 label 精確比對，`query` / `kind` / `tags` 等其餘全文檢索/過濾參數不適用；`status` 過濾預設 `active`，可由呼叫端覆蓋
- 查詢範圍：(a) 目前這個連線自己專案的 `memory_labels`，加上 (b) 透過登記表逐一開啟「其他」已知專案的唯讀連線查詢，合併結果並在每筆結果標註 `source_project_id` 表示其來源專案
- 與既有的 `all_projects` 旗標是完全獨立的兩個維度，互不取代：`all_projects` 只移除「目前這一個資料庫檔案內」的 `project_id` 過濾（每個 project 各自是獨立檔案，此旗標從不開啟其他檔案）；`cross_project_label` 才會透過登記表真正開啟其他 project 各自獨立的資料庫檔案
- **Fan-out 上限預設 50、可設定、硬上限 200**（`search._CROSS_PROJECT_FANOUT_CAP`，原為寫死的 20；PPLX 架構審查共識調整）：單次搜尋最多開啟這麼多個「其他」已知專案的資料庫（不含目前連線自己所屬的專案，那一個是直接查詢、不計入上限）。可透過 CLI `--fanout-cap` 或 `REMAGRAPH_FANOUT_CAP` 環境變數覆寫，兩者皆會被夾在硬上限 200（`REMAGRAPH_FANOUT_HARD_CAP` 才可再提高）之內，刻意不提供「無上限」逃生口——已知專案數會隨時間單調增加（目前沒有自動清除機制），若無上限，一次 fan-out 可能觸發過多並行 SQLite 連線，在 CI/容器等資源受限環境有 OOM 風險。超過上限時**不會**悄悄截斷佯裝已涵蓋全部：`SearchResponse.cross_project_fanout_capped` 標記為 `true`，並附上 `candidates_total`/`candidates_searched`/`candidates_skipped` 三個計數（`total == searched + skipped` 恆成立，皆已排除呼叫端自己所屬的專案，避免計入 off-by-one），CLI 於截斷時 exit code 改為 `2`（有別於 `0`=完整、`1`=真正錯誤），讓呼叫端能明確分辨「完整結果」「結果不完整」「工具本身出錯」三種情況，而不是把截斷誤讀成空結果。
- 已登記但目前不可達的專案（例如目錄已被刪除、或該專案的資料庫尚未升級到含 `memory_labels` 表的 schema 版本）會被優雅跳過，不讓整個搜尋因單一專案失敗
- 結果依 `(source_project_id, id)` 去重：即使呼叫端未提供 `project_id`（因而無法在 fan-out 迴圈中提前判斷、跳過自己所屬的專案），也保證同一筆記憶不會被回傳兩次。此去重鍵有一個已修復的邊界情況：若呼叫端自己的連線與某個已註冊的候選專案**物理上是同一個 SQLite 檔案**（例如本機的 `default` state dir 恰好與某個已註冊專案指向同一路徑），兩次出現會帶著不同的 `source_project_id` 字串，光靠這個鍵攔不住重複——因此 fan-out 迴圈額外用 `PRAGMA database_list` 取得雙方實際連到的實體檔案絕對路徑比對，物理上相同就跳過，不只依賴 `project_id` 字串比對

#### 專案隔離安全閥（`safety_validate_project`）與 `remagraph serve` 的單專案綁定

`project_id` 本身只是資料列上的標籤欄位，**真正決定連到哪個實體 SQLite 檔案的是 `REMAGRAPH_STATE_DIR`/`REMAGRAPH_PROJECT` 環境變數**（或明確傳入 `connect()` 的 `state_dir`）。`db.connect(project_id=...)` 內建 `maintenance.safety_validate_project(project_id)` 這道安全閥：透過 `resolve_project_state_dir(project_id)` 算出這個 `project_id` 應該對應的權威 state_dir，並讀取該目錄下的 `project.json`（`db.validate_project_metadata()`）確認其記錄的 `project_id` 與目前要求的一致——不一致（該目錄先前已合法用於另一個 project）一律 `SafetyValveError`，記一筆 `project_metadata_mismatch` 違規稽核，在任何寫入發生之前就擋下。

這道安全閥門只有在呼叫端把 `project_id` 明確傳進 `connect()` 時才會生效；CLI 各子命令與 `remagraph serve` 現在都會這麼做（2026-07-25 修復前，兩者皆以零參數呼叫 `_db.connect()`，安全閥完全不會被觸發，實際連到哪個檔案純看 process 環境當下剛好是什麼——這正是一次真實生產事故的根因：一個專案的 `serve` process 繼承了另一個專案的環境變數，卻悄悄把資料寫進了後者的真實資料庫）。

`remagraph serve` 的專案綁定模型（PPLX 架構審查共識，見下方待決策記錄）：**單一 serve process 嚴格綁定單一 project，且在啟動時就 fail-fast**，不是「第一次呼叫決定綁定」：
- 啟動時必須提供 `--project <id>` 或 `REMAGRAPH_PROJECT` 環境變數其中之一，兩者皆缺席直接非零 exit，不進入 MCP stdio 迴圈
- 綁定成功後印出診斷訊息（實際綁定的 `project_id` 與解析出的 state_dir），若偵測到連線是唯讀降級模式也會提前警告
- 之後任何 tool call（`remagraph_store`/`search`/`status`）帶入與綁定不同、非 `None` 的 `project_id`，一律回傳結構化錯誤，不悄悄沿用/切換連線
- **刻意不支援單一 process 動態路由多個 project**（PPLX 明確否決此設計方向）：SQLite WAL 模式下多條長駐連線的 checkpoint 時機會互相干擾；連線 cache 的 eviction/關閉時機管理複雜；且安全閥本身假設「目前 process 環境只對應一個 project_id」，動態路由會讓這個假設不成立，等於要連帶重新設計安全閥語意。需要同時服務多個專案時，應在 MCP host 層為每個專案各自啟動一個 `remagraph serve --project <id>` process，而非讓單一 server 跨專案路由——這也是 MCP 規格本身建議的分工方式

---

### 審計（Audit）

#### 設計原則

RemaGraph 自管 audit，不依賴任何外部系統。若存在外部排程系統，會透過讀取此檔案驗證 agent 是否完成記憶寫入。

#### 路徑

`~/.local/state/remagraph/audit.jsonl`（0600，目錄 0700）

#### Schema

```jsonl
{"ts":"2026-07-21T14:23:01.234Z","actor_id":"agent_id/task_id","action":"remagraph_store","mem_id":"mem-20260721-001","task_id":"task-2026-07-21-003","status":"stored","error":null}
```

| 欄位 | 說明 |
|------|------|
| `ts` | ISO 8601 UTC（`Z` 後綴，不支援 local time），與常見外部 audit log 的時間戳慣例一致 |
| `actor_id` | `{agent_id}/{task_id}` 複合形式 |
| `action` | 對 `remagraph_store` 交易固定為 `remagraph_store`（見下方對外公告的 Audit Contract，此值不變）；同一份 audit-YYYYMM.jsonl 另外也由 `append_event` 寫入維護／生命週期事件的 action 值（例如 `safety_violation`、`maintenance_completed`、`maintenance_light_failed`），這些記錄是不同、更簡單的結構（不含 `task_id`、`agent_id`、`kind`、`status`、`mem_id` 等欄位） |
| `mem_id` | 寫入成功後的 memory id，外部系統比對用 |
| `task_id` | 明確 index key，外部系統可直接 grep |
| `status` | `"stored"` 或 `"error"` |
| `error` | 失敗時填 exception class name（不存 traceback 或 message，最小洩漏原則） |

- v1 不做 audit.jsonl rotation（單一 append-only 檔案，DEFER to v2）

#### Audit Contract（給外部排程系統）

RemaGraph 對外公告的合約（本節可獨立引用）：

- **路徑**：`~/.local/state/remagraph/audit.jsonl`
- **驗證方式**：以 `task_id` 為 key 查 audit，找 `action="remagraph_store"` 且 `status="stored"` 的記錄
- **未寫入的行為**：未找到記錄時，排程系統應自行決定處理策略（例如發 follow-up prompt 提醒 agent、記錄 `memory_write_failed`）
- **schema 變更**：RemaGraph 若修改 audit schema，會在 release note 中公告

---

### CI/CD 品質門檻

沿用一套標準、廣泛採用的開源 CI/CD 門檻組合：

| 門檻 | 設定 |
|------|------|
| **測試** | pytest（單元測試 + MCP 整合測試） |
| **覆蓋率** | `pytest --cov=src/remagraph --cov-fail-under=80` |
| **突變測試** | mutmut（限縮 `arbitration.py` + `dedup.py`，`runner = "pytest"`，非阻塞但持續追蹤） |
| **機密掃描** | gitleaks（每 push / PR，全 Git 歷史） |
| **簽章** | DCO（`git commit -s`） |
| **CI** | GitHub Actions：ubuntu × macos × Python 3.11–3.13 |

---

### 專案結構

```
remagraph/
├── pyproject.toml
├── README.md
├── DESIGN.md                       # 本文件
├── LICENSE                          # Apache-2.0
├── .github/
│   └── workflows/
│       ├── test.yml
│       ├── gitleaks.yml
│       ├── pip-audit.yml
│       ├── mutmut.yml
│       └── publish.yml
├── src/
│   └── remagraph/
│       ├── __init__.py
│       ├── server.py               # MCP server entrypoint（stdio transport）
│       ├── store.py                # SQLite + FTS5 讀寫
│       ├── search.py               # BM25 查詢邏輯
│       ├── dedup.py                # model2vec 去重
│       ├── arbitration.py          # 五條仲裁規則
│       ├── audit.py                # 自管 audit writer
│       ├── models.py               # Pydantic schema
│       └── db.py                   # SQLite 連線管理與 migration
├── tests/
│   ├── test_store.py
│   ├── test_search.py
│   ├── test_dedup.py
│   ├── test_arbitration.py
│   └── test_audit.py
└── docs/
    └── audit.md                    # Audit Contract（外部系統引用本節即可）
```

---

### 設計決策歷程

完整規劃討論記錄保存在專案內部的設計審查存檔中（不屬於本份公開規格書），包含：

1. 需求釐清：多 agent 共享記憶、agent 自寫自查、不受任何中央協調者把關個別寫入
2. 技術選型：PPLX 對抗式審查四輪（去重方案、生命週期管理、行為引導、audit 架構、儲存層評估）
3. 命名迭代：五輪 PPLX 討論，最終選定 RemaGraph（Remanent，殘磁）
4. 架構定位：從「構思於某個較大、封閉的多專案生態系底下的子工具」獨立為任何 AI coding agent 都能使用的通用 MCP server

---

### 未來升級路線（非 v1 範圍）

```
v1: SQLite + FTS5 + trigram tokenizer（零依賴，BM25 全文檢索支援中文，stdio transport）
  ↓
v2: SQLite + FTS5 + sqlite-vec（語意搜尋，pip install remagraph[vector]）
  ↓
vN: Unix socket daemon（長駐 process，減少冷啟動延遲）│ DuckDB（百萬級資料，複雜分析查詢）
  ↓
vN+1: PostgreSQL + pgvector（多人協作，雲端服務）
```

每個階段的觸發條件是實際使用量與使用者回饋，而非預先規劃。

---

### PPLX-CONSENSUS-APPLIED

> 2026-07-21 PPLX 對抗式審查（`docs/design/reviews/pplx-design-review-2026-07-21.md`）
> 共識行動清單（`docs/design/reviews/pplx-consensus-actions-2026-07-21.md`）

- [x] B1：去重模型 `potion-base-8M` → `potion-multilingual-128M`，宣告 v1 支援中文，fail-fast
- [x] B2：FTS5 DDL 改用 `tokenize='trigram'`，修正 CJK tokenizer 描述，補降級方案說明
- [x] B3：部署形態改為 v1 主要 stdio，Unix socket daemon 移至 vN 路線圖
- [x] C1：`handoff_note` 規則 #3 限定僅 `task_handoff` 強制
- [x] C2：FTS5 CJK 分詞描述已隨 B2 修正
- [x] C3：`remagraph_status` limit 預設 20、最大 100
- [x] C4：`remagraph_search` top_k 預設 20、最大 100
- [x] C5：補 `memories_au` AFTER UPDATE trigger
- [x] C6：DDL 補 `timestamp` 欄位（與 `created_at` 語意區分）
- [x] C7：同 B3
- [x] C8：`StoreResponse` 擴充 `superseded` / `invalidated_count` 欄位
- [x] R1–R9：所有設計回寫項（模型名、中文支援、trigram、agent_id 長度、has_more、sanitize、mcp 依賴等）
- [x] Q1–Q8 裁決：去重門檻 0.90、同 task_id supersede、無雙向 invalidate、2,000 筆上限、float32 LE、UTC Z、error class name only、PID 鎖、空 query 不拋錯、len(strip())、stdio lazy load
- [x] N4：記憶 timestamp（秒）vs audit ts（毫秒）精度差異已於 DDL 註解標注
- [x] N9：FTS5 sanitize 已寫入查詢範例與搜尋說明
- [x] N10：`mcp>=1.0` 依賴已寫入 pyproject.toml 片段
- [x] 全文無 `potion-base-8M`、無「v1 以 Unix socket 為主」舊敘述
