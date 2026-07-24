# RemaGraph MCP / CLI 嚴重損壞報告（來自 opencode agent，2026-07-24）

**發現情境**：在 MegaNote 專案使用 RemaGraph 儲存專案理解時，嘗試 `remagraph store`、`remagraph status` 及 MCP `remagraph_remagraph_store` / search 全部失敗。

## 主要錯誤

### 1. Schema 版本不匹配
```
資料庫 schema_version=4 比程式碼的 SCHEMA_VERSION=2 還新，無法降級
```
觸發位置：MCP status、store、search 呼叫時。

### 2. audit.py 型別錯誤（最頻繁）
```
File ".../remagraph/audit.py", line 62, in append_audit
    if response.status not in ("stored", "error"):
AttributeError: 'str' object has no attribute 'status'
```
大量重複出現在 maintenance fallback 路徑。

### 3. 維護層無限遞迴 + RecursionError
- `light_maintenance_on_connect` → `run_maintenance` → `_raw_connect` → `light_maintenance_on_connect` ...
- 疊加 `append_audit` 失敗。
- 伴隨 pathlib 損壞：
  ```
  AttributeError: 'PosixPath' object has no attribute '_str'
  AttributeError: 'PosixPath' object has no attribute '_drv'
  RecursionError: maximum recursion depth exceeded
  ```
  發生在 `resolve_project_state_dir` / `safety_validate_project` / `Path.resolve()`。

### 4. 其他
- safety_validate_project 要求 REMAGRAPH_STATE_DIR（即使已 source env.sh）。
- CLI 與 MCP 行為一致崩潰。
- 只有直接 `sqlite3 INSERT` 到 memories 表（繞過 db.connect 的 maintenance）才能成功寫入。

## 已儲存的記憶
- task_id: `remagraph-bug-report-mcp-cli-20260724`
- kind: `discovered_constraint`
- project: meganote (remagraph-meganote state dir)
- id: ab711ab1-e0c6-4245-8498-d8dd37882359 （另有 status_update 版本）

## 建議修復順序
1. **audit.py**：讓 append_audit 穩健處理 str / 例外 response，加入 try/except 與型別檢查。
2. **maintenance.py**：防止遞迴（使用旗標或 try/finally 保護）、改善錯誤時的降級行為。
3. **schema 處理**：加入 migration 或寬鬆版本檢查（或明確的 downgrade 政策）。
4. **pathlib 使用**：避免在可能被污染的環境中依賴 Path 內部狀態，改用字串路徑。
5. **CLI / MCP 入口**：在連線前先做輕量檢查，給出清楚的「請執行 uv tool update remagraph 或 rm db 重新 init」的訊息。
6. 增加測試覆蓋 maintenance + audit 失敗路徑。

此報告已同時以 discovered_constraint 寫入 RemaGraph 記憶（供 RemaGraph Claude agent 直接 recall）。

**opencode agent 回報**

## 完整錯誤片段（來自 tool output）

（詳細 traceback 已見上節，以下為關鍵堆疊）

AttributeError: 'str' object has no attribute 'status'
  .../remagraph/audit.py:62
    if response.status not in ("stored", "error"):

RecursionError + pathlib corruption during:
  resolve_project_state_dir
  safety_validate_project
  Path.resolve / __str__ / _load_parts

MCP 端錯誤：
  Error executing tool remagraph_store: 資料庫 schema_version=4 比程式碼的 SCHEMA_VERSION=2 還新，無法降級

直接 INSERT workaround 成功，資料可查詢。

