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
| **GitHub** | 個人帳號（private `aiken884/RemaGraph`） |
| **授權** | Apache-2.0（與 CodeGraph 相同） |
| **PyPI** | 目標 `pip install remagraph`；**v1 尚未 publish**（目前 `pip install -e .`／原始碼安裝） |
| **套件版本** | `0.1.0`（見 `pyproject.toml`）；實作收斂 [`docs/reviews/v1-closeout-status.md`](docs/reviews/v1-closeout-status.md) |
| **Python** | 3.11+，uv 管理依賴 |
| **與外部專案的關係** | 完全獨立（無程式碼耦合）。與 herdr-bridge 透過 ACP 直接協調 + 範例整合；組織層（herdr-org）僅設計階段。目前工具層+治理層已完成，任何 AI coding agent 都可直接使用 |

---

## 對外邊界

RemaGraph 是獨立專案，不認識任何外部系統。以下界定它與常見相關專案的關係，防止未來維護者誤設依賴方向。

### RemaGraph 不認識的專案

RemaGraph **不知道**以下專案的存在，它的程式碼、README、CHANGELOG、API 文件中**不應出現**以下名稱：

| 專案 | 關係 |
|------|------|
| herdr-bridge | 無程式碼/API 耦合。透過 ACP 直接跨專案溝通 + examples/herdr-bridge/ 範例對接。herdr-bridge 提供 hooks，RemaGraph 提供 MemoryDispatcher。目前組織層（herdr-org）設計階段 |
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

- **v1 主要使用 stdio transport**（符合 MCP 主流 client 生態）
- `pip install remagraph`，一行 MCP config 即可用
- Unix socket daemon 為進階模式（vN 路線圖）
- v1 **單 process**（PID 鎖），不支援多實例共用 DB 與 concurrency
- state 目錄：`~/.local/state/remagraph/`
- 單一 SQLite 檔案：`~/.local/state/remagraph/remagraph.db`
- 審計檔案：`~/.local/state/remagraph/audit.jsonl`（0600）

---

## MCP 介面

三個 tool，agent 透過 MCP（v1 使用 stdio transport）直接呼叫：

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

### `remagraph_search`

agent 查詢記憶。FTS5 BM25 全文檢索 + tag/kind 過濾 + 時間排序。

**Request：**
```json
{
  "query": "subagent deny-all 連線錯誤",
  "top_k": 20,
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
  ],
  "has_more": false
}
```
- `top_k` 預設 **20**，最大 **100**
- `has_more`：`true` 表示還有更多結果（`LIMIT top_k + 1` 取 k+1 筆），agent 可縮小查詢範圍再查；v1 不提供精確 `total_matches`
- `query=""`（空字串）：回傳空 `results` + `has_more=false`，不拋錯，記錄 warning log
- FTS5 query 輸入前需在 server 端 sanitize（移除/跳脫特殊字元如 `*`、`"`、`AND`、`OR`、`NOT`），防止非預期語法錯誤

### `remagraph_status`

查專案最新現況。回傳所有 active 的 `status_update` 型記憶，以 task_id 去重（只留每 task_id 最新一筆）。

**Request：**
```json
{ "limit": 20 }
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
- `limit` 預設 **20**，最大 **100**

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
| 1 | `summary` ≥ 30 字（`len(summary.strip())`） | 防止空洞（「修了一個 bug」） |
| 2 | `learnings` 至少一筆 | 沒學到東西不該寫記憶 |
| 3 | `handoff_note` ≥ 20 字 | 僅對 `kind=task_handoff` 強制；其他 kind 可空 |
| 4 | model2vec 去重 | `potion-multilingual-128M`（支援 101 語言含中文），cosine similarity ≥ 0.90 拒絕（待中文資料集校準），回傳最相似的既有記憶 ID。模型載入失敗 **fail-fast**，不靜默降級 |
| 5 | `agent_id` 格式 + Lazy Registration | 格式 `^[a-z0-9_-]+$`，長度 **3–64** 字元；首次寫入時自動註冊 |

### 去重補充說明

- 去重僅比對同 `kind` 的 active 記憶
- 去重門檻 v1 統一 **0.90**（標記「待中文資料集校準」）
- 同 kind active ≤ 2,000 筆：全量 cosine 比對；超過時取最新 2,000 筆比對
- 可選按 kind 分設門檻（`task_handoff: 0.90`、`status_update: 0.88`、`discovered_constraint: 0.92`），僅為建議非強制
- `status_update` supersede **嚴格同 task_id**，v1 不跨 task
- `discovered_constraint` invalidate **不做雙向**追溯（不設 `invalidated_by` 回指欄位）

---

## 儲存層：SQLite + FTS5

單一檔案，stdlib 零依賴。

### Schema（SQL）

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
```

### 查詢範例

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

### embedding 欄位策略

- v1 只將 model2vec embedding 存為 BLOB，不做向量查詢
- 格式：`np.float32` little-endian（寫入：`.astype('<f4').tobytes()`，讀回：`np.frombuffer(b, dtype='<f4')`）
- stdio 模式：**lazy load** 模型（process 生命週期短，避免冷啟動延遲）
- 模型載入失敗：**fail-fast**（啟動失敗或第一次呼叫時回傳明確錯誤），不靜默降級
- sqlite-vec 不加
- 未來 v2 若要語意搜尋，`pip install remagraph[vector]` → 對既有 BLOB 建 sqlite-vec index，不用重算全量 embedding

### pyproject.toml（零依賴）

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
| `ts` | ISO 8601 UTC（`Z` 後綴，不支援 local time），與 herdr-bridge audit 格式一致 |
| `actor_id` | `{agent_id}/{task_id}` 複合形式 |
| `action` | 固定為 `remagraph_store`，未來可擴展 |
| `mem_id` | 寫入成功後的 memory id，外部系統比對用 |
| `task_id` | 明確 index key，外部系統可直接 grep |
| `status` | `"stored"` 或 `"error"` |
| `error` | 失敗時填 exception class name（不存 traceback 或 message，最小洩漏原則） |

- v1 不做 audit.jsonl rotation（單一 append-only 檔案，DEFER to v2）

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
| **突變測試** | mutmut（限縮 `arbitration.py` + `dedup.py`，`--runner pytest -n auto`，非阻塞但持續追蹤） |
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

## 設計決策歷程

完整規劃討論記錄見 `herdr-planner-discussion` 專案，包含：

1. 需求釐清：多 agent 共享記憶、agent 自寫自查、指揮塔不介入
2. 技術選型：PPLX 對抗式審查四輪（去重方案、生命週期管理、行為引導、audit 架構、儲存層評估）
3. 命名迭代：五輪 PPLX 討論，最終選定 RemaGraph（Remanent，殘磁）
4. 架構定位：從「herdr 生態系子工具」獨立為通用 MCP server

---

## 未來升級路線（非 v1 範圍）

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

## PPLX-CONSENSUS-APPLIED

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
