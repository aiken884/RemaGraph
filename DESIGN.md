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
  - 此檔案（`DEFAULT_STATE_DIR` 底下那一份）額外承載一張跨專案共用的 `project_registry` 表，與 `"default"` 專案自己的 memories 共用同一份檔案（見下方「跨專案協作」章節）
- 審計檔案：`~/.local/state/remagraph/audit.jsonl`（0600）

---

## MCP 介面

三個 tool，agent 透過 MCP（v1 使用 stdio transport）直接呼叫：

### `remagraph_store`

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

### `remagraph_search`

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

### `remagraph_status`

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

單一檔案，stdlib 零依賴。目前 `SCHEMA_VERSION = 6`（migration chain v1→v6；v5→v6 新增下方 `memory_labels` 表，v4→v5 新增下方「版本相容性」小節所述的前向相容欄位）。

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

### 版本相容性（`_meta` 前向相容欄位 + 三層判斷）

背景：獨立釘版的舊消費端一旦打開一個 `schema_version` 比自己程式碼還新的資料庫，過去只能整個拒絕開啟（`MigrationError`），且錯誤訊息寫死在舊版程式碼裡——之後即使改善訊息文字，舊消費端也永遠讀不到，因為它執行的是自己那份舊 source（已有 MegaNote、Meshtastic 兩個真實案例撞到「schema_version 比程式碼新，無法降級」而放棄寫入）。解法：把升級指引與相容性邊界存進資料庫本身的 `_meta` 表（消費端一定會開、一定會讀到），而不是只寫在程式碼字串常數裡。

**`_meta` 新增欄位**（schema v4→v5 起，`_migrate_v4_to_v5()` 種下；全新資料庫建立時同步寫入，不必等 migration chain 跑到）：

| 欄位 | 說明 |
|------|------|
| `min_reader_version` | 這個資料庫允許被「讀取」的最舊程式碼 `SCHEMA_VERSION`。目前預設 `"1"` |
| `min_writer_version` | 這個資料庫允許被「寫入」的最舊程式碼 `SCHEMA_VERSION`。每次涉及欄位/CHECK 變動的 migration 都會更新為當時的 `SCHEMA_VERSION`（例如 v4→v5 時寫入 `"5"`；v5→v6 純新增 `memory_labels` 表，不修改 `memories` 本身欄位/CHECK，刻意維持 `min_writer_version` 不變） |
| `upgrade_hint` | 自我完整、不依賴任何程式碼常數的中文升級指引文字，供拒絕/降級訊息附加顯示 |

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

## 跨專案協作（Cross-Project Collaboration）

RemaGraph 每個 `project_id` 對應完全獨立的 state_dir / SQLite 檔案（見「部署形態」），彼此原本互不知道對方存在，各自是一座孤島。本節描述讓 agent 能在需要時「看見」其他專案存在、並精確查詢其標籤的兩層機制——這是後續 `recall_related` 等跨專案查詢能力的地基，本身不含全文檢索或關聯圖功能。

### 跨專案登記表（`project_registry`）

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

### 標籤（`memory_labels`）與跨專案標籤搜尋

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

### 專案隔離安全閥（`safety_validate_project`）與 `remagraph serve` 的單專案綁定

`project_id` 本身只是資料列上的標籤欄位，**真正決定連到哪個實體 SQLite 檔案的是 `REMAGRAPH_STATE_DIR`/`REMAGRAPH_PROJECT` 環境變數**（或明確傳入 `connect()` 的 `state_dir`）。`db.connect(project_id=...)` 內建 `maintenance.safety_validate_project(project_id)` 這道安全閥：透過 `resolve_project_state_dir(project_id)` 算出這個 `project_id` 應該對應的權威 state_dir，並讀取該目錄下的 `project.json`（`db.validate_project_metadata()`）確認其記錄的 `project_id` 與目前要求的一致——不一致（該目錄先前已合法用於另一個 project）一律 `SafetyValveError`，記一筆 `project_metadata_mismatch` 違規稽核，在任何寫入發生之前就擋下。

這道安全閥門只有在呼叫端把 `project_id` 明確傳進 `connect()` 時才會生效；CLI 各子命令與 `remagraph serve` 現在都會這麼做（2026-07-25 修復前，兩者皆以零參數呼叫 `_db.connect()`，安全閥完全不會被觸發，實際連到哪個檔案純看 process 環境當下剛好是什麼——這正是一次真實生產事故的根因：一個專案的 `serve` process 繼承了另一個專案的環境變數，卻悄悄把資料寫進了後者的真實資料庫）。

`remagraph serve` 的專案綁定模型（PPLX 架構審查共識，見下方待決策記錄）：**單一 serve process 嚴格綁定單一 project，且在啟動時就 fail-fast**，不是「第一次呼叫決定綁定」：
- 啟動時必須提供 `--project <id>` 或 `REMAGRAPH_PROJECT` 環境變數其中之一，兩者皆缺席直接非零 exit，不進入 MCP stdio 迴圈
- 綁定成功後印出診斷訊息（實際綁定的 `project_id` 與解析出的 state_dir），若偵測到連線是唯讀降級模式也會提前警告
- 之後任何 tool call（`remagraph_store`/`search`/`status`）帶入與綁定不同、非 `None` 的 `project_id`，一律回傳結構化錯誤，不悄悄沿用/切換連線
- **刻意不支援單一 process 動態路由多個 project**（PPLX 明確否決此設計方向）：SQLite WAL 模式下多條長駐連線的 checkpoint 時機會互相干擾；連線 cache 的 eviction/關閉時機管理複雜；且安全閥本身假設「目前 process 環境只對應一個 project_id」，動態路由會讓這個假設不成立，等於要連帶重新設計安全閥語意。需要同時服務多個專案時，應在 MCP host 層為每個專案各自啟動一個 `remagraph serve --project <id>` process，而非讓單一 server 跨專案路由——這也是 MCP 規格本身建議的分工方式

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
| `action` | 對 `remagraph_store` 交易固定為 `remagraph_store`（見下方對外公告的 Audit Contract，此值不變）；同一份 audit-YYYYMM.jsonl 另外也由 `append_event` 寫入維護／生命週期事件的 action 值（例如 `safety_violation`、`maintenance_completed`、`maintenance_light_failed`），這些記錄是不同、更簡單的結構（不含 `task_id`、`agent_id`、`kind`、`status`、`mem_id` 等欄位） |
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
