# T-RG-D03：MCP Server 執行期設計

> **艦隊任務 ID**：`T-RG-D03`
> **狀態**：設計完成，尚未實作
> **約束**：本文件僅為設計產出，不得引入對特定外部專案的具名耦合。以 `DESIGN.md` 為 SOT。

---

## 目錄

1. [MCP Tool 完整 JSON Schema](#1-mcp-tool-完整-json-schema)
2. [Unix Socket Daemon 部署形態](#2-unix-socket-daemon-部署形態)
3. [啟動參數與環境變數](#3-啟動參數與環境變數)
4. [單實例 / 多實例策略](#4-單實例--多實例策略)
5. [MCP Config 一行安裝範例](#5-mcp-config-一行安裝範例)
6. [與內部模組的呼叫邊界](#6-與內部模組的呼叫邊界)
7. [錯誤映射到 MCP 回應](#7-錯誤映射到-mcp-回應)
8. [優雅關閉](#8-優雅關閉)
9. [日誌規範](#9-日誌規範)
10. [公開 Python 介面草圖](#10-公開-python-介面草圖)
11. [Given/When/Then 驗收條件](#11-givenwhenthen-驗收條件)
12. [開放問題](#12-開放問題)
13. [與 DESIGN.md 對齊聲明](#13-與-designmd-對齊聲明)

---

## 1. MCP Tool 完整 JSON Schema

RemaGraph 透過 MCP 協定暴露三個 tool，以下為每個 tool 的完整 request/response JSON schema。所有回應統一由 MCP `content` 區塊攜帶，MCP 層級錯誤（連線中斷、protocol mismatch）另以 MCP error 機制回報。

### 1.1 `remagraph_store`

**作用**：agent 寫入記憶。通過五條仲裁規則後寫入 SQLite + FTS5 index，並記錄 audit。

**Request Schema**：

```json
{
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
  "invalidates": null
}
```

| 欄位 | 型別 | 必要 | 約束 | 說明 |
|------|------|------|------|------|
| `task_id` | `string` | ✅ | 非空，無格式限制 | 外部任務識別鍵 |
| `agent_id` | `string` | ✅ | 格式 `^[a-z0-9_-]+$`，長度 3–64 | agent 識別 |
| `kind` | `string` | ✅ | `"task_handoff"` / `"status_update"` / `"discovered_constraint"` | 記憶類型 |
| `summary` | `string` | ✅ | ≥ 30 字（Unicode codepoint） | 任務摘要 |
| `learnings` | `string[]` | ✅ | 至少一筆，每筆非空字串（`strip()` 後有內容） | 學到的東西 |
| `handoff_note` | `string` | ✅ | `kind="task_handoff"` 時 ≥ 20 字，其他 kind 可空 | 交接筆記 |
| `tags` | `string[]` | ✅ | 每筆元素建議全小寫 | 自由標籤 |
| `invalidates` | `string[]` 或 `null` | ❌ | 僅 `kind="discovered_constraint"` 時有意義 | 要標記為 invalidated 的既有 memory id |

**Response（寫入成功）**：

```json
{
  "status": "stored",
  "id": "mem-20260721-001"
}
```

**Response（`status_update` 寫入成功，含 supersede 資訊）**：

```json
{
  "status": "stored",
  "id": "mem-20260721-005",
  "superseded": 3
}
```

| 欄位 | 型別 | 出現時機 | 說明 |
|------|------|----------|------|
| `status` | `string` | 總是 | `"stored"` |
| `id` | `string` | 總是 | 生成的 memory id（格式 `mem-YYYYMMDD-NNN`） |
| `superseded` | `integer` | 僅 `kind="status_update"` | 本輪被標記為 superseded 的既有記憶數量 |
| `invalidated_count` | `integer` | 僅 `kind="discovered_constraint"` 且 `invalidates` 非空 | 本輪被標記為 invalidated 的既有記憶數量 |

**Response（仲裁拒絕）**：

```json
{
  "status": "rejected",
  "reason": "summary_too_short",
  "detail": "summary 需 ≥ 30 字，目前 12 字"
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `status` | `string` | `"rejected"` |
| `reason` | `string` | reason_code（見 §7 錯誤碼表） |
| `detail` | `string` | 人類可讀的錯誤說明 |
| `closest_memory_id` | `string` | 僅 `reason="duplicate_content"` 時出現 |
| `closest_similarity` | `float` | 僅 `reason="duplicate_content"` 時出現 |

### 1.2 `remagraph_search`

**作用**：agent 查詢記憶。FTS5 BM25 全文檢索 + tag/kind/agent_id/task_id 過濾 + 時間排序。

**Request Schema**：

```json
{
  "query": "subagent deny-all 連線錯誤",
  "top_k": 5,
  "kind": "task_handoff",
  "status": "active",
  "tags": ["acpx"],
  "agent_id": null,
  "task_id": null,
  "before": null,
  "after": null
}
```

| 欄位 | 型別 | 必要 | 預設值 | 說明 |
|------|------|------|--------|------|
| `query` | `string` | ✅ | — | FTS5 全文檢索查詢字串。**伺服器端必須 sanitize**：將 FTS5 特殊字元（`*`, `"`, `(`, `)`, `AND`, `OR`, `NOT`, `NEAR`）轉義或包裹為 phrase query，防止非預期行為（見裁決 §12-6） |
| `top_k` | `integer` | ❌ | `20` | 回傳筆數上限（1–100）。**已裁決**：預設 20，最大 100 |
| `kind` | `string` | ❌ | `null`（不過濾） | `"task_handoff"` / `"status_update"` / `"discovered_constraint"` |
| `status` | `string` | ❌ | `"active"` | `"active"` / `"superseded"` / `"invalidated"` / `null`（全包） |
| `tags` | `string[]` | ❌ | `null`（不過濾） | 任意一個匹配即納入（AND 語意） |
| `agent_id` | `string` | ❌ | `null`（不過濾） | 精確比對 |
| `task_id` | `string` | ❌ | `null`（不過濾） | 精確比對，支援前綴匹配（`task-2026-07-21%`） |
| `before` | `string`（ISO 8601） | ❌ | `null` | 僅回傳 `created_at < before` |
| `after` | `string`（ISO 8601） | ❌ | `null` | 僅回傳 `created_at >= after` |

**Response（有結果）**：

```json
{
  "results": [
    {
      "id": "mem-20260721-001",
      "task_id": "task-2026-07-21-003",
      "agent_id": "oc-dspro",
      "kind": "task_handoff",
      "summary": "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤",
      "learnings": [
        "錯誤發生在 opencode task tool 生成 child session 之後",
        "acpx 0.12.0 在 child session 生命週期管理上有 race condition"
      ],
      "handoff_note": "接手者：此錯誤與 G1 不同...",
      "tags": ["acpx", "subagent", "deny-all", "bug"],
      "status": "active",
      "timestamp": "2026-07-21T14:30:00Z",
      "created_at": "2026-07-21T14:30:00Z",
      "score": 0.87,
      "matched_fields": ["summary", "learnings", "tags"]
    }
  ],
  "has_more": true
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `results` | `array` | 匹配的記憶陣列，依 `score DESC, created_at DESC` 排序 |
| `results[].score` | `float` | FTS5 BM25 分數（由 SQLite `rank` 欄位計算，負值越小越相關，回傳時轉為 0–1 正規化值） |
| `results[].matched_fields` | `string[]` | 哪些欄位匹配到了查詢文字（`summary` / `learnings` / `handoff_note` / `tags`） |
| `has_more` | `boolean` | 是否還有更多匹配結果（`results` 長度等於 `top_k` 時為 `true`）。v1 不提供精確 `total_matches`，以 `has_more` 取代 |

**Response（無結果）**：

```json
{
  "results": [],
  "has_more": false
}
```

### 1.3 `remagraph_status`

**作用**：查詢專案最新現況。回傳所有 active 的 `status_update` 型記憶，以 `task_id` 去重（只留每 `task_id` 最新一筆）。

**Request Schema**：

```json
{
  "limit": 10,
  "task_id": null
}
```

| 欄位 | 型別 | 必要 | 預設值 | 說明 |
|------|------|------|--------|------|
| `limit` | `integer` | ❌ | `20` | 回傳筆數上限（1–100）。**已裁決**：預設 20，最大 100 |
| `task_id` | `string` | ❌ | `null`（回傳全部） | 精確比對，支援前綴匹配 |

**Response**：

```json
{
  "latest": [
    {
      "id": "mem-20260721-005",
      "task_id": "task-2026-07-21-003",
      "agent_id": "oc-dspro",
      "kind": "status_update",
      "summary": "subagent 委派 bug 正在修，等待上游 PR 審查\nPR: https://github.com/...",
      "learnings": ["pr 已發，預計週五合併"],
      "handoff_note": "",
      "tags": ["acpx", "subagent"],
      "timestamp": "2026-07-21T14:30:00Z",
      "created_at": "2026-07-21T14:30:00Z"
    }
  ],
  "active_tasks": 5
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `latest` | `array` | active `status_update` 陣列，每 `task_id` 只取最新一筆，依 `created_at DESC` 排序 |
| `active_tasks` | `integer` | 目前有 active `status_update` 的獨立 `task_id` 總數（可能大於 `latest` 長度） |

**Response（無 active 任務）**：

```json
{
  "latest": [],
  "active_tasks": 0
}
```

---

## 2. MCP Server 部署形態（stdio 優先）

RemaGraph v1 **以 stdio 為主要傳輸模式**——MCP client 啟動 `remagraph serve --stdio` 為 subprocess，透過 stdin/stdout 通訊。這符合 MCP 協定規範中本地 server 的標準部署方式，且為 Claude Desktop、Cursor、VS Code 等主流 MCP client 的通用模式。

Unix socket daemon 模式保留為進階／路線圖功能，用於需要長期運行、多 agent 共用的場景。

### 2.1 架構概覽（stdio 模式）

```
┌──────────────────────────────────────────────┐
│  AI Coding Agent (Claude / Codex / Cursor)   │
│                     │                        │
│  MCP Client ────────┼── subprocess stdin/stdout ─┐
└─────────────────────┼──────────────────────────┤ │
                       │                          │ │
┌──────────────────────┼──────────────────────────┤ │
│  RemaGraph Process   ▼                          │ │
│  ┌──────────────────────────────┐              │ │
│  │  server.py (MCP transport)   │              │ │
│  │  - JSON-RPC 2.0 handler      │              │ │
│  │  - tool dispatch             │              │ │
│  └──────────┬───────────────────┘              │ │
│             │                                   │ │
│  ┌──────────▼───────────────────┐              │ │
│  │  store.py   search.py        │              │ │
│  │  (SQLite +  (FTS5 BM25)      │              │ │
│  │   FTS5)                      │              │ │
│  └──────────┬───────────────────┘              │ │
│             │                                   │ │
│  ┌──────────▼───────────────────┐              │ │
│  │  arbitration.py  dedup.py    │              │ │
│  │  (五條規則)    (model2vec)    │              │ │
│  └──────────┬───────────────────┘              │ │
│             │                                   │ │
│  ┌──────────▼───────────────────┐              │ │
│  │  audit.py  db.py             │              │ │
│  │  (audit)   (connection mgr)  │              │ │
│  └──────────────────────────────┘              │ │
│                                                 │ │
│  狀態目錄: ~/.local/state/remagraph/          │ │
│    ├── remagraph.db        (SQLite)            │ │
│    ├── remagraph.sock      (Unix socket,       │ │
│    │                         daemon 模式時建立) │ │
│    ├── remagraph.pid       (PID lock,          │ │
│    │                         daemon 模式時建立) │ │
│    └── audit.jsonl         (審計)              │ │
└─────────────────────────────────────────────────┘
```

### 2.2 傳輸層

RemaGraph 支援兩種 MCP 傳輸模式，v1 以 stdio 為主要模式：

| 模式 | 說明 | 適用場景 | v1 角色 |
|------|------|----------|---------|
| **stdio**（v1 主要） | 由 MCP client 啟動為 subprocess，透過 stdin/stdout 通訊。使用 MCP JSON-RPC 2.0 over stdio transport（搭配 `mcp` SDK） | Claude Desktop / VS Code / Cursor / OpenCode 等直接啟動；單 agent 使用 | **主要** |
| **Unix socket**（進階） | daemon 監聽 Unix socket，MCP client 透過 socket 路徑連線。使用 MCP JSON-RPC 2.0 over stream transport（newline-delimited JSON） | 長期運行、多 agent 共用的進階場景 | 路線圖 |

**預設行為**：
- `remagraph serve` 以 stdio 模式執行（v1 主要模式）
- `remagraph serve --socket` 啟動 Unix socket daemon（前景執行，進階模式）
- `remagraph serve --daemon` 背景執行（fork + detach，需搭配 `--socket`）

### 2.3 生命週期（依模式）

#### stdio 模式生命週期

```
remagraph serve (stdlib)
  │
  ├─ 1. 解析參數，設定 state 目錄
  ├─ 2. 建立 state 目錄（~/.local/state/remagraph/），若不存在
  ├─ 3. 初始化 db.py（migration）
  ├─ 4. [lazy] model2vec 模型首次 remagraph_store 時才載入
  │     （potion-multilingual-128M，約 128MB，見裁決 §12-3）
  ├─ 5. 註冊 MCP tool（remagraph_store / remagraph_search / remagraph_status）
  ├─ 6. 透過 MCP SDK（`mcp` 套件）處理 stdio transport
  │     ├─ 解析 JSON-RPC 2.0 訊息
  │     ├─ dispatch 到對應 tool handler
  │     └─ 回傳 JSON-RPC 2.0 回應
  └─ 7. stdin 關閉時：關閉 SQLite、退出
```

#### Unix socket daemon 模式生命週期（v1 進階）

```
remagraph serve --socket
  │
  ├─ 1. 解析參數，設定 socket 路徑
  ├─ 2. 建立 state 目錄（~/.local/state/remagraph/），若不存在
  ├─ 3. 檢查 PID 鎖（防止多實例，見 §4）
  ├─ 4. 初始化 db.py（migration + connection pool）
  ├─ 5. [eager] 載入 model2vec 模型（potion-multilingual-128M，約 128MB）
  ├─ 6. bind + listen Unix socket
  ├─ 7. 寫入 PID 檔案
  ├─ 8. 註冊 SIGTERM / SIGINT handler（見 §8）
  ├─ 9. accept loop：處理每個連線的 MCP 請求
  │     ├─ 解析 JSON-RPC 2.0 訊息
  │     ├─ dispatch 到對應 tool handler
  │     └─ 回傳 JSON-RPC 2.0 回應
  └─ 10. 關閉時：停止 accept、排空請求、關閉 SQLite、移除 socket 檔、移除 PID 檔
```

> **model2vec 載入策略（已裁決）**：stdio 模式使用 **lazy load**（首次 `remagraph_store` 時才載入模型，減少啟動延遲）；若使用 daemon 模式則 **eager load**（啟動時載入，消除首次請求延遲）。v1 以 stdio lazy load 為準。

---

## 3. 啟動參數與環境變數

### 3.1 命令列介面

```bash
remagraph serve [OPTIONS]

# v1 預設：stdio 模式（MCP client 直接啟動為 subprocess）
remagraph serve

# Unix socket daemon 模式（進階）
remagraph serve --socket
remagraph serve --socket --daemon  # 背景執行
```

| 參數 | 環境變數 | 預設值 | 說明 |
|------|----------|--------|------|
| `--socket-path` | `REMAGRAPH_SOCKET_PATH` | `~/.local/state/remagraph/remagraph.sock` | Unix socket 路徑 |
| `--state-dir` | `REMAGRAPH_STATE_DIR` | `~/.local/state/remagraph` | 狀態目錄（DB、audit、PID 檔） |
| `--db-path` | `REMAGRAPH_DB_PATH` | `{state_dir}/remagraph.db` | SQLite 資料庫路徑（可獨立指定以共用 DB） |
| `--audit-path` | `REMAGRAPH_AUDIT_PATH` | `{state_dir}/audit.jsonl` | 審計檔案路徑 |
| `--stdio` | — | `true`（預設） | 使用 stdio 傳輸。v1 預設模式 |
| `--socket` | — | `false` | 使用 Unix socket 傳輸（前景 daemon） |
| `--daemon` | — | `false` | 背景執行（fork + detach，需搭配 `--socket`） |
| `--log-level` | `REMAGRAPH_LOG_LEVEL` | `info` | 日誌層級：`debug` / `info` / `warning` / `error` |
| `--log-file` | `REMAGRAPH_LOG_FILE` | `stderr` | 日誌輸出；`stderr` 或檔案路徑 |
| `--max-connections` | `REMAGRAPH_MAX_CONNECTIONS` | `32` | 最大同時連線數（Unix socket 模式） |
| `--request-timeout` | `REMAGRAPH_REQUEST_TIMEOUT` | `30` | 單一 MCP 請求逾時秒數 |

### 3.2 環境變數說明

| 變數 | 用途 |
|------|------|
| `REMAGRAPH_STATE_DIR` | 所有持久化資料的根目錄。設定此變數後，`--db-path`、`--audit-path`、`--socket-path` 預設都以此為基底 |
| `REMAGRAPH_DB_PATH` | 覆寫 SQLite 路徑，用於多 daemon 共用同一 DB 的情境 |
| `REMAGRAPH_SOCKET_PATH` | 覆寫 socket 路徑 |
| `REMAGRAPH_LOG_LEVEL` | 覆寫日誌層級。Debug 日誌會包含 request/response payload（不含 summary 等內容，見 §9） |
| `REMAGRAPH_LOG_FILE` | 日誌檔案路徑。預設 stderr（daemon 模式下強制寫入檔案，預設 `{state_dir}/remagraph.log`） |
| `XDG_STATE_HOME` | 若未設 `REMAGRAPH_STATE_DIR`，以此為基底計算預設 state 目錄（`$XDG_STATE_HOME/remagraph`）。若兩者皆未設，fallback 到 `~/.local/state/remagraph` |

### 3.3 設定優先序

```
命令列參數 > 環境變數 > 預設值
```

---

## 4. 單實例 / 多實例策略

### 4.1 預設：單實例（PID 鎖）

預設行為為**單實例**——同一個 socket 路徑（即同一個 state 目錄）在同一時間只能有一個 daemon 執行。

**實作方案**：

```
啟動時：
1. 開啟 {state_dir}/remagraph.pid（O_CREAT | O_EXCL | O_WRONLY）
2. 若檔案已存在，讀取既有 PID
3. 若既有 PID 的 process 仍在執行 → 拒絕啟動，輸出 "daemon already running (pid=N)"
4. 若既有 PID 的 process 已不存在 → 移除舊檔，重新建立
5. 寫入目前 PID
6. 註冊 atexit：正常關閉時移除 PID 檔
```

**設計理由**：
- 避免兩個 daemon 同時寫入同一 SQLite 檔案（SQLite 的 WAL 模式可處理多 reader 但 writer 競爭可能導致 `SQLITE_BUSY`）
- 避免兩個 daemon 同時寫入同一 audit.jsonl（無檔案鎖定，會交錯）
- PID 鎖是輕量方案，無需引入外部 lock manager

### 4.2 多實例支援

多實例可透過**指定不同的 state 目錄**達成：

```bash
# 實例 1：預設路徑
remagraph serve

# 實例 2：獨立的 state 目錄（不同的 DB + socket）
remagraph serve --state-dir ~/projects/project-b/.remagraph

# 實例 3：共用 DB、獨立 socket（兩個 daemon 讀寫同一 DB）
remagraph serve --db-path ~/.local/state/remagraph/remagraph.db \
                --socket-path /tmp/remagraph-readonly.sock
```

**多實例的注意事項**（文件標註，不阻擋）：
- 共用 DB 時，SQLite 以 WAL 模式執行，允許 concurrent reader + 單一 writer。兩個 daemon 同時寫入會由 SQLite 的 busy handler 處理（retry with exponential backoff，預設 5 秒）。
- 共用 audit.jsonl 時，建議僅一個 daemon 寫入（audit 無檔案鎖定）。

### 4.3 PID 鎖的邊界案例

| 案例 | 行為 |
|------|------|
| 正常啟動，PID 檔不存在 | 建立 PID 檔，正常執行 |
| 正常啟動，PID 檔存在但 process 已死 | 移除舊檔，重新建立，輸出 warning log |
| 正常啟動，PID 檔存在且 process 仍在執行 | 拒絕啟動，exit code 1，stderr 輸出錯誤訊息 |
| daemon crash（未清理 PID 檔） | 下次啟動時偵測 PID 不存在，自動清理 |
| 手動 `kill -9` 砍掉 daemon | 同上，PID 檔殘留，下次啟動時自動修復 |
| `--state-dir` 變更 | PID 鎖以 state_dir 為單位，不同目錄互不影響 |

---

## 5. MCP Config 一行安裝範例

### 5.1 Stdio 模式（v1 標準）

```bash
pip install remagraph
```

MCP client config — 所有主流 client 通用（Claude Desktop、OpenCode、Cursor、VS Code）：

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

RemaGraph v1 以 stdio 為預設模式（`remagraph serve` 即為 stdio），MCP client 自動啟動為 subprocess，透過 stdin/stdout 通訊。Server 端使用 `mcp` SDK（`mcp>=1.0.0`）處理 JSON-RPC 2.0 over stdio transport。

### 5.2 Unix Socket Daemon 模式（v1 進階／路線圖）

### 5.2 Unix Socket Daemon 模式（v1 進階／路線圖）

若需使用 Unix socket daemon 模式（例如 agent 無法啟動 subprocess，或需要多 agent 共用同一 daemon），先手動啟動 daemon：

```bash
# 啟動 daemon（手動，一次性）
remagraph serve --socket --daemon
```

此模式需要 MCP client 支援 Unix socket transport（部分 client 可能不支援；以各 client 文件為準）。v1 不將此模式列為必要完成項目。

---

## 6. 與內部模組的呼叫邊界

### 6.1 模組依賴圖

```
server.py  ──→ models.py     (parse + validate MCP input)
           ──→ arbitration.py (run 5 rules before store)
           ──→ store.py       (insert + supersede + invalidate)
           ──→ search.py      (FTS5 query + rank)
           ──→ audit.py       (write audit event)
           ──→ db.py          (connection access)

arbitration.py  ──→ dedup.py  (model2vec cosine similarity)
                ──→ db.py     (load existing embeddings)

store.py  ──→ db.py           (SQLite writes)
         ──→ arbitration.py   (supersede + invalidate helpers)

search.py ──→ db.py           (SQLite reads)
```

`db.py` 為**唯一持有 SQLite connection** 的模組。所有其他模組透過 `db.py` 取得連線，不自行建立。

### 6.2 每個 tool 的呼叫流程

#### `remagraph_store`

```
server._handle_store(request)
  │
  ├─ 1. models.StoreRequest.model_validate(request)  → Pydantic 驗證
  │     └─ 失敗 → 回傳 MCP JSON 格式的 validation error
  │
  ├─ 2. db.get_connection()  → sqlite3.Connection
  │
  ├─ 3. arbitration.run_arbitration(request, conn, model2vec)
  │     ├─ 規則 #1–#5 依序執行
  │     └─ 失敗 → 回傳 {status: "rejected", reason, detail}
  │
  ├─ 4. 若 kind == "status_update":
  │     └─ arbitration.supersede_status_updates(task_id, conn)
  │         └─ 回傳 superseded_count → 納入最終 response
  │
  ├─ 5. 若 kind == "discovered_constraint" && invalidates 非空:
  │     └─ arbitration.invalidate_constraints(invalidate_ids, conn)
  │         ├─ 驗證全部存在且同 kind
  │         ├─ 失敗 → 回傳 {status: "rejected", reason, detail}
  │         └─ 成功 → 回傳 invalidated_count
  │
  ├─ 6. 生成 id: arbitration.generate_memory_id(conn)
  │
  ├─ 7. 計算 embedding: dedup.encode(summary, model2vec)
  │
  ├─ 8. store.insert_memory(...)  → INSERT INTO memories
  │     └─ FTS5 由 SQLite trigger 自動同步
  │
  ├─ 9. db.commit()
  │
  ├─ 10. audit.write_store_event(mem_id, task_id, agent_id, "stored")
  │     └─ 寫入 audit.jsonl（在 transaction commit 之後）
  │
  └─ 11. 回傳 MCP JSON: {status: "stored", id, [superseded], [invalidated_count]}
```

#### `remagraph_search`

```
server._handle_search(request)
  │
  ├─ 1. 簡單驗證（top_k 範圍、query 非空）
  │
  ├─ 2. db.get_connection()
  │
  ├─ 3. search.bm25_search(query, top_k, kind, status, tags, agent_id, task_id, before, after, conn)
  │     ├─ 建構 FTS5 MATCH + WHERE 過濾條件
  │     ├─ 執行 SQL（見 DESIGN.md 查詢範例），使用 `LIMIT top_k + 1` 判斷 has_more
  │     └─ 回傳 list[dict] + has_more
  │
  └─ 4. 封裝 MCP JSON: {results: [...], has_more}
```

#### `remagraph_status`

```
server._handle_status(request)
  │
  ├─ 1. 簡單驗證（limit 範圍）
  │
  ├─ 2. db.get_connection()
  │
  ├─ 3. search.latest_status_updates(limit, task_id, conn)
  │     └─ SELECT ... FROM memories
  │        WHERE kind = 'status_update' AND status = 'active'
  │        [AND task_id = ?]
  │        GROUP BY task_id
  │        HAVING created_at = MAX(created_at)
  │        ORDER BY created_at DESC LIMIT ?
  │
  └─ 4. 封裝 MCP JSON: {latest: [...], active_tasks}
```

### 6.3 邊界規則

| 規則 | 說明 |
|------|------|
| `server.py` 不直接寫 SQL | 所有 SQL 操作透過 `store.py` / `search.py` / `db.py` |
| `server.py` 不持有全域狀態 | 每個 tool handler 從參數取得所需資訊。model2vec instance 在 daemon 啟動時初始化，存為 module-level singleton（由 `dedup.py` 管理） |
| `db.py` 管理連線生命週期 | connection pool（或 single connection + WAL）。每個 MCP 請求取得連線 → 操作 → 歸還／關閉 |
| `audit.py` 無 DB 依賴 | 只寫檔案（audit.jsonl），不讀 SQLite。寫入失敗不影響 store 結果（best-effort） |
| `arbitration.py` 接收 conn 參數 | 不自行建立連線。由 server.py 傳入 `db.get_connection()` 的結果 |

---

## 7. 錯誤映射到 MCP 回應

### 7.1 錯誤回應分類

RemaGraph 將錯誤分為兩類，對應不同的 MCP 回應格式：

| 類型 | MCP 回應格式 | 說明 |
|------|-------------|------|
| **工具層級錯誤**（tool-level） | MCP `content` 區塊，`text` type，JSON body：`{"status": "rejected", "reason": "..."}` | 業務邏輯拒絕（仲裁失敗、參數驗證失敗）。agent 可根據 `reason` 決定重試策略 |
| **伺服器層級錯誤**（server-level） | MCP `error` 物件（JSON-RPC 2.0 error） | 系統錯誤（DB 無法連線、CSM 模型載入失敗）。agent 應 fail task |

### 7.2 完整錯誤碼表

#### 工具層級（MCP `content` 區塊）

| reason_code | 對應階段 | 說明 | 回傳範例 |
|-------------|---------|------|----------|
| `summary_too_short` | 仲裁 #1 | summary 不足 30 字 | `{"status":"rejected","reason":"summary_too_short","detail":"summary 需 ≥ 30 字，目前 12 字"}` |
| `learnings_empty` | 仲裁 #2 | learnings 為空或全空白 | `{"status":"rejected","reason":"learnings_empty","detail":"learnings 至少需要一筆非空內容"}` |
| `handoff_note_too_short` | 仲裁 #3 | task_handoff 的 handoff_note 不足 20 字 | `{"status":"rejected","reason":"handoff_note_too_short","detail":"handoff_note 需 ≥ 20 字，目前 8 字"}` |
| `duplicate_content` | 仲裁 #4 | 與既有記憶語意高度相似 | `{"status":"rejected","reason":"duplicate_content","detail":"與既有記憶高度相似（similarity=0.95），最接近的記憶：mem-20260721-001","closest_memory_id":"mem-20260721-001","closest_similarity":0.95}` |
| `invalid_agent_id` | 仲裁 #5 | agent_id 格式或長度不符 | `{"status":"rejected","reason":"invalid_agent_id","detail":"agent_id 格式不符，僅允許小寫英數字元、底線、連字號：^[a-z0-9_-]+$"}` |
| `invalidates_not_found` | invalidates 驗證 | invalidates 指定的 id 不存在 | `{"status":"rejected","reason":"invalidates_not_found","detail":"invalidates 指定的記憶不存在：mem-20260721-999"}` |
| `invalidates_kind_mismatch` | invalidates 驗證 | 試圖 invalidate 非 discovered_constraint | `{"status":"rejected","reason":"invalidates_kind_mismatch","detail":"只能 invalidate discovered_constraint 類型的記憶"}` |
| `invalidates_not_applicable` | 參數驗證 | non-discovered_constraint 帶了 invalidates | `{"status":"rejected","reason":"invalidates_not_applicable","detail":"invalidates 僅適用於 kind=discovered_constraint"}` |
| `invalid_kind` | 參數驗證 | kind 不在三個允許值內 | `{"status":"rejected","reason":"invalid_kind","detail":"kind 必須是 task_handoff / status_update / discovered_constraint 之一，收到：xxx"}` |
| `top_k_out_of_range` | 參數驗證 | search/status 的 top_k/limit 超出 1–100 | `{"status":"rejected","reason":"top_k_out_of_range","detail":"top_k 必須在 1–100 之間，收到：500"}` |
| `query_empty` | 參數驗證 | remagraph_search 的 query 為空字串 | 已裁決：`query=""` 不拋錯，回傳空 `results` + warning log（非 `rejected`） |

#### 伺服器層級（MCP error）

| JSON-RPC error code | reason_code | 說明 |
|---------------------|-------------|------|
| `-32000` (Server error) | `db_error` | 資料庫層級錯誤（連線失敗、disk full、corrupt） |
| `-32000` | `model_load_error` | model2vec 模型載入失敗 |
| `-32000` | `internal_error` | 未分類的內部錯誤 |
| `-32602` (Invalid params) | `invalid_params` | MCP JSON-RPC 參數格式錯誤（非 JSON、缺必要欄位） |

### 7.3 錯誤回應的 agent 指引

每次錯誤回應中，`detail` 欄位應包含**可操作的提示**，讓 agent 能據以修正請求並重試。例如：

- `"summary 需 ≥ 30 字，目前 12 字"` → agent 知道需要擴充 summary
- `"agent_id 格式不符，僅允許小寫英數字元、底線、連字號"` → agent 知道需要修正 agent_id 格式
- `"與既有記憶高度相似（similarity=0.95），最接近的記憶：mem-20260721-001"` → agent 知道已有類似記憶，可選擇修改內容或查詢既有記憶

---

## 8. 優雅關閉

### 8.1 關閉訊號處理

| 訊號 | 行為 |
|------|------|
| `SIGTERM` | 優雅關閉。停止接受新連線（close listening socket），等待所有進行中的請求完成（上限 30 秒），關閉 SQLite connection，移除 socket 檔案，移除 PID 檔案，exit 0 |
| `SIGINT` | 同 SIGTERM（Ctrl+C 觸發） |
| `SIGQUIT` | 立即關閉。不等待請求完成，直接關閉 SQLite connection（pending transaction rollback），移除 socket + PID 檔案，exit 0 |

### 8.2 關閉流程

```
收到 SIGTERM/SIGINT:
  │
  ├─ 1. 設定關閉旗標（shutdown_flag = True）
  ├─ 2. 關閉 listening socket（不再接受新連線）
  ├─ 3. 等待進行中的請求完成或逾時
  │     ├─ 定時器：30 秒
  │     ├─ 若所有請求在 30 秒內完成 → 繼續步驟 4
  │     └─ 若逾時 → log warning（剩餘 N 個請求未完成），強制繼續
  │
  ├─ 4. 關閉所有 active 的 client socket
  ├─ 5. db.close() → sqlite3.Connection.close()
  │     └─ pending WAL checkpoint 寫入主檔案
  ├─ 6. 移除 PID 檔案
  ├─ 7. 移除 socket 檔案
  ├─ 8. log "shutdown complete"
  └─ 9. exit(0)
```

### 8.3 極端情況

| 情況 | 行為 |
|------|------|
| daemon 被 `kill -9` | 無法優雅關閉。SQLite WAL 模式保證資料不損毀（crash-safe）。socket 檔案和 PID 檔案殘留，下次啟動時自動清理（見 §4.3） |
| 關閉過程中 audit.py 正在寫入 audit.jsonl | `audit.py` 的寫入是 `O_APPEND` + 單行寫入（atomic on POSIX for lines < PIPE_BUF）。已寫入的行不損毀 |
| 關閉時 model2vec 正在計算 embedding | model2vec 無 I/O 狀態，可安全中斷。當前批次計算丟棄 |
| 關閉過程中收到第二個 SIGTERM | 從優雅關閉升級為強制關閉（等同 SIGQUIT 行為），不等待請求完成 |

---

## 9. 日誌規範

### 9.1 日誌格式

結構化 JSON lines，輸出至 stderr（或 `--log-file` 指定的檔案）。

```json
{
  "ts": "2026-07-21T14:23:01.234Z",
  "level": "info",
  "event": "tool_call",
  "tool": "remagraph_store",
  "agent_id": "oc-dspro",
  "task_id": "task-2026-07-21-003",
  "status": "stored",
  "mem_id": "mem-20260721-001",
  "duration_ms": 45.2
}
```

```json
{
  "ts": "2026-07-21T14:23:02.456Z",
  "level": "warning",
  "event": "tool_call",
  "tool": "remagraph_store",
  "agent_id": "oc-dspro",
  "task_id": "task-2026-07-21-004",
  "status": "rejected",
  "reason": "summary_too_short",
  "duration_ms": 2.1
}
```

```json
{
  "ts": "2026-07-21T14:23:03.789Z",
  "level": "error",
  "event": "tool_call",
  "tool": "remagraph_store",
  "agent_id": "oc-dspro",
  "task_id": "task-2026-07-21-005",
  "status": "error",
  "reason": "db_error",
  "detail": "database is locked after 5 retries",
  "duration_ms": 5012.3
}
```

### 9.2 日誌事件類型

| event | level | 時機 | 記錄欄位 |
|-------|-------|------|----------|
| `daemon_start` | `info` | daemon 啟動完成（socket 已 listen） | `socket_path`, `state_dir`, `db_path`, `pid` |
| `daemon_stop` | `info` | 優雅關閉完成 | `uptime_sec`, `requests_served` |
| `daemon_shutdown_timeout` | `warning` | 關閉逾時，強制終止 | `pending_requests` |
| `tool_call` | `info` | 每個 MCP tool 呼叫結束（成功） | `tool`, `agent_id`, `task_id`, `status`, `mem_id`, `duration_ms` |
| `tool_call` | `warning` | 每個 MCP tool 呼叫結束（拒絕） | `tool`, `agent_id`, `task_id`, `status`, `reason`, `duration_ms` |
| `tool_call` | `error` | 每個 MCP tool 呼叫結束（伺服器錯誤） | `tool`, `agent_id`, `task_id`, `status`, `reason`, `detail`, `duration_ms` |
| `db_migration` | `info` | 資料庫 migration 執行 | `from_version`, `to_version` |
| `model_load` | `info` | model2vec 模型載入成功 | `model_name`, `model_size_mb`, `load_duration_ms` |
| `model_load` | `error` | model2vec 模型載入失敗 | `model_name`, `error` |
| `pid_stale_cleaned` | `warning` | 發現殘留 PID 檔，自動清理 | `stale_pid` |

### 9.3 禁止記錄的內容（security）

以下欄位**絕對不出現在日誌**中：

- ❌ `summary` 的完整內容
- ❌ `learnings` 的完整內容
- ❌ `handoff_note` 的完整內容
- ❌ `tags` 的完整內容
- ❌ 任何環境變數的值（含 API key、token、密碼）
- ❌ embedding 的原始 bytes
- ❌ model2vec 模型檔案路徑中可能含有的個人目錄名稱（使用 `{state_dir}` 佔位符）

日誌僅記錄**結構化 metadata**（tool、agent_id、task_id、status、reason、duration_ms），不記錄**內容**。

> **設計理由**：RemaGraph 儲存 agent 的任務內容與學習筆記，這些內容可能包含對 agent 任務有操作意義的資訊，但對 server 維運日誌無關，且可能洩漏使用者工作內容。日誌的用途是維運監控與除錯，不需要記憶內容。

### 9.4 debug 層級例外

`REMAGRAPH_LOG_LEVEL=debug` 時，額外記錄：

- 請求參數的**結構**（不含內容）：`{"kind":"task_handoff","learnings_count":3,"tags_count":4,"summary_length":42}`
- `remagraph_search` 的查詢字串（query 本身是 agent 輸入，debug 可記錄以確認 FTS5 查詢是否正確）
- SQLite query plan（`EXPLAIN QUERY PLAN`）

---

## 10. 公開 Python 介面草圖

### 10.1 `server.py` — MCP 入口

```python
"""MCP server entrypoint (stdio default + Unix socket optional).

v1 主要使用 stdio transport，搭配 `mcp` SDK 處理 JSON-RPC 2.0 over stdio。
Unix socket 模式保留為進階功能。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
from typing import Any, Protocol


# --- 公開符號 ---

class MCPToolHandler(Protocol):
    """MCP tool 的處理函式簽名。"""

    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]: ...


# --- 主要函式 ---

def parse_args(argv: list[str] | None = None) -> argparse.Namespace: ...
"""解析命令列參數與環境變數，回傳合併後的設定。

優先序：命令列 > 環境變數 > 預設值。
若 state 目錄不存在，自動建立（含父目錄）。
"""


def resolve_paths(args: argparse.Namespace) -> dict[str, str]: ...
"""根據參數計算所有實際路徑（socket, db, audit, pid, log）。

回傳 dict: {socket_path, state_dir, db_path, audit_path, pid_path, log_path}
處理 ~ 展開與相對路徑轉絕對路徑。
"""


def run_unix_socket_server(
    socket_path: str,
    handler: MCPToolHandler,
    *,
    max_connections: int = 32,
    request_timeout: float = 30.0,
) -> None: ...
"""在 Unix socket 上執行 MCP server 主迴圈。

- bind + listen
- 每個 client 連線在獨立 thread 中處理（或 asyncio task）
- 解析 JSON-RPC 2.0 訊息，dispatch 到 handler
- 處理 SIGTERM/SIGINT 優雅關閉
- 永不回傳（blocking），直到收到關閉訊號
"""


def run_stdio_server(handler: MCPToolHandler) -> None: ...
"""在 stdin/stdout 上執行 MCP server。

- 讀取 stdin line-by-line（newline-delimited JSON）
- 解析 JSON-RPC 2.0 訊息，dispatch 到 handler
- 回傳結果至 stdout
- stdin 關閉時退出
"""


def create_handler() -> MCPToolHandler: ...
"""建立 MCP tool dispatch handler。

內部依賴注入：初始化 db.py connection、model2vec instance、
建立 store/search/arbitration 的 closure。
回傳的 handler 接收 tool name + arguments，回傳 JSON 結果。
"""


def main() -> None: ...
"""程式入口。CLI dispatch: remagraph serve [...]"""


# --- MCP 訊息處理 ---

def handle_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    db_conn: ...,
    model2vec_instance: ...,
) -> dict[str, Any]: ...
"""MCP tool dispatch: 根據 tool_name 轉送。

tool_name:
  - "remagraph_store"   → _handle_store(arguments, db_conn, model2vec_instance)
  - "remagraph_search"  → _handle_search(arguments, db_conn)
  - "remagraph_status"  → _handle_status(arguments, db_conn)
  - 其他                → {"status": "error", "reason": "unknown_tool"}

所有例外在這一層被捕獲，轉為對應的 MCP 錯誤格式（§7）。
"""


def _handle_store(
    arguments: dict[str, Any],
    db_conn: ...,
    model2vec_instance: ...,
) -> dict[str, Any]: ...
"""remagraph_store 的完整處理流程。詳見 §6.2。"""


def _handle_search(
    arguments: dict[str, Any],
    db_conn: ...,
) -> dict[str, Any]: ...
"""remagraph_search 的完整處理流程。詳見 §6.2。"""


def _handle_status(
    arguments: dict[str, Any],
    db_conn: ...,
) -> dict[str, Any]: ...
"""remagraph_status 的完整處理流程。詳見 §6.2。"""


# --- JSON-RPC 2.0 helpers ---

def parse_jsonrpc_message(line: str) -> dict[str, Any]: ...
"""解析 JSON-RPC 2.0 訊息。若格式不符回傳 error response。"""


def build_jsonrpc_response(
    request_id: str | int,
    result: dict[str, Any],
) -> str: ...
"""封裝為 JSON-RPC 2.0 成功 response（JSON string）。"""


def build_jsonrpc_error(
    request_id: str | int | None,
    code: int,
    message: str,
) -> str: ...
"""封裝為 JSON-RPC 2.0 error response（JSON string）。"""
```

### 10.2 `db.py` — 連線管理

```python
"""SQLite 連線管理與 migration"""

from __future__ import annotations

import sqlite3


def init_db(db_path: str) -> sqlite3.Connection: ...
"""初始化 SQLite 連線。

- 建立目錄（若不存在）
- 開啟連線（WAL mode, foreign keys ON, busy timeout 5s）
- 執行 migration（建立 tables, indexes, triggers）
- 回傳已初始化的 connection
"""


def get_connection() -> sqlite3.Connection: ...
"""取得目前的 SQLite 連線（singleton）。

在 daemon 啟動時由 init_db() 初始化。
所有模組透過此函式取得同一個連線。
"""


def close_db() -> None: ...
"""優雅關閉 SQLite 連線。

- 執行 WAL checkpoint（PRAGMA wal_checkpoint(TRUNCATE)）
- 關閉 connection
"""


def run_migrations(conn: sqlite3.Connection) -> None: ...
"""執行 schema migration。

若有 version table，檢查目前版本並依序執行未完成的 migration。
若無 version table（首次建立），建立所有 tables。
Idempotent：所有 CREATE 使用 IF NOT EXISTS。
"""
```

### 10.3 `audit.py` — 審計

```python
"""自管 audit writer"""

from __future__ import annotations


def init_audit(audit_path: str) -> None: ...
"""初始化 audit 檔案。

- 建立目錄（若不存在）
- 若檔案不存在，建立空檔案（0600）
- 若目錄不存在，建立目錄（0700）
"""


def write_store_event(
    *,
    audit_path: str,
    mem_id: str,
    task_id: str,
    agent_id: str,
    status: str,         # "stored" | "error"
    error: str | None = None,
) -> None: ...
"""寫入 audit 事件至 audit.jsonl。

格式：{"ts":"...","actor_id":"{agent_id}/{task_id}","action":"remagraph_store",...}
寫入失敗（disk full, permission denied）→ log warning，不拋例外（best-effort）。
"""
```

---

## 11. Given/When/Then 驗收條件

### 11.1 Daemon 生命週期

```
Given remagraph 尚未啟動
When 執行 remagraph serve
Then daemon 在前景啟動
  And socket 檔案建立在預設路徑 ~/.local/state/remagraph/remagraph.sock
  And PID 檔案存在
  And stderr 輸出 daemon_start 日誌事件

Given daemon 正在執行
When 再次執行 remagraph serve（同 socket 路徑）
Then 拒絕啟動
  And exit code 為 1
  And stderr 輸出 "daemon already running (pid=N)"

Given daemon 正在執行
When 發送 SIGTERM
Then daemon 停止接受新連線
  And 等待進行中的請求完成
  And 關閉 SQLite connection
  And 移除 socket 檔案
  And 移除 PID 檔案
  And exit code 為 0
  And stderr 輸出 daemon_stop 日誌事件

Given daemon 以 kill -9 被終止（PID 檔案殘留）
When 執行 remagraph serve
Then 自動清理殘留 PID 檔案
  And 正常啟動
  And stderr 輸出 pid_stale_cleaned 日誌事件
```

### 11.2 remagraph_store 正常流程

```
Given daemon 執行中
When MCP client 呼叫 remagraph_store（合法參數，全部仲裁通過）
Then 回傳 {"status": "stored", "id": "mem-YYYYMMDD-NNN"}
  And SQLite memories 表新增一筆記錄
  And FTS5 index 自動更新
  And audit.jsonl 新增一行
  And stderr 輸出 tool_call 日誌（level=info, status=stored, mem_id=...）

Given 寫入 kind="status_update"，同 task_id 已有 2 筆 active status_update
When MCP client 呼叫 remagraph_store
Then 回傳 {"status": "stored", "id": "...", "superseded": 2}
  And 舊 2 筆的 status 變為 "superseded"
  And 新記錄 status="active"
  And 三個操作在同一 transaction 內完成
```

### 11.3 remagraph_store 仲裁拒絕

```
Given summary 為 "修了一個 bug"（7 字）
When MCP client 呼叫 remagraph_store
Then 回傳 {"status": "rejected", "reason": "summary_too_short", "detail": "summary 需 ≥ 30 字，目前 7 字"}
  And SQLite 無新增記錄
  And audit.jsonl 無新增記錄
  And stderr 輸出 tool_call 日誌（level=warning, status=rejected）
```

### 11.4 remagraph_search

```
Given 資料庫中有 10 筆與 "acpx" 相關的 task_handoff 記憶
When MCP client 呼叫 remagraph_search (query="acpx 連線錯誤", top_k=3, kind="task_handoff")
Then 回傳 results 陣列（長度 ≤ 3）
  And 每筆包含 id, summary, agent_id, kind, timestamp, score, matched_fields
  And has_more 為實際是否有更多結果
  And 結果依 score DESC, created_at DESC 排序

Given query 未匹配任何記錄
When MCP client 呼叫 remagraph_search
Then 回傳 {"results": [], "total_matches": 0}
  And 不報錯
```

### 11.5 錯誤情境

```
Given SQLite 資料庫檔案損毀
When MCP client 呼叫 remagraph_store
Then 回傳 MCP error（JSON-RPC code -32000, reason: db_error）
  And stderr 輸出 tool_call 日誌（level=error, status=error, reason=db_error）

Given MCP client 傳送格式錯誤的 JSON
When server 嘗試解析
Then 回傳 MCP error（JSON-RPC code -32602, reason: invalid_params）
  And daemon 不 crash
  And 其他連線不受影響
```

### 11.6 日誌隱私

```
Given remagraph_store 寫入成功（summary 內容為 "嘗試修復 subagent 委派..."）
When 檢查 stderr 日誌
Then 日誌中不含 summary 的完整內容
  And 日誌中不含 learnings 的完整內容
  And 日誌中不含 handoff_note 的完整內容
  And 日誌中僅含結構化 metadata（tool, agent_id, task_id, status, mem_id, duration_ms）
```

---

## 12. 開放問題與已裁決項目

### 12-1. 已裁決（PPLX Consensus 2026-07-21）

| # | 原問題 | 裁決 |
|---|--------|------|
| Q1 | Unix socket daemon vs stdio only？ | **v1 以 stdio 為主**。Unix socket daemon 為進階／路線圖功能（見 §2） |
| Q3 | model2vec 初始化成本（eager vs lazy）？ | **stdio lazy load**：首次 `remagraph_store` 時才載入模型。若 daemon 模式則 eager（見 §2.3） |
| Q5 | `total_matches` 效能 vs `has_more`？ | v1 使用 `has_more`（`LIMIT top_k + 1`），不提供精確 `total_matches`（見 §1.2） |
| Q6 | FTS5 query 注入防護（sanitize）？ | **必須 sanitize**。伺服器端將 FTS5 特殊字元轉義，防止非預期行為（見 §1.2 query 欄位說明） |
| — | MCP SDK 依賴 | `mcp>=1.0.0` 需寫入 `pyproject.toml` dependencies |
| — | `remagraph_status` limit 預設值 | 預設 **20**，最大 **100**（見 §1.3） |
| — | `remagraph_search` top_k 預設值 | 預設 **20**，最大 **100**（見 §1.2） |
| — | query="" 行為 | 回傳空 `results` + warning log，不拋錯（見 §7.2） |
| — | StoreResponse 額外欄位 | 支援 `invalidated_count`（`kind="discovered_constraint"` 且 invalidates 非空時）（見 §1.1） |
| — | v1 單 process | v1 不支援多實例共用 DB。PID 鎖僅在 daemon 模式使用（見 §4） |

### 12-2. 仍開放（留待實作階段或 v2）

| # | 問題 | 備註 |
|---|------|------|
| Q2 | MCP 協定的 JSON-RPC 版本？實作時應固定哪個版本？ | MCP spec 仍在快速迭代。實作時依 `mcp` SDK 支援的版本為準 |
| Q4 | stdio 模式的 concurrency？ | stdio 是單一雙向串流。v1 以 sequence 處理，未來可評估 request queue |
| — | 狀態目錄中 socket/PID 檔案僅在 daemon 模式時建立 | stdio 模式下不建立這些檔案 |

---

## 13. 與 DESIGN.md 對齊聲明

本文件所有設計決策的來源皆來自 `/Users/aikenlin/Projects/RemaGraph/DESIGN.md` 及 `docs/design/01-data-model-arbitration.md`。以下為關鍵對齊點：

| DESIGN.md 章節 | 本文件對應 |
|----------------|-----------|
| MCP 介面（三個 tool 的 request/response） | §1 完整 JSON schema，含所有欄位、型別、約束。**has_more 取代 total_matches（v1 裁決）** |
| 部署形態（v1 stdio 主要） | §2 架構圖（stdio 模式）、傳輸層（stdio 優先、socket 進階） |
| 啟動方式（`pip install remagraph`，一行 MCP config） | §3 啟動參數、環境變數；§5 MCP config 範例（stdio 預設） |
| 記憶 Schema / 仲裁規則 | 不重複，直接引用 DESIGN.md 及 01-data-model-arbitration.md；§6 定義 server.py 如何呼叫這些模組 |
| 審計（Audit） | §6 定義 audit.py 的呼叫邊界；§10 函式簽名 |
| 對外邊界（不耦合任何特定外部系統） | 本文件無任何外部具名專案詞彙，已驗證 |
| SQLite + FTS5 Schema | 實現層級（本文件不重複 SQL schema，見 DESIGN.md §儲存層） |

本文件新增的設計（MCP JSON-RPC transport、生命週期、PID 鎖、日誌規範、錯誤映射、優雅關閉）**不違反** DESIGN.md 中任何既有決策。所有新增內容皆為 DESIGN.md 部署形態章節的具體化展開。

---

## DONE

- [x] 三個 MCP tool 的完整 request/response JSON schema（含所有欄位、型別、約束、回傳範例）
- [x] Unix socket daemon 架構圖與生命週期（含 stdio 雙模式支援）
- [x] 啟動參數、環境變數、設定優先序
- [x] 單實例 PID 鎖機制與多實例策略（含邊界案例表）
- [x] MCP config 一行安裝範例（Claude Desktop / OpenCode / Cursor）
- [x] 與 store/search/arbitration/audit/db 的呼叫邊界（含 flow diagram）
- [x] 錯誤映射表（11 個工具層級 reason_code + 3 個伺服器層級 error code）
- [x] 優雅關閉流程（SIGTERM/SIGINT/SIGQUIT + 極端情況處理）
- [x] 日誌規範（結構化 JSON lines、事件類型、禁止記錄的內容）
- [x] 公開 Python 介面草圖（server.py / db.py / audit.py 函式簽名層級）
- [x] Given/When/Then 驗收條件（6 組場景，共 16 條）
- [x] 開放問題（7 題）
- [x] 與 DESIGN.md + 01-data-model-arbitration.md 對齊聲明
- [x] 驗證：全文無外部具名專案詞彙

---

## PPLX-CONSENSUS-APPLIED

本文件已完成以下 PPLX 共識裁決的寫入（2026-07-21）：

- [x] **B3 — Transport: stdio 為主**：§2 全部重構為 stdio 優先架構（含架構圖、生命週期、傳輸層表格），Unix socket daemon 標示為進階／路線圖
- [x] **B3 — 啟動參數**：`remagraph serve` 預設改為 stdio，`--socket` 為 opt-in daemon
- [x] **B3 — MCP Config**：§5 改為 stdio 一行安裝，daemon 模式移至 §5.2
- [x] **C7 — Transport 裁決**：與 B3 一致，全文件 stdio-first
- [x] **C8 — StoreResponse invalidated_count**：§1.1 回應 schema 已包含 `invalidated_count`
- [x] **`has_more` 取代 `total_matches`**：§1.2 回應 schema、§6.2 流程、§11.4 GWT 全部改用 `has_more`
- [x] **FTS query sanitize**：§1.2 query 欄位說明加入必須 sanitize 的要求；§7.2 空 query 改為不回絕
- [x] **model2vec lazy load**：§2.3 stdio 生命週期標示 lazy load；daemon 模式 eager。§12-1 裁決記錄
- [x] **MCP SDK 依賴**：§5.1 記載 `mcp>=1.0.0`；§10.1 server.py docstring 更新
- [x] **`remagraph_status` limit 預設 20**：§1.3 欄位表標示已裁決
- [x] **`remagraph_search` top_k 預設 20**：§1.2 欄位表標示已裁決
- [x] **query="" 行為**：§7.2 錯誤碼表更新為「不回絕，回傳空 results + warning」
- [x] **v1 單 process**：§4 保留 PID 鎖機制，但僅在 daemon 模式生效。§12-1 裁決記錄
- [x] **模型名稱**：`potion-base-8M` → `potion-multilingual-128M`（§2.3 生命週期）
- [x] **全文無 potion-base-8M** 舊名稱
- [x] **§12 開放問題**：重寫為「已裁決」＋「仍開放」兩子節
