# RemaGraph — 設計規格書

> **凡走過必留下痕跡。** RemaGraph 是一把輕量的 MCP 工具，任何 AI coding agent 走過後自然留下的殘跡，後人可循跡。與 CodeGraph 互補：CodeGraph 記「這段程式碼有什麼已知問題」，RemaGraph 記「處理時留下了什麼痕跡」。

---

## 專案基本資訊

| 項目 | 內容 |
|------|------|
| **名稱** | RemaGraph |
| **詞源** | Remanent（殘磁）——外部力量走了，痕跡還在 |
| **定位** | 獨立開源 MCP server，agent 工具箱裡的一把工具 |
| **擁有者** | Aiken Lin 個人 side project |
| **GitHub** | 個人帳號 |
| **授權** | Apache-2.0（與 CodeGraph 相同） |
| **PyPI** | `pip install remagraph` |
| **Python** | 3.11+，uv 管理依賴 |
| **與外部專案的關係** | 完全獨立。不屬於 herdr-bridge / herdr-gov，不認識 Herdr 生態系。任何 AI coding agent 都可以直接使用 |

---

## 對外邊界

RemaGraph 是獨立專案，不認識任何外部系統。以下界定它與常見相關專案的關係，防止未來維護者誤設依賴方向。

### RemaGraph 不認識的專案

RemaGraph **不知道**以下專案的存在，它的程式碼、README、CHANGELOG、API 文件中**不應出現**以下名稱：

| 專案 | 關係 |
|------|------|
| herdr-bridge | RemaGraph 與 herdr-bridge 沒有程式碼層級或 API 層級的互動。RemaGraph 不引用 herdr-bridge，herdr-bridge 亦不引用 RemaGraph |
| herdr-gov | 同上，RemaGraph 不認識 herdr-gov |

### 外部排程系統如何消費 RemaGraph

RemaGraph 對外只暴露一個穩定的合約：**Audit Contract**（詳見下方「審計」章節的「Audit Contract」小節）。任何排程系統（例如 herdr-gov）只需要知道兩件事：

1. audit 檔案路徑：`~/.local/state/remagraph/audit.jsonl`
2. 以 `task_id` 為 key 查 `action="remagraph_store"` 且 `status="stored"` 的記錄

合約的單一真相來源（SOT）是本文件。RemaGraph 若修改 audit schema，會在 release note 中公告。消費方（外部排程系統）的 config 應指向 RemaGraph 的 audit 路徑，並附註解指向本文件。

### 禁止的耦合

以下行為明確違反 RemaGraph 的邊界設計：

- ❌ 在 RemaGraph 的程式碼中 import herdr-bridge
- ❌ 在 RemaGraph 的 README 中提及「專為 Herdr 設計」
- ❌ 在 herdr-bridge 的 README 或 API 文件中提及 RemaGraph
- ❌ 讓 RemaGraph 的 MCP tool 名稱帶有 Herdr 前綴

---

## 與 CodeGraph 的互補定位

| | CodeGraph | RemaGraph |
|---|---|---|
| **記憶主體** | 程式碼符號 | agent 活動 |
| **記什麼** | 這段程式碼有什麼已知問題、ADR、慣例 | 處理時學到了什麼、發現了什麼限制、交接線索 |
| **隱喻** | 結構本身（骨架） | 結構運作後留下的殘跡（副產品） |
| **範例** | 「`AcpxAdapter` 要注意 OPENCODE_CONFIG 不是設定合併鏈最終權威」 | 「修 bug #5 時發現 acpx 有 race condition，接手者請先確認上游 PR 狀態」 |
| **查詢觸發** | 開啟檔案時自動撈 | agent 開工前主動搜 |

---

## 部署形態

- **獨立 Unix socket daemon process**（比照 CodeGraph）
- `pip install remagraph`，一行 MCP config 即可用
- state 目錄：`~/.local/state/remagraph/`
- 單一 SQLite 檔案：`~/.local/state/remagraph/remagraph.db`
- 審計檔案：`~/.local/state/remagraph/audit.jsonl`（0600）

---

## MCP 介面

三個 tool，agent 透過 MCP（Unix socket）直接呼叫：

### `remagraph_store`

