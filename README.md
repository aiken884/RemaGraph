# RemaGraph

> **凡走過必留下痕跡。** RemaGraph 是一把輕量的 MCP 工具，任何 AI coding agent 走過後自然留下的殘跡，後人可循跡。與 CodeGraph 互補：CodeGraph 記「這段程式碼有什麼已知問題」，RemaGraph 記「處理時留下了什麼痕跡」。

| 項目 | 現況 |
|------|------|
| **版本** | `0.2.0`（發行準備中；**尚未** PyPI，需 HITL tag） |
| **狀態** | v2：安全/治理/可靠度 + CLI（init/auto/store/search/status） |
| **任務記憶慣例** | [`docs/task-memory-convention.md`](./docs/task-memory-convention.md) |
| **發行準備** | [`docs/reviews/v2-release-prep.md`](./docs/reviews/v2-release-prep.md) |
| **設計 SOT** | [`DESIGN.md`](./DESIGN.md) |
| **收斂狀態** | [`docs/reviews/v1-closeout-status.md`](./docs/reviews/v1-closeout-status.md) |
| **架構文件** | [`docs/architecture.md`](./docs/architecture.md) |
| **Audit 合約** | [`docs/audit.md`](./docs/audit.md) |
| **治理清單** | [`docs/governance/checklist.md`](./docs/governance/checklist.md) |
| **貢獻指南** | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |
| **變更日誌** | [`CHANGELOG.md`](./CHANGELOG.md) |

## 安裝（內部 Alpha 階段）

**目前僅供內部使用，尚未對外發布 PyPI。**

推薦安裝方式（一行指令）：

```bash
uv tool install git+https://github.com/aiken884/RemaGraph.git
```

或從原始碼開發安裝：

```bash
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph
uv pip install -e .
```

依賴：Python ≥3.11、model2vec、mcp (FastMCP)、pydantic。

## 快速開始（非技術使用者，5 分鐘上手）— 內部測試版

1. 安裝（見上方「內部 Alpha 階段」安裝方式）
2. 初始化：
   ```bash
   remagraph init --project myproject
   source ~/.local/state/remagraph-myproject/env.sh
   ```
3. 一鍵跑任務（自動讀記憶 + 執行 + 寫記憶）：
   ```bash
   remagraph auto --task-id fix-login-001 --agent-id my-ai -- echo "這裡換成你的真正指令"
   ```
   或用包裝腳本：
   ```bash
   curl -O https://raw.githubusercontent.com/aiken884/RemaGraph/main/examples/simple/remagraph-task.sh
   chmod +x remagraph-task.sh
   TASK_ID=fix-login-001 AGENT_ID=my-ai ./remagraph-task.sh python my_agent.py
   ```

不需要寫任何程式碼。完整白話說明見 [`docs/task-memory-convention.md`](./docs/task-memory-convention.md)。

**注意**：目前為內部 Alpha 測試階段，功能可能調整，建議用於 herdr Bridge 內部測試。

## MCP 快速開始

### 1. MCP Client 設定

將 RemaGraph 掛到 MCP client 即可。以下為常見 client 設定範例：

**Claude Desktop**（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "remagraph": {
      "command": "remagraph",
      "args": ["serve"],
      "env": {
        "REMAGRAPH_STATE_DIR": "/home/user/.local/state/remagraph"
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
      "args": ["serve"]
    }
  }
}
```

**OpenCode / Claude Code** — 任何支援 stdio MCP 的 client 皆可，設定方式同上。

### 2. 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `REMAGRAPH_STATE_DIR` | SQLite DB 存放目錄 | `~/.local/state/remagraph/` |

目錄不存在時自動建立（權限 0700），DB 檔案權限 0600。路徑已加入安全性檢查（禁止系統目錄）。

### 3. CLI 入門

```bash
# 啟動 stdio MCP server
remagraph serve

# 初始化 / 一鍵任務
remagraph init --project myproject
remagraph auto --task-id T001 --agent-id my-agent -- make test

