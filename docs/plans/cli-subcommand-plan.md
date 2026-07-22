# CLI Subcommand 實作計畫

## 目標

讓 headless agent（經 herdr bridge）可直接透過 shell 指令呼叫 RemaGraph，不需要走 MCP protocol。

## 設計

### 指令介面

```
remagraph store \
  --task-id STR --agent-id STR --kind STR --summary STR \
  [--learnings JSON] [--handoff-note STR] [--tags JSON] [--invalidates JSON]

remagraph search --query STR [--top-k INT] [--kind STR] \
  [--status STR] [--tags JSON] [--agent-id STR] [--task-id STR]

remagraph status [--limit INT]

remagraph serve   # 原有 MCP stdio mode（不變）
```

所有 CLI 輸出為 JSON（stdout），錯誤走 stderr，exit code 0 成功 / 1 失敗。

### 實作方式

1. 新增 `src/remagraph/cli.py` — click group，三個子命令
2. 每個子命令：parse args → 建立 Pydantic request → call 內部函式 → print JSON
3. `server.py` 的 `_get_conn()` 抽成共用函式移到 `cli.py` 或共用模組
4. `server.py` 的 `remagraph_store()` / `remagraph_search()` / `remagraph_status()` 邏輯不變，CLI 直接 call store/search module

### 不更動範圍

- 不改 `store.py` / `search.py` / `arbitration.py` / `models.py` 等模組的內部邏輯
- 不改 `pyproject.toml` 的 `[project.scripts]` entry point（仍指向 `server:main`，main 判斷 argv 路由）
- 不加新依賴（用 argparse，不用 click）

### 檔案變更

| 檔案 | 變更 |
|------|------|
| `src/remagraph/cli.py` | **新增**：argparse 子命令 + JSON 輸出 |
| `src/remagraph/server.py` | 將 `_get_conn()` / `_safe_close()` 抽成模組級別共用 |
| `pyproject.toml` | 無變更（entry point 不變） |
