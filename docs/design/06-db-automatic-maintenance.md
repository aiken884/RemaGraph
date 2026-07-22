# T-RG-D06：資料庫自動維護機制設計

> **艦隊任務 ID**：`T-RG-D06`
> **狀態**：設計階段，待 PPLX 審查
> **約束**：嚴格遵守 per-project state_dir 隔離（REMAGRAPH_STATE_DIR 必須對應 project）、project_id 強制欄位、所有 store/search 必須經過安全閥門。不得依賴 default DB 作為生產使用。與 D02、D01、herdr-bridge 治理層對齊。

## 1. 背景與問題

目前 RemaGraph DB 維護為手動或啟動時輕量：
- WAL 模式但無自動 checkpoint（導致 WAL 膨脹，如 2.3MB vs 336KB）
- Schema migration 手動觸發（v2 → v4 需 ALTER + rebuild）
- 清理 superseded/invalidated 僅在 arbitration 有 cleanup_superseded，但未自動化
- FTS5 無定期 optimize
- 無 integrity check、vacuum、size 監控
- herdr-bridge 等專案記憶混在 default DB，違反隔離設計

目標：自動化、安全、符合設計（project isolation + safety valve）

## 2. 設計原則（來自先前共識與 DESIGN.md）

- **安全閥門（Safety Valve）**：任何不合規輸入（未設 REMAGRAPH_STATE_DIR、project_id=default 但屬 herdr- 專案、無效 state_dir）立即 reject + audit。
- **Per-project 隔離**：herdr-bridge 必須用 `~/.local/state/remagraph-herdr-bridge/`，RemaGraph 核心用 default 或對應。
- **project_id 強制**：所有 memories 必須有 project_id，query 必須過濾。
- **最小干擾**：維護在 background / on-demand / startup 輕量執行。
- **可審計**：所有維護動作寫入 audit.jsonl + memory (kind=discovered_constraint 或 status_update)。
- **漸進式**：舊 default DB 資料逐步 migrate 到專用 DB 並 invalidated。
- **PPLX 共識**：本設計需經 PPLX 審查後實作。

## 3. 自動維護機制架構

新增模組：`src/remagraph/maintenance.py`

### 3.1 MaintenancePolicy（可配置）
- `wal_checkpoint_interval_ops`: 1000（或時間）
- `prune_superseded_age_days`: 90
- `prune_superseded_max_per_task`: 5
- `fts_optimize_interval`: 每 10000 寫入
- `vacuum_threshold_mb`: 50
- `integrity_check_on_startup`: true
- `max_db_size_mb`: 100（已用 max_page_count 防護）

由 project.json 或 env `REMAGRAPH_MAINTENANCE_*` 載入，預設保守值。

### 3.2 觸發點（自動化）
1. **Startup / connect()**（db.py 內呼叫）：
   - 輕量：WAL checkpoint (PASSIVE)
   - Integrity check (quick)
   - Migration 自動跑（_run_migrations 強化）
   - 若 state_dir / project 不符 → raise SafetyValveError

2. **On store**（store.py process_store 後）：
   - 計數器 + 條件觸發 checkpoint / FTS optimize / prune

3. **Background**（若在 MCP server 模式）：
   - 使用 asyncio 或 threading.Timer 定期（每 5min 或依 policy）
   - 或 MCP tool `remagraph.maintain` 手動觸發

4. **On-demand**：
   - 新 MCP tool: remagraph_maintain (force=True 可 vacuum)

### 3.3 維護操作（依序、安全）
```python
def run_maintenance(conn, policy, project_id: str, force: bool = False):
    stats = {}
    # 1. Safety valve
    validate_isolation(project_id)  # 檢查 env + meta + project_id

    # 2. WAL
    if force or should_checkpoint():
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        stats["wal_checkpoint"] = "done"

    # 3. Prune
    deleted = cleanup_superseded(conn, policy.prune_age_days, project_id)
    stats["pruned"] = deleted

    # 4. FTS
    if force or should_optimize():
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
        stats["fts_optimized"] = True

    # 5. Vacuum if needed
    if get_db_size_mb() > policy.vacuum_threshold:
        conn.execute("VACUUM")
        stats["vacuum"] = "done"

    # 6. Analyze
    conn.execute("ANALYZE")

    # 7. Integrity (quick)
    if policy.integrity_on_maintain:
        res = conn.execute("PRAGMA quick_check").fetchone()
        if res[0] != "ok":
            raise IntegrityError(res)

    append_audit("maintenance", stats, project_id)
    # 寫入 memory 作為 status_update
    return stats
```

### 3.4 安全閥門實作（阻擋不合規）—— PPLX 共識修正版

**權威解析函式**（單一真相來源）：
```python
def resolve_project_state_dir(project_id: str) -> pathlib.Path:
    # 從 project.json / governance registry / _ensure_remagraph_project 讀取
    # 必須 return 絕對路徑（realpath 解析 symlink）
    ...
```

