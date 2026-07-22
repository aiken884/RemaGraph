# RemaGraph 架構文件

> 版本：0.2.0 | 最後更新：2026-07-22

---

## 1. 系統概觀

RemaGraph 是一把輕量的 MCP 工具，讓 AI coding agent 在處理任務時留下結構化的殘跡（任務交接、狀態更新、發現的限制），供後續 agent 循跡查詢，與 CodeGraph 互補。

v0.2 起同時提供 **CLI**（`store` / `search` / `status` / `init` / `auto`），供 headless agent 與非 MCP 流程使用。

---

## 2. 核心架構

```
┌──────────────────────────────┐   ┌──────────────────────────────────┐
│  MCP Client                  │   │  CLI / headless agent            │
│  Claude / Cursor / OpenCode  │   │  remagraph init|auto|store|...   │
└──────────────┬───────────────┘   └────────────────┬─────────────────┘
               │ stdio MCP tools                     │ argv → cli.py
               ▼                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    remagraph/server.py::main()                   │
│         路由：CLI 子命令  vs  FastMCP stdio (serve)               │
└──────────────┬───────────────────────────────┬──────────────────┘
               │                               │
               ▼                               ▼
┌──────────────────────┐         ┌────────────────────────────────┐
│ cli.py               │         │ FastMCP tools                  │
│ init / auto / store  │         │ remagraph_store/search/status  │
│ search / status      │         └────────────────┬───────────────┘
└──────────┬───────────┘                          │
           │                                      │
           └──────────────────┬───────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  store.py / search.py / arbitration.py / dedup.py / db.py       │
│  （CLI 與 MCP 共用同一套核心邏輯）                                  │
└─────────────────────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  SQLite + FTS5 + audit.jsonl  （REMAGRAPH_STATE_DIR）             │
└─────────────────────────────────────────────────────────────────┘
```

（以下為 MCP 工具與仲裁層細節，與 v1 相同。）

```
│  ┌───────────────┐  ┌────────────┐  ┌────────────────────────┐ │
│  │ store.py      │  │ search.py  │  │ server.py              │ │
│  │ process_store │  │ search_    │  │ (FastMCP dispatch)     │ │
│  │ 流程編排      │  │ memories   │  │                        │ │
│  └───────┬───────┘  │ get_status │  └────────────────────────┘ │
│          │           └────────────┘                             │
│          ▼                                                      │
│  ┌──────────────────────────────────────────────────────┐       │
│  │              仲裁與去重層                             │       │
│  │                                                      │       │
│  │  ┌────────────────┐    ┌──────────────────────────┐  │       │
│  │  │ arbitration.py │    │ dedup.py                 │  │       │
│  │  │ 五條仲裁規則    │    │ model2vec 語意去重       │  │       │
│  │  │ #1 summary長度  │    │ (cosine ≥ 0.90)         │  │       │
│  │  │ #2 learnings態  │    │ potion-multilingual-    │  │       │
│  │  │ #3 handoff_note │    │ 128M 模型               │  │       │
│  │  │ #4 (呼叫dedup)  │    └──────────────────────────┘       │
│  │  │ #5 agent_id格式 │                                       │
│  │  └────────────────┘                                        │
│  └──────────────────────────────────────────────────────┘       │
│          │                                                      │
│          ▼                                                      │
│  ┌──────────────────────────────────────────────────────┐       │
│  │              儲存層                                   │       │
│  │                                                      │       │
│  │  ┌────────────┐  ┌────────────────┐  ┌────────────┐ │       │
│  │  │ db.py      │  │ store.py       │  │ audit.py   │ │       │
│  │  │ 連線管理    │  │ SQL 讀寫        │  │ audit      │ │       │
│  │  │ Schema     │  │ FTS5 trigger   │  │ .jsonl     │ │       │
│  │  │ Migration  │  │ ID 生成        │  │ 寫入       │ │       │
│  │  └─────┬──────┘  └───────┬────────┘  └─────┬──────┘ │       │
│  │        │                 │                  │        │       │
│  │        ▼                 ▼                  ▼        │       │
│  │  ┌──────────────────────────────────────────────┐    │       │
│  │  │         ~/.local/state/remagraph/             │    │       │
│  │  │                                              │    │       │
│  │  │  remagraph.db              audit.jsonl       │    │       │
│  │  │   ├─ memories (主表)       (append-only)     │    │       │
│  │  │   ├─ memories_fts (FTS5)                     │    │       │
│  │  │   └─ indexes                                 │    │       │
│  │  │  (WAL mode, 權限 0600)    (權限 0600)        │    │       │
│  │  └──────────────────────────────────────────────┘    │       │
│  └──────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 模組說明

| 模組 | 職責 |
|------|------|
| `server.py` | 程式進入點。路由 CLI 子命令與 MCP stdio；FastMCP 註冊三個 tool；管理 DB 連線生命週期。 |
| `cli.py` | Headless CLI：`init` / `auto` / `store` / `search` / `status`。JSON stdout；與 MCP 共用 store/search 核心。 |
| `models.py` | Pydantic schema 定義。`StoreRequest`、`SearchRequest`、`StatusRequest` 及對應 Response、核心 `Memory` 型別。 |
| `store.py` | 完整的 store 流程編排（仲裁 → 去重 → supersede/invalidate → ID 生成 → INSERT → audit）。SQLite + FTS5 讀寫。 |
| `arbitration.py` | 五條非 LLM 仲裁規則（summary 長度、learnings 非空、handoff_note 長度、model2vec 去重、agent_id 格式），status_update supersede，discovered_constraint invalidate。 |
| `dedup.py` | model2vec 語意去重（仲裁規則 #4）。載入 `potion-multilingual-128M` 模型，計算 cosine similarity，門檻 0.90。fail-fast。 |
| `search.py` | FTS5 BM25 全文檢索（trigram，CJK）+ 無 query 時依 task_id/agent_id 列表模式；status 查詢。 |
| `db.py` | SQLite 連線管理、state 路徑展開、Schema 初始化與 migration。 |
| `audit.py` | 自管 audit writer。store transaction commit 後 append 到 `audit.jsonl`，僅記錄 stored/error 兩種狀態。 |

---

## 4. 資料流

### 4.1 `remagraph_store` — 寫入流

```
Client Request
    │
    ▼
