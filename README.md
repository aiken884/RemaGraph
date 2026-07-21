# RemaGraph

> **凡走過必留下痕跡。** RemaGraph 是一把輕量的 MCP 工具，任何 AI coding agent 走過後自然留下的殘跡，後人可循跡。與 CodeGraph 互補：CodeGraph 記「這段程式碼有什麼已知問題」，RemaGraph 記「處理時留下了什麼痕跡」。

| 項目 | 現況 |
|------|------|
| **版本** | `0.1.0`（private repo；**尚未** PyPI） |
| **狀態** | v1 可用：stdio MCP 三 tool |
| **設計 SOT** | [`DESIGN.md`](./DESIGN.md) |
| **收斂狀態** | [`docs/reviews/v1-closeout-status.md`](./docs/reviews/v1-closeout-status.md) |
| **Audit 合約** | [`docs/audit.md`](./docs/audit.md) |
| **治理清單** | [`docs/governance/checklist.md`](./docs/governance/checklist.md) |

## 安裝

RemaGraph 尚未發布到 PyPI，請從原始碼安裝：

```bash
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph
pip install -e .
```

依賴：Python ≥3.11、model2vec、mcp (FastMCP)、pydantic。

## 快速開始

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

**OpenCode / Claude Code** — 任何支援 stdio MCP 的 client 皆可，設定方式同上，指定 `command: "remagraph"`、`args: ["serve"]`。

### 2. 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `REMAGRAPH_STATE_DIR` | SQLite DB 存放目錄 | `~/.local/state/remagraph/` |

目錄不存在時自動建立（權限 0700），DB 檔案權限 0600。

### 3. CLI 入門

```bash
# 啟動 stdio MCP server（client 會自行管理生命週期）
remagraph serve

# 自訂 state 目錄
REMAGRAPH_STATE_DIR=/tmp/remagraph-dev remagraph serve
```

## MCP 工具

RemaGraph 透過 MCP（stdio transport）暴露三個 tool，相容 Claude Desktop、Cursor 等主流 MCP 客戶端：

### `remagraph_store` — 寫入記憶

agent 寫入記憶，通過五條仲裁規則後寫入 SQLite + FTS5 index。

| 參數 | 型別 | 說明 |
|------|------|------|
| `task_id` | `str` | 任務識別碼 |
| `agent_id` | `str` | agent 識別碼 |
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

詳細規格見 [`DESIGN.md`](./DESIGN.md)；對外穩定合約見 [`docs/audit.md`](./docs/audit.md)。

## 開發與驗證

```bash
# 建議使用 uv
uv sync --all-extras   # 或依專案慣例安裝 dev deps
uv run ruff check src tests
uv run pytest -m 'not slow'
REMAGRAPH_STATE_DIR=$(mktemp -d) uv run pytest tests/smoke
```

- CI：smoke → lint → test（coverage ≥80）；另有 gitleaks、pip-audit、mutmut（非 blocking）。
- 勿在測試中預設寫入生產 state；冒煙必須使用 `REMAGRAPH_STATE_DIR` 或 pytest `tmp_path`。

## 授權

Apache-2.0