agent 寫入記憶。觸發五條仲裁規則，通過後寫入 SQLite + 同步 FTS5 index。

**Request：**
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
  "tags": ["acpx", "subagent", "deny-all", "bug"]
}
```

**Response（成功）：**
```json
{ "status": "stored", "id": "mem-20260721-001" }
```

**Response（被仲裁拒絕）：**
```json
{ "status": "rejected", "reason": "summary_too_short", "detail": "需 ≥ 30 字，目前 12 字" }
```

### `remagraph_search`

agent 查詢記憶。FTS5 BM25 全文檢索 + tag/kind 過濾 + 時間排序。

**Request：**
```json
{
  "query": "subagent deny-all 連線錯誤",
  "top_k": 5,
  "kind": "task_handoff",
  "status": "active"
}
```

**Response：**
```json
{
  "results": [
    {
      "id": "mem-20260721-001",
      "summary": "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤",
      "agent_id": "oc-dspro",
      "timestamp": "2026-07-21T14:30:00Z",
      "score": 0.87
    }
  ]
}
```

### `remagraph_status`

查專案最新現況。回傳所有 active 的 `status_update` 型記憶，以 task_id 去重（只留每 task_id 最新一筆）。

**Request：**
```json
{ "limit": 10 }
```

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
  ]
}
```

---

## 記憶 Schema

三種 `kind`，每條記錄包含：`id`、`task_id`、`agent_id`、`timestamp`、`kind`、`summary`、`learnings[]`、`handoff_note`、`tags[]`、`status`。

| kind | 用途 | 生命週期 | 範例 |
|------|------|----------|------|
| `task_handoff` | 做了什麼、學到什麼、交接筆記 | 永遠 active | 「修 bug #5 時發現 acpx 有 race condition」 |
| `status_update` | 專案現況（PR merged、bug 發現、等待決策） | 同 `task_id` 自動 supersede | 「PR #4 merged，subagent bug 正在修」 |
| `discovered_constraint` | 發現的限制或陷阱 | 永遠 active，agent 可顯式 `invalidates=[id]` | 「OPENCODE_CONFIG 不是設定合併鏈最終權威」 |

`status_update` 的 supersede 規則：寫入新 `status_update` 時，自動將**同 `task_id`** 的所有舊 `status_update` 標記為 `superseded`。`task_id` 是精確的結構化鍵，不做語意判斷。

---

## 輕量仲裁（寫入端，零 LLM、零人類介入）

每筆 `remagraph_store` 請求必須通過全部五條規則，任一失敗即拒絕並回傳原因：

| # | 規則 | 說明 |
|---|---|---|
| 1 | `summary` ≥ 30 字 | 防止空洞（「修了一個 bug」） |
| 2 | `learnings` 至少一筆 | 沒學到東西不該寫記憶 |
| 3 | `handoff_note` ≥ 20 字 | 交接不能空白 |
| 4 | model2vec 去重 | `potion-base-8M`（8MB），cosine similarity ≥ 0.92 拒絕，回傳最相似的既有記憶 ID |
| 5 | `agent_id` 格式 + Lazy Registration | 格式 `^[a-z0-9_-]+$`，首次寫入時自動註冊 |

---

## 儲存層：SQLite + FTS5

單一檔案，stdlib 零依賴。

### Schema（SQL）

```sql
-- 主表
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('task_handoff', 'status_update', 'discovered_constraint')),
    task_id    TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    summary    TEXT NOT NULL,
    learnings  TEXT NOT NULL DEFAULT '[]',   -- JSON array
    handoff_note TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]',   -- JSON array
    status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'invalidated')),
    embedding  BLOB,                         -- model2vec vector (numpy bytes)，v1 只存不查
    created_at TEXT NOT NULL,                -- ISO 8601
    updated_at TEXT NOT NULL
);

-- FTS5 虛擬表（BM25 全文檢索，增量寫入自動更新）
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    summary,
    learnings,
    handoff_note,
    tags,
    content='memories',
    content_rowid='rowid'
);

-- INSERT 自動同步 FTS5
CREATE TRIGGER IF NOT EXISTS memories_ai
AFTER INSERT ON memories BEGIN
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
```