**統一安全閥門**（不可 bypass）：
```python
def safety_validate_project(project_id: str, *, require_env_match: bool = True):
    configured = resolve_project_state_dir(project_id)
    env_dir = os.environ.get("REMAGRAPH_STATE_DIR")
    if not env_dir:
        raise SafetyValveError("REMAGRAPH_STATE_DIR 未設定")
    if require_env_match and pathlib.Path(env_dir).resolve() != configured.resolve():
        raise SafetyValveError(f"REMAGRAPH_STATE_DIR 與 project 配置不符: {env_dir} != {configured}")
    if project_id.startswith("herdr-") and configured == resolve_project_state_dir("default"):
        raise SafetyValveError("herdr-* project 不得使用 default DB")
    # 寫 audit + discovered_constraint（若違規）
```

- **單一對外 API 層**（store/search/maintain 必須走此）：
  - `remagraph/api.py` 暴露 `store_memories(..., project_id)`、`search_memories(..., project_id)`
  - 內部第一步永遠呼叫 `safety_validate_project(project_id)`
  - 禁止直接暴露 db cursor 給外部（包含 maintenance、arbitration 內部模組必須經由此或顯式傳 project_id + 驗證）。
- search 強制：SQL 必須有 `WHERE project_id = ?`，不允許省略 project_id 的全庫查詢。
- 所有 herdr-bridge 啟動點（router __init__、commander 啟動、測試 fixture）**強制**：
  1. 呼叫 `_ensure_remagraph_project("herdr-bridge")`
  2. 設定 `REMAGRAPH_STATE_DIR`
  3. 立即呼叫 `safety_validate_project("herdr-bridge")`
  4. 若失敗 → 直接中止啟動 + audit
- Query/store 時 project_id 為**強制參數**，無預設值（除非明確為 core "default" 專案）。

**run_maintenance 修正**：
```python
def run_maintenance(policy, project_id: str, force: bool = False):
    safety_validate_project(project_id)
    state_dir = resolve_project_state_dir(project_id)
    conn = sqlite3.connect(state_dir / "remagraph.db")  # 內部自己建立，不接受外部 conn
    ...
    # 所有 SQL 都帶 project_id filter
```

### 3.5 遷移策略（default → 專用 DB）—— PPLX 共識修正版
- **不得將 default DB 視為 herdr-bridge 的 authoritative source**。
- 遷移設計為**一次性、顯式操作**：
  - 只允許透過明確工具觸發：
    - MCP: `remagraph_migrate_project --from default --to herdr-bridge`
    - CLI: `remagraph migrate-project --from default --to herdr-bridge`
  - 內部流程：
    1. safety_validate_project("herdr-bridge")
    2. 掃描 default DB 中屬於 herdr-bridge 的記錄（依 tags / task_id / agent_id 模式，或 governance 標記）。
    3. INSERT 到 herdr-bridge 專用 DB（帶正確 project_id="herdr-bridge"）。
    4. 在 default DB 標記 `status="invalidated"`，learnings 附加 `"migrated_to: remagraph-herdr-bridge at <ts>"`。
    5. 在 default DB 的 metadata / _meta 寫入 `herdr_bridge_migrated_at` flag。
- 之後任何啟動/維護：
  - 若偵測到 flag，**不再自動掃描 default DB**。
  - 安全閥門永久阻擋 herdr-bridge 對 default DB 的 store/search/maintain。
- 舊 default DB 僅作為 archive，永不作為 herdr-bridge 生產來源。

### 3.6 監控與回報
- 維護後回傳 stats（pruned_count, wal_size_before/after, etc.）
- Audit 記錄所有動作。
- 若維護失敗，寫 discovered_constraint 並阻擋後續 write（安全閥）。

## 4. 介面草圖

新增：
- `remagraph/maintenance.py: run_maintenance(conn, policy, project_id, force=False) -> dict`
- `remagraph/db.py`: 在 connect() 後呼叫 light_maintain()
- MCP tool: `remagraph_maintain` (force, project_id)
- CLI: `remagraph maintain --project herdr-bridge --force`

## 5. 風險與緩解
- 維護時鎖定：用 transaction + 短時間。
- 資料遺失：先 prune superseded，active 資料永不刪。
- 效能：background + 閾值觸發，非每次 store。
- 跨專案：安全閥 + ensure 強制。

## 6. 驗收條件
- 啟動 herdr-bridge 專案 → 自動用獨立 DB，default 無新 herdr 資料。
- 連續寫 1000 筆後自動 WAL checkpoint。
- 90 天 superseded 自動 prune。
- FTS query 效能維持。
- 安全閥阻擋不合規 store（raise + audit）。
- PPLX 審查共識後實作。

## 7. 與現有對齊
- 延續 D02 schema、D04 audit、arbitration cleanup。
- herdr-bridge 治理層強制呼叫 ensure。
- project_id 為強制欄位（先前 migration 已加）。

**PPLX 審查請求**：請審查此設計是否符合 RemaGraph 核心原則（隔離、安全、自動化、最小干擾）、SQLite 最佳實務、與先前 PPLX 裁決一致。提出修改建議或共識版本。