# 查詢（可只帶 task-id）
remagraph search --task-id T001
remagraph search --query "FastMCP 生命週期" --top-k 5
remagraph status --limit 10
```

## 與 herdr Bridge 整合（指揮塔派工自動帶記憶）

如果你已經用 herdr Bridge 當指揮塔派工給 headless agent：

1. 在你的指揮塔程式中，使用我們提供的極簡幫手：
   ```bash
   # 下載
   curl -O https://raw.githubusercontent.com/aiken884/RemaGraph/main/examples/herdr-bridge/simple-memory-helper.sh
   chmod +x simple-memory-helper.sh
   ```

2. 在派工前呼叫它來取得記憶上下文，然後塞進送給 agent 的文字裡。

或者最簡單：讓 agent 啟動時使用上面的 `remagraph-task.sh` 包裝你的 agent 指令。

這樣指揮塔只要傳 task_id 給 agent，agent 就會自動記錄。

（詳細範例見 examples/herdr-bridge/ ）

## MCP 工具

RemaGraph 透過 MCP（stdio transport）暴露三個 tool，相容 Claude Desktop、Cursor 等主流 MCP 客戶端：

### `remagraph_store` — 寫入記憶

agent 寫入記憶，通過五條仲裁規則後寫入 SQLite + FTS5 index。

| 參數 | 型別 | 說明 |
|------|------|------|
| `task_id` | `str` | 任務識別碼（格式：英數字 + `-_`，最多 64 字元） |
| `agent_id` | `str` | agent 識別碼（同 task_id 格式限制） |
| `kind` | `"task_handoff" \| "status_update" \| "discovered_constraint"` | 記憶類型 |
| `summary` | `str` | 一句話摘要（供 FTS5 全文檢索） |
| `learnings` | `list[str]` | 學到的要點 |
| `handoff_note` | `str` | 交接備註（`task_handoff` 時必填） |
| `tags` | `list[str]` | 分類標籤（選填） |
| `invalidates` | `list[str]` | 要 invalidate 的 memory id（`discovered_constraint` 時用） |

三種 `kind` 的行為：
- **`task_handoff`**：任務交接記錄，附 `handoff_note`
- **`status_update`**：狀態更新，同 `task_id` 自動 supersede 舊記錄
- **`discovered_constraint`**：發現的限制，可 `invalidates` 既有錯誤記憶

### `remagraph_search` — 查詢記憶

FTS5 BM25 全文檢索（trigram tokenizer，支援 CJK）+ tag/kind/agent_id/task_id 過濾。

| 參數 | 型別 | 說明 |
|------|------|------|
| `query` | `str` | 搜尋關鍵字（支援中英日韓） |
| `top_k` | `int` | 回傳筆數上限（預設 20，最大 100） |
| `kind` | `str` | 過濾記憶類型（選填） |
| `status` | `"active" \| "superseded" \| "invalidated"` | 過濾狀態（選填） |
| `tags` | `list[str]` | 過濾標籤（選填） |
| `agent_id` | `str` | 過濾 agent（選填） |
| `task_id` | `str` | 過濾任務（選填） |

短查詢（≤2 字元）回傳空結果不拋錯。

### `remagraph_status` — 查詢專案最新現況

回傳所有 active 的 `status_update` 記憶，以 `task_id` 去重（每 task 只留最新一筆）。

| 參數 | 型別 | 說明 |
|------|------|------|
| `limit` | `int` | 回傳筆數上限（預設 20，最大 100） |

## 治理與安全

- **Rate limiting**：per-agent token bucket（60 calls/60 秒），防止濫用
- **輸入驗證**：`task_id` / `agent_id` 經 Pydantic validator 檢核格式
- **路徑安全**：`REMAGRAPH_STATE_DIR` 禁止系統目錄路徑
- **Audit rotation**：`audit-YYYYMM.jsonl` 按月自動分檔
- **DB 容量**：SQLite `max_page_count` 設定 100MB soft limit
- **Migration**：內建 schema 版本追蹤與 migration chain
- **超期清理**：`cleanup_superseded()` 可清理 90 天前的非 active 記錄

## CLI 子命令（headless agent 用）

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

## 開發與驗證

```bash
# 建議使用 uv
uv sync --all-extras
uv run ruff check src tests
uv run mypy src/
uv run pytest -m 'not slow'
REMAGRAPH_STATE_DIR=$(mktemp -d) uv run pytest tests/smoke
```

- CI：smoke → lint（ruff + mypy）→ test（coverage ≥80）；另有 gitleaks、pip-audit、mutmut（非 blocking）。
- 勿在測試中預設寫入生產 state；冒煙必須使用 `REMAGRAPH_STATE_DIR` 或 pytest `tmp_path`。

## 授權

Apache-2.0