server.py (FastMCP dispatch)
    │
    ▼
store.py::process_store()
    │
    ├─ 1. run_arbitration_rules_cheap()    仲裁 #1, #2, #3, #5
    │     ├─ summary ≥ 30 字
    │     ├─ learnings 至少一筆
    │     ├─ handoff_note ≥ 20 字 (僅 task_handoff)
    │     └─ agent_id 格式 ^[a-z0-9_-]{3,64}$
    │
    ├─ 2. check_duplicate()                仲裁 #4 (model2vec)
    │     ├─ 編碼 summary → 256 維 embedding
    │     ├─ 與同 kind active 記憶比對 cosine
    │     └─ ≥ 0.90 → reject
    │
    ├─ 3. supersede_status_updates()       僅 status_update
    │     同 task_id 舊記錄 → superseded
    │
    ├─ 4. invalidate_constraints()         僅 discovered_constraint
    │     invalidates 列表 → 標記 invalidated
    │
    ├─ 5. generate_memory_id()             mem-YYYYMMDD-NNN
    ├─ 6. encode_summary()                 存 embedding BLOB
    ├─ 7. INSERT → FTS5 trigger 同步
    ├─ 8. COMMIT
    │
    ▼
audit.py::append_audit()                 寫入 audit.jsonl
    │
    ▼
StoreResponse (stored / rejected / error)
```

### 4.2 `remagraph_search` — 查詢流

```
Client Request
    │
    ▼
server.py (FastMCP dispatch)
    │
    ▼
search.py::search_memories()
    │
    ├─ sanitize_fts5_query()              移除特殊字元, 跳脫關鍵字
    ├─ ≤ 2 字元 → 回傳空結果 (不拋錯)
    ├─ FTS5 BM25 MATCH + kind/status/tags/agent_id/task_id 過濾
    ├─ LIMIT top_k + 1 (判定 has_more)
    │
    ▼
SearchResponse (results[], has_more)
```

### 4.3 `remagraph_status` — 狀態流

```
Client Request
    │
    ▼
server.py (FastMCP dispatch)
    │
    ▼
search.py::get_status()
    │
    ├─ 查詢所有 active status_update
    ├─ 以 task_id 去重，每 task_id 只留 created_at 最新一筆
    ├─ ORDER BY created_at DESC, LIMIT
    │
    ▼
StatusResponse (latest[])
```

---

## 5. 部署模式

### Stdio MCP（v1 唯一模式）

RemaGraph 以 stdio transport 作為 MCP server，由 client 管理子 process 生命週期。

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

### CLI 入口（v0.2）

| 指令 | 用途 |
|------|------|
| `remagraph init --project NAME` | 建立專案 state 目錄與 `env.sh` |
| `remagraph auto --task-id T --agent-id A -- CMD` | 一鍵 recall → 執行 → store |
| `remagraph store / search / status` | 與 MCP 工具對等的 JSON CLI |
| `remagraph serve`（或無子命令） | MCP stdio server |

白話慣例見 [`docs/task-memory-convention.md`](./task-memory-convention.md)。

### 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `REMAGRAPH_STATE_DIR` | SQLite DB 與 audit.jsonl 存放目錄 | `~/.local/state/remagraph/` |
| `TASK_ID` / `AGENT_ID` | 供 `auto` / wrapper 使用的預設識別碼 | 自動產生 / `default-agent` |

### Process 模型

- 單 process，PID 鎖（不支援多實例共用 DB）
- stdio 模式每次啟動載入 model2vec (lazy load)
- DB 使用 WAL 模式，SERIALIZED 隔離層級
- 目錄權限 0700，檔案權限 0600

### 檔案佈局

```
~/.local/state/remagraph/
├── remagraph.db          (SQLite, 0600)
└── audit.jsonl           (append-only audit log, 0600)
```

---

## 6. 技術選型

| 項目 | 選擇 | 理由 |
|------|------|------|
| **語言** | Python ≥ 3.11 | MCP SDK 首選生態, `sqlite3` stdlib |
| **MCP 框架** | `mcp` (FastMCP) | 官方 Python MCP SDK, stdio transport 內建 |
| **資料庫** | SQLite + FTS5 | stdlib 零依賴, trigram tokenizer 支援 CJK |
| **語意去重** | `model2vec` + `potion-multilingual-128M` | 101 語言含中文, 256 維, 純 CPU 推論 |
| **向量儲存** | BLOB (np.float32, little-endian) | v1 只存不查, 未來可接 sqlite-vec |
| **Schema 驗證** | Pydantic | FastMCP 內建支援, 型別安全 |
| **全文檢索** | FTS5 BM25 + trigram | CJK 支援, stdlib 內建 |
| **審計** | JSONL append-only | 簡單可靠, 外部系統可直接 grep |
| **授權** | Apache-2.0 | 與 CodeGraph 一致 |
| **套件管理** | uv | 現代 Python 套件管理 |
| **CI** | GitHub Actions | ubuntu × macos × Python 3.11–3.14 |
