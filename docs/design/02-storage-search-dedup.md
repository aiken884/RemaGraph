# T-RG-D02：儲存層、搜尋引擎與去重模組設計

> **艦隊任務 ID**：`T-RG-D02`
> **狀態**：設計完成，尚未實作
> **約束**：本文件僅為設計產出，不得引入對特定外部專案的具名耦合。以 `DESIGN.md` 為 SOT。所有模組（`store.py` / `search.py` / `dedup.py` / `db.py`）的實作目前皆為 `raise NotImplementedError`。

---

## 目錄

1. [SQLite Schema 評審與修正建議](#1-sqlite-schema-評審與修正建議)
2. [State 路徑、權限與 Migration 策略](#2-state-路徑權限與-migration-策略)
3. [db.py — 連線管理與 Schema 初始化](#3-dbpy--連線管理與-schema-初始化)
4. [store.py — 記憶體讀寫 API](#4-storepy--記憶體讀寫-api)
5. [search.py — BM25 全文檢索](#5-searchpy--bm25-全文檢索)
6. [dedup.py — model2vec 語意去重](#6-deduppy--model2vec-語意去重)
7. [Embedding BLOB 策略：v1 只存不查、v2 sqlite-vec 升級路徑](#7-embedding-blob-策略v1-只存不查v2-sqlite-vec-升級路徑)
8. [併發與 Locking](#8-併發與-locking)
9. [失敗模式](#9-失敗模式)
10. [各模組完整介面簽名草圖](#10-各模組完整介面簽名草圖)
11. [Given/When/Then 驗收條件](#11-givenwhenthen-驗收條件)
12. [PPLX 審查裁決（已定案）](#12-pplx-審查裁決已定案)
13. [與 DESIGN.md 及 D01 對齊聲明](#13-與-designmd-及-d01-對齊聲明)

---

## 1. SQLite Schema 評審與修正建議

DESIGN.md 的「儲存層：SQLite + FTS5」章節已定義完整 schema。本節逐項評審，指出需要修正或補充之處。

### 1.1 主表 `memories`

DESIGN.md 的 `CREATE TABLE memories` 定義**基本正確**，但缺少以下欄位：

| 缺失 | 說明 | 建議 |
|------|------|------|
| `timestamp` 欄位 | D01 定義了 `Memory.timestamp`（寫入時間，ISO 8601 UTC），但 DESIGN.md 的 DDL 只有 `created_at` / `updated_at`，沒有獨立的 `timestamp` | **必須補上**。`timestamp` 是 MCP 回傳中的寫入時間；`created_at` 是內部審計時間。兩個欄位通常相等，但語意不同 |
| `invalidated_by` 欄位 | D01 §4.3 明確決定「不回傳 invalidates 清單」、「不在被 invalidate 的記錄上儲存 `invalidated_by`」 | **不需要補**。這是刻意的設計選擇，非缺失 |

**修正後的主表 DDL：**

```sql
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('task_handoff', 'status_update', 'discovered_constraint', 'fleet_member')),
    task_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    timestamp   TEXT NOT NULL,                -- 寫入時間（ISO 8601 UTC），MCP 回傳用
    summary     TEXT NOT NULL,
    learnings   TEXT NOT NULL DEFAULT '[]',   -- JSON array
    handoff_note TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',   -- JSON array
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'invalidated')),
    embedding   BLOB,                         -- model2vec vector (numpy bytes)，v1 只存不查
    created_at  TEXT NOT NULL,                -- ISO 8601
    updated_at  TEXT NOT NULL                 -- ISO 8601
);
```

> **⚠️ 已裁決**：DESIGN.md 的 DDL 缺少 `timestamp` 欄位。`timestamp` 是寫入時間（MCP 回傳用），`created_at` 是內部審計時間，兩者語意不同。**必須補上**（PPLX 審查裁決 C6）。

### 1.2 FTS5 虛擬表

DESIGN.md 的 FTS5 定義需修正 tokenizer。FTS5 預設 `unicode61` tokenizer 對 CJK 文字**視為連續字元串，不進行分詞**（並非 bigram），導致中文全文檢索召回率極低。

**修正後**使用 `tokenize='trigram'`，對所有文字（含 CJK）做三元組（trigram）分詞：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    summary,
    learnings,
    handoff_note,
    tags,
    tokenize='trigram',
    content='memories',
    content_rowid='rowid'
);
```

> **注意**：trigram tokenizer 需要 SQLite ≥ 3.34（2020-12 釋出）。若 runtime SQLite 版本過舊，降級方案為應用層手動 bigram 前處理後再送入 FTS5（文件層級記載，v1 不實作自動降級）。CI 驗收應包含「確認 SQLite 支援 trigram」之設計註記。

### 1.3 Triggers（FTS5 同步）

DESIGN.md 定義了 `AFTER INSERT` 和 `AFTER DELETE` 兩個 trigger。**缺了 `AFTER UPDATE`**：

```sql
-- INSERT 自動同步 FTS5（DESIGN.md 已有，保留）
CREATE TRIGGER IF NOT EXISTS memories_ai
AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;

-- DELETE 自動同步 FTS5（DESIGN.md 已有，保留）
CREATE TRIGGER IF NOT EXISTS memories_ad
AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
END;

-- UPDATE 自動同步 FTS5（DESIGN.md 缺失，必須補上）
CREATE TRIGGER IF NOT EXISTS memories_au
AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;
```

> **⚠️ 已裁決**：DESIGN.md 缺少 `memories_au`（AFTER UPDATE）trigger。當 `status` 變更為 `superseded` 或 `invalidated` 時，雖然摘要文字未變，但 UPDATE 若涉及 `summary` / `learnings` / `handoff_note` / `tags` 欄位的修改，FTS5 索引會變成過期資料。**必須補上此 trigger**（PPLX 審查裁決 C5）。

### 1.4 Indexes

DESIGN.md 定義的 indexes 正確且完整：

```sql
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(task_id);
CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC);
```

**補充建議**：為高效支援 dedup.py 的查詢（「同 kind、status = 'active' 的所有記憶」），建議加入複合 index：

```sql
-- dedup.py 的核心查詢：載入所有同 kind 的 active 記憶 embedding
CREATE INDEX IF NOT EXISTS idx_memories_dedup
    ON memories(kind, status) WHERE status = 'active';
```

此為部分索引（partial index），僅索引 `status = 'active'` 的列，大幅減少 dedup 掃描範圍。

### 1.5 最終完整 DDL（彙整修正後版本）

```sql
-- 主表（已補 timestamp）
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('task_handoff', 'status_update', 'discovered_constraint', 'fleet_member')),
    task_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    summary     TEXT NOT NULL,
    learnings   TEXT NOT NULL DEFAULT '[]',
    handoff_note TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'invalidated')),
    embedding   BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- FTS5 虛擬表（trigram tokenizer，支援 CJK）
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    summary,
    learnings,
    handoff_note,
    tags,
    tokenize='trigram',
    content='memories',
    content_rowid='rowid'
);

-- Triggers（已補 AFTER UPDATE）
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;

-- Indexes（已補 dedup 複合 index）
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(task_id);
CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_dedup ON memories(kind, status) WHERE status = 'active';
```

---

## 2. State 路徑、權限與 Migration 策略

### 2.1 路徑與權限

| 項目 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| State 目錄 | `~/.local/state/remagraph/` | `0700` | 僅擁有者可讀寫執行。`~` 展開為 `$HOME`（Linux）或 `/Users/{user}`（macOS），跨平台一致 |
| SQLite 資料庫 | `~/.local/state/remagraph/remagraph.db` | `0600` | 僅擁有者可讀寫。SQLite 會在寫入時自動建立 `-wal` 和 `-shm` 檔案（WAL 模式下），這些暫存檔案繼承目錄權限 |
| Audit 記錄 | `~/.local/state/remagraph/audit.jsonl` | `0600` | 逐行 JSON，append-only |

> **注意**：`~/.local/state/` 遵循 XDG Base Directory 規範。macOS 上 `~/.local/state/` 並非系統預設路徑，但 `pathlib.Path.home() / ".local" / "state"` 可跨平台建立。

### 2.2 目錄初始化

```
首次 import remagraph 或 server 啟動時：
  1. os.makedirs(state_dir, mode=0o700, exist_ok=True)
  2. 若目錄已存在但權限不符 → 不自動修正，僅 log warning
  3. 開啟 SQLite 連線 → schema 初始化（見 §3）
```

### 2.3 Migration 策略

RemaGraph 採用**基於版本號的漸進式 migration**，不使用 Alembic 等外部工具（保持零依賴）。

#### 版本追蹤

在 `remagraph.db` 中建立 `_meta` 表存放 schema 版本：

```sql
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 初始化時寫入當前 schema 版本
INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '1');
```

#### Migration 執行流程

```
db.connect()
  │
  ├─ 1. 檢查 _meta 表是否存在 → 不存在 = 全新資料庫，執行完整 DDL（§1.5）
  │
  ├─ 2. 讀取 schema_version
  │     v1（初始）→ 無需 migration
  │     v2（未來）→ 執行 v1→v2 的 migration script
  │     vN（未來）→ 依序執行 v1→v2→...→vN
  │
  └─ 3. 全部 migration 在單一 transaction 內執行
       若任一步驟失敗 → ROLLBACK，raise MigrationError
```

#### Migration 規則

1. **所有 DDL 使用 `IF NOT EXISTS`**，確保冪等（idempotent）
2. **不降級**（no downgrade）。Schema 只向前演进
3. **版本號不可跳躍**。從 v1 升級到 v3 必須經過 v2 的 migration
4. **v1→v2 的預留**：若需加 sqlite-vec extension 或新欄位，在 `db.py` 中新增 `_migrate_v1_to_v2(conn)` 函式，並於版本偵測時呼叫

#### 開發階段的便利性

開發期間若 schema 變更頻繁，提供以下逃生口：
- 刪除 `~/.local/state/remagraph/remagraph.db` 後重啟（`pip install` 重裝後自動重建）
- 或在測試中使用 `:memory:` SQLite 資料庫（測試永遠從空白開始）

---

## 3. db.py — 連線管理與 Schema 初始化

`db.py` 負責 SQLite 連線的生命週期與 schema 初始化。它是 `store.py` 和 `search.py` 的底層依賴。

### 3.1 設計原則

- **單一連線**：整個 process 共用一個 `sqlite3.Connection`，由 `db.py` 管理
- **WAL 模式**：啟用 Write-Ahead Logging，支援並行讀寫（讀不阻塞寫、寫不阻塞讀）
- **SERIALIZED 隔離**：Python `sqlite3` 模組在 `check_same_thread=False` 時自動啟用序列化模式。MCP server 預設為單執行緒（asyncio event loop），不需擔心多執行緒競爭
- **Foreign keys 強制**：`PRAGMA foreign_keys = ON`

### 3.2 連線參數

```python
SQLITE_PARAMS = {
    "database": "~/.local/state/remagraph/remagraph.db",  # 展開後
    "isolation_level": None,            # 自動 commit 模式；手動管理 transaction
    "check_same_thread": False,         # asyncio 事件迴圈可能在不同執行緒
}
```

### 3.3 公開函式

| 函式 | 說明 |
|------|------|
| `get_db_path() -> Path` | 展開 `~` 並回傳完整路徑。若目錄不存在則自動建立（`mode=0o700`） |
| `connect() -> sqlite3.Connection` | 建立連線、啟用 WAL、設定 pragma、執行 schema 初始化與 migration |
| `close(conn)` | 關閉連線。server shutdown 時呼叫 |
| `_init_schema(conn)` | 執行完整 DDL（§1.5）。所有語句使用 `IF NOT EXISTS` 確保冪等 |
| `_run_migrations(conn)` | 檢查 `_meta.schema_version` 並依序執行 migration |

---

## 4. store.py — 記憶體讀寫 API

`store.py` 是 `remagraph_store` MCP tool 的後端實作。它**不包含仲裁邏輯**（仲裁由 `arbitration.py` 處理），只負責 SQLite 讀寫操作。

### 4.1 設計原則

- **每個公開寫入函式都接受 `conn` 參數**，由呼叫方（`server.py`）管理 transaction 邊界。`store.py` 自己不呼叫 `conn.commit()`
- **所有寫入都在呼叫方控制的單一 transaction 內**
- **`id` 生成由 `store.py` 負責**（`mem-YYYYMMDD-NNN`），使用 `INSERT OR IGNORE` + 重試機制保證並發安全

### 4.2 公開 API

#### `generate_memory_id(conn, /, *, now: datetime | None = None) -> str`

生成唯一記憶 ID，格式 `mem-YYYYMMDD-NNN`。

```
演算法：
1. 取當日日期 → YYYYMMDD
2. 查當日最大 NNN：
   SELECT MAX(CAST(SUBSTR(id, 14) AS INTEGER)) FROM memories
   WHERE id LIKE 'mem-YYYYMMDD-%'
3. NNN = max_nnn + 1（若無則 NNN = 1），zero-padded 到三位數
4. 回傳 f"mem-{YYYYMMDD}-{NNN:03d}"

並發安全：此函式應在 transaction 內呼叫。若有競爭（同一秒內兩次呼叫），
後者會因 PRIMARY KEY 衝突失敗，呼叫方應 retry。
```

#### `insert_memory(conn, memory: Memory, embedding: numpy.ndarray | None) -> str`

插入一筆記憶記錄。

```
參數：
  conn:       SQLite 連線（呼叫方已開啟 transaction）
  memory:     完整的 Memory 物件（含 id, timestamp 等伺服器端欄位）
  embedding:  model2vec 編碼後的 numpy array，或 None

行為：
  INSERT INTO memories (id, kind, task_id, agent_id, timestamp, summary,
                        learnings, handoff_note, tags, status, embedding,
                        created_at, updated_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

回傳：記憶 id（與 memory.id 相同）
```

#### `supersede_status_updates(conn, task_id: str) -> int`

將同 `task_id` 的所有 active `status_update` 標記為 `superseded`。

```sql
UPDATE memories
SET status = 'superseded', updated_at = ?
WHERE task_id = ? AND kind = 'status_update' AND status = 'active'
```

回傳被影響的筆數。

#### `invalidate_constraints(conn, invalidate_ids: list[str]) -> int`

將指定的 `discovered_constraint` 記憶標記為 `invalidated`。

```sql
UPDATE memories
SET status = 'invalidated', updated_at = ?
WHERE id IN (?, ?, ...) AND kind = 'discovered_constraint' AND status = 'active'
```

回傳被影響的筆數。

> **注意**：此函式**不驗證** `invalidates_not_found` 或 `invalidates_kind_mismatch`。這些驗證由 `arbitration.py` 的 `invalidate_constraints()` 在呼叫此函式**之前**完成。

#### `get_memory_by_id(conn, memory_id: str) -> Memory | None`

依 `id` 查詢單筆記憶。供 `dedup.py` 回傳 `closest_memory_id` 時的 detail 使用。

#### `get_active_embeddings(conn, kind: MemoryKind) -> list[tuple[str, bytes]]`

載入同 `kind`、`status = 'active'` 的所有記憶的 `(id, embedding)`。供 `dedup.py` 做 cosine similarity 比對。

```sql
SELECT id, embedding FROM memories
WHERE kind = ? AND status = 'active' AND embedding IS NOT NULL
```

回傳 `[(id, embedding_bytes), ...]`。若無結果回傳空 list。

#### `get_latest_status_updates(conn, limit: int = 10) -> list[Memory]`

供 `remagraph_status` 使用。回傳所有 active 的 `status_update`，以 `task_id` 去重（每 `task_id` 只取最新一筆）。

```sql
SELECT m.* FROM memories m
INNER JOIN (
    SELECT task_id, MAX(created_at) AS max_ts
    FROM memories
    WHERE kind = 'status_update' AND status = 'active'
    GROUP BY task_id
) latest ON m.task_id = latest.task_id AND m.created_at = latest.max_ts
WHERE m.kind = 'status_update'
ORDER BY m.created_at DESC
LIMIT ?
```

---

## 5. search.py — BM25 全文檢索

`search.py` 是 `remagraph_search` MCP tool 的後端實作。

### 5.1 設計原則

- **BM25 為預設排序**（FTS5 內建 `bm25()` 排名函式）
- **過濾鏈**：先以 FTS5 MATCH 做全文過濾，再加上 `kind` / `tags` / `status` 等條件
- **`tags` 過濾使用 LIKE**，因 tags 以 JSON array 形式儲存（`["acpx", "bug"]`）
- **SQL injection 防護**：所有使用者輸入（`query` 除外）使用參數化查詢。`query` 直接嵌入 FTS5 MATCH 字串，但需先做 sanitize（移除 FTS5 特殊字元：`*`、`"`、`(`、`)`、`AND`、`OR`、`NOT`）

### 5.2 查詢參數

| 參數 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `query` | `str` | 必要 | 全文檢索關鍵字。傳入 FTS5 MATCH，支援 BM25 自然語言查詢 |
| `top_k` | `int` | `20` | 回傳筆數上限。最小值 1，最大值 100 |
| `kind` | `MemoryKind \| None` | `None` | 過濾記憶類型。`None` = 不過濾 |
| `status` | `MemoryStatus \| None` | `None` | 過濾生命週期狀態。`None` = 不過濾 |
| `tags` | `list[str] \| None` | `None` | 過濾標籤。AND 邏輯（需同時滿足所有指定 tag）。`None` = 不過濾 |
| `agent_id` | `str \| None` | `None` | 過濾 agent |
| `task_id` | `str \| None` | `None` | 過濾任務 |

### 5.3 SQL 建構邏輯

```python
BASE_QUERY = """
SELECT m.*, fts.rank
FROM memories_fts fts
JOIN memories m ON fts.rowid = m.rowid
WHERE memories_fts MATCH ?
"""

# 動態附加過濾條件
filters: list[str] = []
params: list = [sanitized_query]

if kind is not None:
    filters.append("m.kind = ?")
    params.append(kind)

if status is not None:
    filters.append("m.status = ?")
    params.append(status)

if agent_id is not None:
    filters.append("m.agent_id = ?")
    params.append(agent_id)

if task_id is not None:
    filters.append("m.task_id = ?")
    params.append(task_id)

if tags:
    # JSON array 中的 LIKE 比對。每個 tag 需獨立存在於 JSON 中
    for tag in tags:
        filters.append("m.tags LIKE ?")
        params.append(f'%"{tag}"%')

# 組合
where_clause = " AND ".join(filters) if filters else ""
order_clause = "ORDER BY fts.rank, m.created_at DESC"
limit_clause = "LIMIT ?"

full_query = f"{BASE_QUERY} AND {where_clause} {order_clause} {limit_clause}"
```

### 5.4 FTS5 Query Sanitization

FTS5 將以下字元視為語法標記，必須在使用者輸入中移除或跳脫：

```python
FTS5_SPECIAL_CHARS = ['*', '"', '(', ')', 'AND', 'OR', 'NOT']

def sanitize_fts5_query(raw: str) -> str:
    """移除 FTS5 特殊字元，保留純文字查詢。"""
    for char in ['*', '"', '(', ')']:
        raw = raw.replace(char, ' ')
    # 移除 FTS5 保留字（僅在作為獨立 token 時）
    for word in ['AND', 'OR', 'NOT']:
        raw = raw.replace(f' {word} ', ' ')
        if raw.startswith(f'{word} '):
            raw = raw[len(word):]
        if raw.endswith(f' {word}'):
            raw = raw[:-len(word)]
    return raw.strip()
```

### 5.5 回傳格式

```python
@dataclass
class SearchResult:
    memory: Memory
    score: float           # BM25 rank（FTS5 的 rank 欄位為負值，較高（接近 0）= 較相關）
    rank: int              # 排名（1-based）


@dataclass
class SearchResponse:
    results: list[SearchResult]
    has_more: bool          # 是否還有更多符合條件的結果（v1 不提供精確 total）
    query: str
```

---

## 6. dedup.py — model2vec 語意去重

`dedup.py` 實作仲裁規則 #4：將新記憶的 `summary` 與同 `kind` 的所有 active 記憶做語意相似度比對。

### 6.1 設計原則

- **model2vec `potion-multilingual-128M`**（128MB 模型），透過 `model2vec` 套件載入
- **cosine similarity ≥ 0.90 視為重複**（v1 初始值，待中文資料集校準）
- **同 kind 內比對**（`status_update` 不與 `task_handoff` 比）
- **只比對 `status = 'active'` 的記憶**（superseded / invalidated 的不參與比對）
- **延遲載入模型**：首次呼叫 `check_duplicate()` 時才載入 `potion-multilingual-128M`，避免 import `remagraph` 時的啟動延遲
- **載入失敗 → fail-fast**：raise `ModelLoadError`，不靜默降級
- **比對上限 2000 筆**：同 kind active ≤ 2000 全量；超過取最新 2000

### 6.2 模型規格

| 屬性 | 值 |
|------|-----|
| **模型名稱** | `potion-multilingual-128M` |
| **來源** | `model2vec` 套件（`pip install model2vec>=0.1.0`） |
| **大小** | ~128MB |
| **輸出維度** | 768（`potion-multilingual-128M` 的靜態 embedding 維度） |
| **支援語言** | 多語言（含中文、英文、日文、韓文等 CJK 語言） |
| **編碼方式** | `model2vec.StaticModel.encode()` → `numpy.ndarray` |
| **最大輸入長度** | 512 token（model2vec 的 tokenizer 限制）。`summary` 超過 512 token 時取前 512 token 做 embedding |
| **儲存格式** | `numpy.ndarray.astype(np.float32).tobytes()`（little-endian `<f4`）→ SQLite BLOB；讀回：`np.frombuffer(b, dtype=np.float32)` |
| **載入策略** | 延遲載入（首次呼叫時初始化）；載入失敗 → **fail-fast**（raise `ModelLoadError`，不靜默降級） |

### 6.3 去重流程

```
check_duplicate(summary, kind, conn):
  │
  ├─ 1. 載入 model2vec 模型（若尚未載入）。載入失敗 → raise ModelLoadError（fail-fast）
  │
  ├─ 2. 將 summary 編碼為 embedding vector：
  │     vec = model.encode(summary[:512_tokens])
  │
  ├─ 3. 從 store 載入同 kind 的所有 active embedding（上限 2000）：
  │     candidates = store.get_active_embeddings(conn, kind, limit=2000)
  │     → [(id, embedding_bytes), ...]
  │     若同 kind active 超過 2000 筆 → 取最新 2000（created_at DESC）
  │
  ├─ 4. 若 candidates 為空（首筆寫入）→ 直接通過
  │
  ├─ 5. 對每個 candidate：
  │     existing_vec = np.frombuffer(embedding_bytes, dtype=np.float32)
  │     sim = cosine_similarity(vec, existing_vec)
  │
  ├─ 6. 取 max_similarity。若 ≥ 0.90（待中文資料集校準）：
  │     失敗 → 回傳 closest_memory_id, closest_similarity
  │     若 < 0.90：
  │     通過
  │
  └─ 回傳 ArbitrationResult(passed=True/False, ...)
```

### 6.4 Cosine Similarity 計算

```python
import numpy as np

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """計算兩個向量的 cosine similarity。"""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))
```

### 6.5 效能考量

| 情境 | 資料量 | 預估耗時 | 策略 |
|------|--------|----------|------|
| 首筆寫入 | 0 筆 active | < 1ms | 直接跳過 |
| 一般使用 | < 100 筆 active | < 10ms | 全量線性掃描，效能可接受 |
| 中量使用 | 100–2,000 筆 active | 10–100ms | 全量線性掃描，仍在可接受範圍 |
| 大量使用 | > 2,000 筆 active | — | 取最新 2,000 筆比對（`created_at DESC LIMIT 2000`），不做 ANN 索引。v1 以此限制確保效能可預測 |

### 6.6 模型單例管理

```python
_model: StaticModel | None = None

def _get_model() -> StaticModel:
    """延遲載入 model2vec 模型（Singleton）。載入失敗 → raise ModelLoadError。"""
    global _model
    if _model is None:
        from model2vec import StaticModel
        _model = StaticModel.from_pretrained("potion-multilingual-128M")
    return _model
```

### 6.7 去重僅在同 kind 內的理由（設計決策重述）

`status_update` 的 supersede 已依 `task_id` 精確處理，不該被跨 kind 的語意相似度阻擋寫入。去重的目的是防止「同一件事被記錄兩次」，而非「不同類型的記憶出現相似用語」。

---

## 7. Embedding BLOB 策略：v1 只存不查、v2 sqlite-vec 升級路徑

### 7.1 v1 策略

- **只存不查**：`dedup.py` 透過 `store.get_active_embeddings()` 載入同 kind 的 active embedding（上限 2000 筆）做全量線性掃描。不依賴任何向量索引
- **BLOB 格式**：`numpy.ndarray.astype(np.float32).tobytes()`（32-bit float little-endian `<f4`，768 維 = 3072 bytes per vector）。跨架構（x86/ARM）一致，因為 `np.float32` 在所有主流平台都是 IEEE 754 little-endian
- **NULL 容許**：若 model2vec 載入失敗 → **fail-fast**（raise `ModelLoadError`），不寫入 NULL embedding。模型為必要依賴

### 7.2 v2 升級路徑

觸發條件（任一即升級）：
- 資料庫中 active 記憶 > 5,000 筆，`check_duplicate()` 線性掃描 > 200ms
- 使用者需求：語意搜尋（`remagraph_search` 支援 embedding-based semantic search）

升級步驟：

```
1. pip install remagraph[vector] → 安裝 sqlite-vec

2. db.py 自動偵測 sqlite-vec 可用性 → 建立向量索引：
   -- sqlite-vec 的虛擬表（儲存 embedding 向量）
   CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0(
       embedding float[768]
   );

3. 背景批次將既有 BLOB 轉入 sqlite-vec：
   -- 從 memories.embedding BLOB 讀出 → 寫入 memories_vec
   -- 不需要重跑 model2vec.encode()，直接從既有 BLOB 讀取

4. dedup.py 改用 sqlite-vec 的 KNN 查詢：
   SELECT id, distance FROM memories_vec
   WHERE embedding MATCH ?
   ORDER BY distance
   LIMIT 10;

5. search.py 新增 hybrid search 模式：
   -- BM25 (FTS5) + embedding KNN (sqlite-vec)
   -- 合併分數：final_score = α * bm25_score + (1-α) * vec_similarity

6. Migration 版本號 incremented → v2
```

### 7.3 為什麼 v1 不做 sqlite-vec

- 保持零依賴（`model2vec` 是唯一外部依賴，`sqlite3` 是 stdlib）
- `pip install remagraph` 即可用，無需編譯 C extension
- v1 的線性掃描在 < 1,000 筆 active 記憶時完全夠用
- sqlite-vec 是 optional dependency（`pip install remagraph[vector]`），使用者自行決定何時啟用

---

## 8. 併發與 Locking

### 8.1 SQLite Locking 模型

RemaGraph 使用 SQLite 的 **WAL（Write-Ahead Logging）模式**：

| Locking 層級 | 行為 |
|-------------|------|
| 讀取（SELECT） | 不阻塞。WAL 模式下讀取直接讀 snapshot |
| 寫入（INSERT/UPDATE/DELETE） | 序列化。SQLite 同一時間只允許一個 writer；多個 writer 排隊等待 |
| 讀寫並行 | 完全支援。Reader 不阻塞 Writer，Writer 不阻塞 Reader |

### 8.2 MCP Server 的併發模型

MCP server（Unix socket daemon）使用 `asyncio`：

```
asyncio event loop（單執行緒）
  ├─ 接收 MCP request
  ├─ 建立 SQLite transaction
  ├─ 呼叫 arbitration.py → dedup.py
  ├─ 呼叫 store.py（INSERT/UPDATE）
  ├─ 呼叫 audit.py（寫 audit.jsonl）
  └─ commit transaction
```

由於 asyncio event loop 本質上單執行緒，多個 MCP request 會序列化處理（或在 asyncio task 之間交錯，但不會同時執行 Python 位元組碼）。此設計下：

- **不需要 application-level lock**（如 `threading.Lock`）
- **`sqlite3` 的 SERIALIZED 模式**已提供足夠的並發安全（當 `check_same_thread=False` 時自動啟用）

### 8.3 Transaction 邊界

一個完整的 `remagraph_store` 請求對應一個 SQLite transaction：

```
BEGIN TRANSACTION
  ├─ 規則 #1–#5 仲裁
  ├─ 若 kind=status_update → supersede 同 task_id 舊記錄
  ├─ 若 kind=discovered_constraint 且有 invalidates → invalidate
  ├─ generate_memory_id()
  ├─ INSERT INTO memories
  └─ (FTS5 trigger 自動同步)
COMMIT → 成功後寫 audit.jsonl
```

**若任何步驟失敗 → ROLLBACK**。audit.jsonl 不寫（因為是在 commit 之後才寫）。這保證了 SQLite 與 audit.jsonl 之間的最終一致性（audit.jsonl 可能缺少記錄，但不會有孤兒記錄）。

### 8.4 audit.jsonl 的 Write Safety

audit.jsonl 使用 `open(path, "a")` 的 append 模式寫入。在 POSIX 系統上，小於 `PIPE_BUF`（通常 4KB）的單次 `write()` 是原子的。RemaGraph 的 audit 記錄約 200–400 bytes，遠小於此上限，因此跨 process 的 concurrent append 不會交錯。

---

## 9. 失敗模式

### 9.1 錯誤分類

| 類別 | 範例 | 處理策略 |
|------|------|----------|
| **可恢復的輸入錯誤** | `summary_too_short`、`invalid_agent_id` | 回傳 `{status: "rejected", reason: "..."}`，不回傳 HTTP 500 |
| **基礎設施錯誤** | 磁碟已滿、權限不足、資料庫損毀 | 回傳 `{status: "error", reason: "db_error"}`，log 完整 traceback（不洩漏給 client） |
| **模型錯誤** | model2vec 載入失敗 | **fail-fast**：raise `ModelLoadError`。不降級，模型為必要依賴 |
| **Transaction 錯誤** | COMMIT 失敗（db locked、disk full） | ROLLBACK，回傳 error。audit.jsonl 無記錄 |

### 9.2 各模組的失敗處理

#### db.py

| 失敗情境 | 行為 |
|----------|------|
| 目錄不存在且無法建立（權限不足） | `raise OSError`，附建議（`chmod 700 ~/.local/state/remagraph/`） |
| SQLite 檔案損毀 | `raise sqlite3.DatabaseError`。建議使用者執行 `rm ~/.local/state/remagraph/remagraph.db` 重建 |
| Migration 失敗 | `raise MigrationError`。不降級，手動介入 |

#### store.py

| 失敗情境 | 行為 |
|----------|------|
| `generate_memory_id` 的 INSERT OR IGNORE 衝突（極端並發） | 重新查詢當日最大 NNN 並重試（最多 3 次），仍失敗則 `raise MemoryIDGenerationError` |
| INSERT 失敗（constraint violation） | 由呼叫方 transaction ROLLBACK，回傳 error |
| UPDATE 影響 0 筆（例如 supersede 時無符合條件的記錄） | 正常。回傳 `0`，不視為錯誤 |

#### search.py

| 失敗情境 | 行為 |
|----------|------|
| FTS5 MATCH 語法錯誤（例如 query 為空或僅含特殊字元） | 回传空結果（`{"results": [], "has_more": false}`），不拋例外 |
| 資料庫中無 FTS5 index | `raise sqlite3.OperationalError`。這表示 schema 初始化失敗 |

#### dedup.py

| 失敗情境 | 行為 |
|----------|------|
| model2vec 模型不存在（首次載入，需下載） | 首次 `StaticModel.from_pretrained("potion-multilingual-128M")` 會自動下載（~128MB）。若網路不可用 → **fail-fast**：`raise ModelLoadError` |
| model2vec encode 失敗（例如輸入為空字串） | 空字串回傳零向量。不拋例外 |
| 既有 embedding 的 BLOB 資料損毀 | 跳過該筆記錄，不參與比對。log warning |

### 9.3 降級策略總表

| 模組 | 失敗時降級行為 |
|------|---------------|
| `dedup.py` | model2vec 不可用 → **fail-fast**（raise `ModelLoadError`），不降級。模型為必要依賴，無模型即無法保證去重品質 |
| `search.py` | FTS5 不可用 → 降級為 LIKE '%query%'（基本的 SQL LIKE 查詢，無 BM25 排名） |
| `store.py` | SQLite write lock timeout（預設 5 秒）→ 回傳 `{status: "error", reason: "db_locked"}`，client 可稍後重試 |
| `audit.py` | audit.jsonl 寫入失敗 → log error，**不影響 `remagraph_store` 的成功回傳**。記憶已寫入 SQLite，audit 為輔助功能 |

---

## 10. 各模組完整介面簽名草圖

### 10.1 db.py

```python
"""SQLite 連線管理與 schema 初始化。

本模組負責：
- 展開 state 路徑 (~/.local/state/remagraph/)
- 建立 SQLite 連線（WAL 模式、SERIALIZED 隔離）
- Schema 初始化與 migration 編排
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# --- 常數 ---

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "remagraph"
DB_FILENAME = "remagraph.db"
SCHEMA_VERSION = 1


class MigrationError(RuntimeError):
    """Schema migration 失敗。"""


# --- 公開 API ---

def get_db_path(state_dir: Path | None = None) -> Path:
    """回傳 SQLite 資料庫的完整路徑。

    若 state_dir 為 None，使用 DEFAULT_STATE_DIR。
    若目錄不存在，自動建立（mode=0o700）。
    """
    ...


def connect(state_dir: Path | None = None) -> sqlite3.Connection:
    """建立 SQLite 連線並初始化。

    1. 展開路徑、建立目錄（若需要）
    2. 建立 sqlite3.Connection（WAL 模式、FK ON、SERIALIZED 隔離）
    3. 執行 schema 初始化（_init_schema）
    4. 執行 migration（_run_migrations）
    5. 回傳已就緒的連線

    Raises:
        OSError: 目錄無法建立（權限不足）
        MigrationError: Schema migration 失敗
        sqlite3.DatabaseError: 資料庫損毀
    """
    ...


def close(conn: sqlite3.Connection) -> None:
    """安全關閉 SQLite 連線。"""
    ...


# --- 內部函式（不直接暴露給其他模組） ---

def _init_schema(conn: sqlite3.Connection) -> None:
    """執行完整 DDL（見 §1.5）。

    所有語句使用 IF NOT EXISTS，確保冪等。
    包括：memories 主表、FTS5 虛擬表、triggers、indexes。
    """
    ...


def _run_migrations(conn: sqlite3.Connection) -> None:
    """檢查 _meta.schema_version 並執行 migration chain。

    目前只有 v1（初始版本），未來版本在此新增 migration 函式。
    """
    ...
```

### 10.2 store.py

```python
"""SQLite + FTS5 讀寫。

本模組負責：
- 記憶 ID 生成（mem-YYYYMMDD-NNN）
- 記憶的 INSERT / UPDATE（supersede / invalidate）
- 查詢（單筆、embedding 批次、最新 status）

注意：本模組不包含仲裁邏輯，也不自行管理 transaction。
所有寫入函式接受 conn 參數，由呼叫方控制 transaction 邊界。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import numpy as np

from remagraph.models import Memory, MemoryKind

# --- 自訂例外 ---

class MemoryIDGenerationError(RuntimeError):
    """記憶 ID 生成失敗（例如並發衝突超過重試次數）。"""


# --- 公開 API ---

def generate_memory_id(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> str:
    """生成唯一記憶 ID，格式 mem-YYYYMMDD-NNN。

    應在 transaction 內呼叫以保證並發安全。
    若發生 PRIMARY KEY 衝突，呼叫方應重試（最多 3 次）。

    Raises:
        MemoryIDGenerationError: 重試次數用盡仍無法生成唯一 ID
    """
    ...


def insert_memory(
    conn: sqlite3.Connection,
    memory: Memory,
    embedding: np.ndarray | None,
) -> str:
    """插入一筆記憶記錄。

    應在 transaction 內呼叫。FTS5 trigger 會自動同步。

    回傳 memory.id。
    """
    ...


def supersede_status_updates(
    conn: sqlite3.Connection,
    task_id: str,
) -> int:
    """將同 task_id 的所有 active status_update 標記為 superseded。

    應在 transaction 內、INSERT 新 status_update 之前呼叫。

    回傳被影響的筆數（可能為 0）。
    """
    ...


def invalidate_constraints(
    conn: sqlite3.Connection,
    invalidate_ids: list[str],
) -> int:
    """將指定的 discovered_constraint 記憶標記為 invalidated。

    應在 transaction 內、INSERT 新 discovered_constraint 之前呼叫。
    不驗證 id 是否存在或 kind 是否正確（由呼叫方預先驗證）。

    回傳被影響的筆數。
    """
    ...


def get_memory_by_id(
    conn: sqlite3.Connection,
    memory_id: str,
) -> Memory | None:
    """依 id 查詢單筆記憶。

    回傳 Memory 物件，若不存在回傳 None。
    不需在 transaction 內呼叫。
    """
    ...


def get_active_embeddings(
    conn: sqlite3.Connection,
    kind: MemoryKind,
) -> list[tuple[str, bytes]]:
    """載入同 kind、status='active' 的所有記憶的 (id, embedding)。

    供 dedup.py 做 cosine similarity 比對。
    只回傳 embedding IS NOT NULL 的記錄。

    回傳 [(memory_id, embedding_bytes), ...]。
    不需在 transaction 內呼叫。
    """
    ...


def get_latest_status_updates(
    conn: sqlite3.Connection,
    limit: int = 20,
) -> list[Memory]:
    """回傳所有 active status_update，以 task_id 去重取最新。

    供 remagraph_status MCP tool 使用。
    不需在 transaction 內呼叫。
    """
    ...


# --- 內部輔助函式 ---

def _row_to_memory(row: sqlite3.Row) -> Memory:
    """將 sqlite3.Row 轉換為 Memory Pydantic 物件。

    處理 JSON 欄位的反序列化（learnings、tags）。
    """
    ...
```

### 10.3 search.py

```python
"""BM25 全文檢索。

本模組負責：
- FTS5 query sanitization
- 動態 SQL 建構（BM25 + 多維度過濾）
- 結果排名與分頁

注意：本模組不修改資料，所有操作為唯讀。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from remagraph.models import Memory, MemoryKind, MemoryStatus


# --- 資料結構 ---

@dataclass
class SearchResult:
    """單筆搜尋結果。"""
    memory: Memory
    score: float          # BM25 rank（FTS5 bm25() 回傳值；較低 = 較相關）
    rank: int             # 排名（1-based）


@dataclass
class SearchResponse:
    """搜尋回傳。"""
    results: list[SearchResult]
    has_more: bool       # 是否還有更多符合條件的結果（v1 不提供精確 total）
    query: str           # sanitized 後的實際查詢字串


# --- 公開 API ---

def search(
    conn: sqlite3.Connection,
    *,
    query: str,
    top_k: int = 20,
    kind: MemoryKind | None = None,
    status: MemoryStatus | None = None,
    tags: list[str] | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
) -> SearchResponse:
    """FTS5 BM25 全文檢索 + 多維度過濾。

    參數：
        query:   全文檢索關鍵字。空白或僅含特殊字元時回傳空結果
        top_k:   回傳筆數上限（1–100）
        kind:    過濾記憶類型（None = 不過濾）
        status:  過濾生命週期狀態（None = 不過濾）
        tags:    過濾標籤，AND 邏輯（None = 不過濾）
        agent_id: 過濾 agent
        task_id: 過濾任務

    回傳：
        SearchResponse，含排名結果與 has_more 旗標。

    不需在 transaction 內呼叫。
    """
    ...


# --- 內部函式 ---

def sanitize_fts5_query(raw: str) -> str:
    """移除 FTS5 特殊字元，防止語法錯誤。

    保留純文字內容。空白或無法解析的輸入回傳空字串。
    """
    ...


def _build_search_sql(
    params: dict,
) -> tuple[str, list]:
    """根據參數動態建構 SQL 與參數列表。

    回傳 (sql_string, param_list)。
    """
    ...


def _row_to_search_result(
    row: sqlite3.Row,
    rank: int,
) -> SearchResult:
    """將查詢結果列轉換為 SearchResult。"""
    ...
```

### 10.4 dedup.py

```python
"""model2vec 語意去重。

本模組負責仲裁規則 #4：
- 將 summary 編碼為 model2vec embedding（potion-multilingual-128M，768 維）
- 與同 kind 的所有 active 記憶做 cosine similarity 比對（上限 2000 筆）
- 若最高相似度 ≥ 0.90（待校準）→ 拒絕寫入，回傳最相似的既有記憶 ID

注意：模型為延遲載入（首次呼叫時初始化）；載入失敗 → fail-fast (ModelLoadError)。
"""

from __future__ import annotations

import sqlite3

from remagraph.models import MemoryKind
from remagraph.arbitration import ArbitrationResult


# --- 常數 ---

MODEL_NAME = "potion-multilingual-128M"
SIMILARITY_THRESHOLD = 0.90  # v1 初始值，待中文資料集校準
MAX_TOKENS = 512
DEDUP_MAX_CANDIDATES = 2000   # 同 kind active 記憶比對上限


class ModelLoadError(RuntimeError):
    """model2vec 模型無法載入（例如網路不可用、模型名稱錯誤）。fail-fast，不降級。"""


# --- 公開 API ---

def check_duplicate(
    summary: str,
    kind: MemoryKind,
    conn: sqlite3.Connection,
) -> ArbitrationResult:
    """model2vec 語意去重（仲裁規則 #4）。

    1. 將 summary 編碼為 embedding
    2. 載入同 kind 所有 active embedding（上限 2000 筆）
    3. 計算 cosine similarity
    4. 若最高相似度 ≥ 0.90 → 回傳 ArbitrationResult(passed=False, ...)
       否則 → 回傳 ArbitrationResult(passed=True, ...)

    參數：
        summary: 新記憶的摘要文字
        kind:    記憶類型（僅與同 kind 比對）
        conn:    SQLite 連線

    回傳：
        ArbitrationResult：
        - passed=True：去重通過
        - passed=False, reason="duplicate_content"：
          closest_memory_id 與 closest_similarity 已填入

    Raises:
        ModelLoadError: model2vec 模型載入失敗（fail-fast，不降級）
    """
    ...


def encode_summary(summary: str) -> bytes:
    """將 summary 編碼為 model2vec embedding（potion-multilingual-128M，768 維）。

    取前 MAX_TOKENS token 做編碼。回傳 numpy bytes（float32 little-endian <f4），
    若模型不可用 → raise ModelLoadError（fail-fast）。

    此為公開函式，供 store.py 的 insert_memory 在寫入前呼叫以取得 embedding。
    """
    ...


# --- 內部函式 ---

def _get_model():
    """延遲載入 model2vec 模型（Singleton pattern）。

    首次呼叫時下載並載入 potion-multilingual-128M（~128MB）。
    載入失敗 → raise ModelLoadError。
    """
    ...


def _cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
    """計算兩個 numpy 向量的 cosine similarity。"""
    ...
```

---

## 11. Given/When/Then 驗收條件

### 11.1 store.py — 基本 CRUD

```
Given 全新空白資料庫
When generate_memory_id(conn, now="2026-07-21") 被呼叫
Then 回傳 "mem-20260721-001"

Given 資料庫中已有 mem-20260721-001, mem-20260721-002
When generate_memory_id(conn, now="2026-07-21") 被呼叫
Then 回傳 "mem-20260721-003"

Given 完整 Memory 物件與 embedding BLOB
When insert_memory(conn, memory, embedding) 被呼叫
Then 記錄寫入 memories 主表
And memories_fts 虛擬表中可查到對應 rowid 的內容
And embedding BLOB 可被讀回並正確反序列化為相同 numpy array

Given 資料庫中有 mem-001（task_id="task-A", kind="status_update", status="active"）
When supersede_status_updates(conn, "task-A") 被呼叫
Then mem-001 的 status 變為 "superseded"
And updated_at 更新為呼叫時間
And 回傳值 == 1

Given 資料庫中有 mem-001 和 mem-002（皆為 discovered_constraint, active）
When invalidate_constraints(conn, ["mem-001", "mem-002"]) 被呼叫
Then 兩筆的 status 皆變為 "invalidated"
And 回傳值 == 2
```

### 11.2 search.py — BM25 全文檢索

```
Given 資料庫中有三筆記憶：
  mem-001: summary="subagent 委派時的 acpx 連線錯誤", kind="task_handoff", tags=["acpx", "bug"]
  mem-002: summary="OPENCODE_CONFIG 不是最終權威", kind="discovered_constraint", tags=["config"]
  mem-003: summary="status update for task", kind="status_update", tags=[]
When search(conn, query="acpx 連線", top_k=5) 被呼叫
Then 回傳結果包含 mem-001
And mem-001 排在第一位（BM25 score 最高）
And has_more == False

Given 同上資料
When search(conn, query="acpx", kind="task_handoff") 被呼叫
Then 回傳結果僅包含 mem-001（kind 過濾）
And len(results) == 1

Given 同上資料
When search(conn, query="acpx", tags=["bug"]) 被呼叫
Then 回傳結果包含 mem-001
And len(results) == 1

Given 同上資料
When search(conn, query="nonexistent_xyz", top_k=5) 被呼叫
Then 回傳空結果（results=[]，has_more=False），無錯誤

Given 空字串 query
When search(conn, query="") 被呼叫
Then 回傳空結果，無錯誤。sanitize_fts5_query("") 回傳空字串，SQL MATCH '' 不拋例外
```

### 11.3 dedup.py — 語意去重

```
Given 資料庫中有一筆 active task_handoff，summary="嘗試修復 subagent 委派時的 acpx 連線錯誤"
When check_duplicate(summary="subagent 委派 + deny-all 時的 acpx 連線錯誤", kind="task_handoff", conn) 被呼叫
Then 若兩者語意高度相似（cosine ≥ 0.90）→ 回傳 passed=False, reason="duplicate_content"
And closest_memory_id 指向既有記憶的 id
And closest_similarity ≥ 0.90

Given 同上既有記憶
When check_duplicate(summary="今天是好天氣適合寫程式", kind="task_handoff", conn) 被呼叫
Then 回傳 passed=True（語意不相關）

Given 資料庫中有一筆 active status_update
And 新請求 kind="task_handoff", summary 與既有 status_update 語意相似
When check_duplicate(...) 被呼叫
Then 回傳 passed=True（跨 kind 不比對）

Given 資料庫中無任何 active 記憶（首筆寫入）
When check_duplicate(任何 summary, kind, conn) 被呼叫
Then 回傳 passed=True（無可比對對象）

Given model2vec 模型不存在或無法載入
When check_duplicate(...) 被呼叫
Then raise ModelLoadError（fail-fast，不降級）
```

### 11.4 db.py — Migration

```
Given 全新空白資料庫（無 _meta 表）
When connect() 被呼叫
Then _meta 表中 schema_version 設為 "1"
And 完整 DDL 已執行（memories, memories_fts, triggers, indexes）

Given 資料庫 schema_version 為 "1"，目前程式碼 schema_version 也是 "1"
When connect() 被呼叫
Then 無任何 DDL 或 migration 執行（當前版本）

Given 資料庫 schema_version 為 "1"，目前程式碼 schema_version 為 "2"
When connect() 被呼叫
Then 依序執行 v1→v2 的 migration
And _meta.schema_version 更新為 "2"

Given migration 執行到一半失敗（例如 DISK FULL）
When connect() 被呼叫
Then 整個 migration transaction ROLLBACK
And raise MigrationError
And 資料庫保持在 migration 前的狀態（schema_version 仍為 "1"）
```

### 11.5 併發行為

```
Given 兩個 MCP request 同時對同一 task_id 寫 status_update
When 兩個 request 都通過仲裁並寫入
Then SQLite WAL 序列化寫入，各自產生一筆 active status_update
And 各自將前手 supersede（或各自保留 active 若為首筆）
And 無資料損毀、無 deadlock

Given remagraph_store 寫入中 process crash
When transaction 未 commit
Then SQLite 自動 ROLLBACK
And audit.jsonl 無該筆記錄
And 資料庫保持 crash 前的一致狀態
```

---

## 12. PPLX 審查裁決（已定案）

以下為 PPLX 設計審查（2026-07-21）之裁決結果，已寫入本文件對應章節：

| ID | 原開放問題 | 裁決 | 落地章節 |
|----|-----------|------|----------|
| D02-Q1 | FTS5 CJK 分詞（unicode61 bigram 描述錯誤） | 改用 **`tokenize='trigram'`**。需 SQLite ≥ 3.34；降級方案為手動 bigram 前處理（文件層級，v1 不實作自動降級） | §1.2 |
| D02-Q2 | `status_update` 跨 task_id supersede | **嚴格同 task_id**，v1 不跨 task。`task_id` 為自由格式，未來可嵌入階層語意 | §4（D01 既有設計確認） |
| D02-Q3 | `remagraph_search` top_k 預設值 | 預設 **20**、最大 **100** | §5.2 |
| D02-Q4 | dedup 效能邊界 | 同 kind active ≤ 2000 全量；超過取最新 2000（`created_at DESC`）。v1 不做 ANN | §6.5 |
| D02-Q5 | embedding BLOB endianness | **`float32` little-endian `<f4`**。`np.float32` 在所有主流平台（x86/ARM）都是 IEEE 754 little-endian，跨架構一致 | §7.1 |
| D02-Q6 | audit.jsonl rotation 策略 | **DEFER v2**。v1 不實作 log rotation | §2.1（既有設計確認） |

### 其他跨文件裁決寫入本文件者

| ID | 裁決 | 落地章節 |
|----|------|----------|
| B1 | 去重模型：`potion-multilingual-128M`；載入 fail-fast；門檻 0.90（待校準）；2000 筆上限 | §6、§7、§9、§10.4 |
| C3 | `remagraph_status` limit 預設 20、最大 100 | §4.2 |
| C5 | 補 `memories_au` AFTER UPDATE trigger（已定案） | §1.3 |
| C6 | DDL 補 `timestamp` 欄位（語意與 `created_at` 區分） | §1.1 |
| C8 | StoreResponse 可含 `superseded` / `invalidated_count` | §3.3（D01） |
| — | query="" → 空 results + warning，不拋錯 | §9.2 |
| — | FTS query 必須 sanitize | §5.4（既有設計確認） |
| — | model2vec stdio lazy load（與 B3 stdio 為主一致） | §6.6 |
| — | 去重僅同 kind 比對；v1 不跨 task supersede；不做雙向 invalidate 追溯 | §6.7、§4（D01） |
| — | v1 單 process（PID 鎖），不支援多實例共用 DB | §8 |
| — | search 回應用 `has_more` 取代精確 `total_matches`（v1） | §5.5 |
| — | N4 ts 精度：記憶 timestamp 到秒 vs audit 到毫秒 | §1.1（D01） |
| — | TDD：先 `test_models.py`；mutmut 限縮 arbitration+dedup；MCP SDK 依賴 `mcp` | 設計層註記 |

---

## 13. 與 DESIGN.md 及 D01 對齊聲明

本文件所有設計決策的來源皆來自 `/Users/aikenlin/Projects/RemaGraph/DESIGN.md` 及 `docs/design/01-data-model-arbitration.md`。以下為關鍵對齊點：

| 來源 | 章節 | 本文件對應 |
|------|------|-----------|
| DESIGN.md §儲存層 | SQLite + FTS5 Schema | §1：完整評審、指出缺失（`timestamp`、`memories_au` trigger、`idx_memories_dedup`）並提供修正後 DDL |
| DESIGN.md §儲存層 | embedding 欄位策略（v1 只存不查） | §7：展開 v1 策略與 v2 升級路徑的具體步驟 |
| DESIGN.md §儲存層 | 查詢範例（BM25 SQL） | §5：完整展開為動態 SQL 建構邏輯，含 FTS5 sanitization |
| DESIGN.md §部署形態 | state 目錄 `~/.local/state/remagraph/`、單一 SQLite | §2：補充權限設定、目錄初始化、migration 策略 |
| D01 §7 | `arbitration.py` 介面（`check_duplicate`、`ArbitrationResult`） | §6、§10.4：與 D01 定義的 `ArbitrationResult` 回傳格式一致；`dedup.py` 為 `check_duplicate` 的唯一實作位置 |
| D01 §3 | `status_update` supersede（同 task_id、單一 transaction） | §4.2：`supersede_status_updates()` 接受 `conn` 參數，由呼叫方控制 transaction 邊界 |
| D01 §4 | `discovered_constraint` invalidates（驗證由 arbitration 負責、store 只做 UPDATE） | §4.2：`invalidate_constraints()` 不做驗證，職責分離 |
| D01 §6 | Lazy Registration（v1 不做 `agents` 表） | 本文件不引入 `agents` 表，與 D01 一致 |
| DESIGN.md §對外邊界 | 不耦合任何特定外部系統 | 本文件無任何外部具名專案詞彙，已驗證 |

本文件新增的設計（`db.py` 連線管理、migration 策略、`store.py`/`search.py`/`dedup.py` 完整 API、併發與失敗模式、FTS5 sanitization、embedding v2 路徑）**不違反** DESIGN.md 或 D01 中任何既有決策。本文件對 DESIGN.md 提出的兩個 schema 修正建議（補 `timestamp` 欄位、補 `memories_au` trigger）為**向下相容**的補充，不影響既有設計意圖。

---

## DONE

- [x] SQLite schema 評審與修正建議（`timestamp`、`memories_au` trigger、`idx_memories_dedup`、完整 DDL）
- [x] State 路徑 `~/.local/state/remagraph/`、權限 `0700`/`0600`、XDG 合規說明
- [x] Migration 策略：`_meta` 表版本追蹤、冪等 DDL、v1→v2 升級路徑
- [x] `db.py` 公開 API：`get_db_path()`、`connect()`、`close()`、`_init_schema()`、`_run_migrations()`
- [x] `store.py` 公開 API：`generate_memory_id()`、`insert_memory()`、`supersede_status_updates()`、`invalidate_constraints()`、`get_memory_by_id()`、`get_active_embeddings()`、`get_latest_status_updates()`
- [x] `search.py` 公開 API：`search()`（BM25 + trigram tokenizer + kind/tags/status/agent_id/task_id 過濾 + top_k）、`sanitize_fts5_query()`、`SearchResult` / `SearchResponse` dataclass（`has_more` 取代 `total`）
- [x] `dedup.py` 公開 API：`check_duplicate()`（potion-multilingual-128M、cosine ≥ 0.90、同 kind 比對、fail-fast、2000 筆上限）、`encode_summary()`
- [x] Embedding BLOB：v1 float32 little-endian `<f4`、v2 sqlite-vec 升級路徑（含升級步驟、hybrid search 設計）
- [x] 併發與 Locking：WAL 模式、asyncio 單執行緒模型、transaction 邊界、audit.jsonl 原子寫入
- [x] 失敗模式：錯誤分類、各模組的失敗處理（dedup fail-fast）、降級策略總表
- [x] 完整介面簽名草圖（`db.py` / `store.py` / `search.py` / `dedup.py`，含 docstring 與型別標注）
- [x] Given/When/Then 驗收條件（5 組場景：store CRUD、search BM25、dedup 去重、migration、併發）
- [x] PPLX 審查裁決已定案（§12）
- [x] 與 DESIGN.md 及 D01 對齊聲明
- [x] 驗證：全文無外部具名專案詞彙

---

## PPLX-CONSENSUS-APPLIED

以下為本次 PPLX 審查（2026-07-21）在本文件之落地 checklist：

- [x] B2：FTS5 使用 `tokenize='trigram'`（§1.2、§1.5）；修正 unicode61「bigram」錯誤描述（§1.2）
- [x] B2：trigram 需 SQLite ≥ 3.34；降級方案記載（§1.2）
- [x] B1：模型 `potion-base-8M` → `potion-multilingual-128M`（§6.1、§6.2、§6.6、§7.1、§9.2、§10.4）
- [x] B1：去重門檻 0.92 → 0.90，標「待中文資料集校準」（§6.1、§6.3、§10.4、§11.3）
- [x] B1：模型載入 fail-fast（§6.1、§6.2、§7.1、§9.1、§9.2、§9.3、§11.3）
- [x] B1：去重比對上限 2000 筆（§6.1、§6.3、§6.5、§10.4）
- [x] B1：embedding 格式 float32 little-endian `<f4`（§6.2、§6.3、§7.1）
- [x] C5：`memories_au` AFTER UPDATE trigger 入正文，標「已裁決」（§1.3）
- [x] C6：`timestamp` 欄位入正文，標「已裁決」（§1.1）
- [x] C3：`remagraph_status` limit 預設 20（§4.2）
- [x] C4：`remagraph_search` top_k 預設 20、最大 100（§5.2）
- [x] SearchResponse：`has_more` 取代 `total_matches`（§5.5、§10.3、§11.2）
- [x] v2 升級路徑：embedding 維度 256 → 768（§7.2）
- [x] §12：開放問題 → 已裁決狀態表
- [x] 全文無 `potion-base-8M` 作為 v1 選定模型
- [x] 全文無與 PPLX 共識衝突的舊建議（含降級策略、門檻值）