### 查詢範例

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

### embedding 欄位策略

v1 只將 model2vec embedding 存為 BLOB（numpy bytes），不做向量查詢。sqlite-vec 不加。未來 v2 若要語意搜尋，`pip install remagraph[vector]` → 對既有 BLOB 建 sqlite-vec index，不用重算全量 embedding。

### pyproject.toml（零依賴）

```toml
[project]
name = "remagraph"
requires-python = ">=3.11"
dependencies = [
    "model2vec>=0.1.0",
]
# sqlite3 是 stdlib，不列

[project.optional-dependencies]
vector = ["sqlite-vec>=0.1.0"]
```

---

## 審計（Audit）

### 設計原則

RemaGraph 自管 audit，不依賴任何外部系統。herdr-gov（或其他排程系統）透過讀取此檔案驗證 agent 是否完成記憶寫入。

### 路徑

`~/.local/state/remagraph/audit.jsonl`（0600，目錄 0700）

### Schema

```jsonl
{"ts":"2026-07-21T14:23:01.234Z","actor_id":"agent_id/task_id","action":"remagraph_store","mem_id":"mem-20260721-001","task_id":"task-2026-07-21-003","status":"stored","error":null}
```

| 欄位 | 說明 |
|------|------|
| `ts` | ISO 8601 UTC，與 herdr-bridge audit 格式一致 |
| `actor_id` | `{agent_id}/{task_id}` 複合形式 |
| `action` | 固定為 `remagraph_store`，未來可擴展 |
| `mem_id` | 寫入成功後的 memory id，外部系統比對用 |
| `task_id` | 明確 index key，外部系統可直接 grep |
| `status` | `"stored"` 或 `"error"` |
| `error` | 失敗時填 message（不存 traceback，最小洩漏原則） |

### Audit Contract（給外部排程系統）

RemaGraph 對外公告的合約（本節可獨立引用）：

- **路徑**：`~/.local/state/remagraph/audit.jsonl`
- **驗證方式**：以 `task_id` 為 key 查 audit，找 `action="remagraph_store"` 且 `status="stored"` 的記錄
- **未寫入的行為**：未找到記錄時，排程系統應自行決定處理策略（例如發 follow-up prompt 提醒 agent、記錄 `memory_write_failed`）
- **schema 變更**：RemaGraph 若修改 audit schema，會在 release note 中公告

---

## CI/CD 品質門檻

沿用 herdr-bridge 標準：

| 門檻 | 設定 |
|------|------|
| **測試** | pytest（單元測試 + MCP 整合測試） |
| **覆蓋率** | `pytest --cov=src/remagraph --cov-fail-under=80` |
| **突變測試** | mutmut（核心邏輯模組，非阻塞但持續追蹤） |
| **機密掃描** | gitleaks（每 push / PR，全 Git 歷史） |
| **簽章** | DCO（`git commit -s`） |
| **CI** | GitHub Actions：ubuntu × macos × Python 3.11–3.14 |

---

## 專案結構

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
│       └── pip-audit.yml
├── src/
│   └── remagraph/
│       ├── __init__.py
│       ├── server.py               # MCP server entrypoint（Unix socket daemon）
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

## 設計決策歷程

完整規劃討論記錄見 `herdr-planner-discussion` 專案，包含：

1. 需求釐清：多 agent 共享記憶、agent 自寫自查、指揮塔不介入
2. 技術選型：PPLX 對抗式審查四輪（去重方案、生命週期管理、行為引導、audit 架構、儲存層評估）
3. 命名迭代：五輪 PPLX 討論，最終選定 RemaGraph（Remanent，殘磁）
4. 架構定位：從「herdr 生態系子工具」獨立為通用 MCP server

---

## 未來升級路線（非 v1 範圍）

```
v1: SQLite + FTS5（零依賴，BM25 全文檢索）
  ↓
v2: SQLite + FTS5 + sqlite-vec（語意搜尋，pip install remagraph[vector]）
  ↓
vN: DuckDB（百萬級資料，複雜分析查詢）
  ↓
vN+1: PostgreSQL + pgvector（多人協作，雲端服務）
```

每個階段的觸發條件是實際使用量與使用者回饋，而非預先規劃。
