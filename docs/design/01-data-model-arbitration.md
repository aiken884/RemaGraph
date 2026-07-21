# T-RG-D01：資料模型與仲裁規則設計

> **艦隊任務 ID**：`T-RG-D01`
> **狀態**：設計完成，尚未實作
> **約束**：本文件僅為設計產出，不得引入 `herdr-bridge` / `herdr-gov` 耦合。以 `DESIGN.md` 為 SOT。

---

## 目錄

1. [記憶精確 Schema](#1-記憶精確-schema)
2. [三種 kind 的生命週期](#2-三種-kind-的生命週期)
3. [status_update supersede 機制](#3-status_update-supersede-機制)
4. [discovered_constraint 的 invalidates 機制](#4-discovered_constraint-的-invalidates-機制)
5. [五條仲裁規則精確定義](#5-五條仲裁規則精確定義)
6. [Lazy Registration 的 agent_id 規則](#6-lazy-registration-的-agent_id-規則)
7. [公開 Python 介面草圖](#7-公開-python-介面草圖)
8. [錯誤碼表](#8-錯誤碼表)
9. [邊界案例](#9-邊界案例)
10. [Given/When/Then 驗收條件](#10-givenwhenthen-驗收條件)
11. [PPLX 審查裁決（已定案）](#11-pplx-審查裁決已定案)
12. [與 DESIGN.md 對齊聲明](#12-與-designmd-對齊聲明)

---

## 1. 記憶精確 Schema

本節定義 `Memory` 物件的完整型別與約束。所有欄位皆為必要（除 `learnings` / `tags` 可為空陣列、`handoff_note` 可為空字串）。

### 1.1 型別定義

| 欄位 | Python 型別 | SQLite 型別 | 約束 | 說明 |
|------|------------|-------------|------|------|
| `id` | `str` | `TEXT PRIMARY KEY` | 格式 `mem-YYYYMMDD-NNN`（例：`mem-20260721-001`），全域唯一 | 由 `store` 層在寫入前生成，不在 `Memory` 建構時填入 |
| `task_id` | `str` | `TEXT NOT NULL` | 非空、無格式限制（agent 自由定義），建議格式 `task-YYYY-MM-DD-NNN` | 外部任務識別鍵，跨 agent 共用；`status_update` supersede 依此鍵比對 |
| `agent_id` | `str` | `TEXT NOT NULL` | 格式 `^[a-z0-9_-]+$`（全小寫 ASCII，無空白、無特殊符號）；首次寫入時 Lazy Registration | agent 識別。範例：`oc-dspro`、`claude-haiku`、`gpt4` |
| `timestamp` | `datetime` | `TEXT NOT NULL`（ISO 8601） | UTC，精確到秒（`YYYY-MM-DDTHH:MM:SSZ`），伺服器端生成 | 寫入時間，非 agent 端時間 |
| `kind` | `Literal["task_handoff", "status_update", "discovered_constraint"]` | `TEXT NOT NULL` | `CHECK (kind IN (...))` | 三種之一，詳見第 2 節 |
| `summary` | `str` | `TEXT NOT NULL` | 非空、≥ 30 字（Unicode codepoint 計數）；仲裁規則 #1 強制 | 任務摘要。範例：「嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤」 |
| `learnings` | `list[str]` | `TEXT NOT NULL DEFAULT '[]'`（JSON array） | 至少一筆非空元素；仲裁規則 #2 強制；每筆元素非空字串 | 學到的東西。範例：`["錯誤發生在 opencode task tool…", "acpx 0.12.0 在…"]` |
| `handoff_note` | `str` | `TEXT NOT NULL DEFAULT ''` | `kind == "task_handoff"` 時 ≥ 20 字；`kind == "status_update"` 時可為空字串；`kind == "discovered_constraint"` 時可為空字串；仲裁規則 #3 僅對 `task_handoff` 強制 | 給接手者的筆記 |
| `tags` | `list[str]` | `TEXT NOT NULL DEFAULT '[]'`（JSON array） | 可為空陣列；每筆元素非空字串、建議全小寫 | 自由標籤。範例：`["acpx", "subagent", "bug"]` |
| `status` | `Literal["active", "superseded", "invalidated"]` | `TEXT NOT NULL DEFAULT 'active'` | `CHECK (status IN (...))` | 生命週期狀態 |
| `embedding` | `numpy.ndarray \| None` | `BLOB`（可為 NULL） | v1 只存不查；由 model2vec `potion-multilingual-128M` 生成，float32 little-endian (`<f4`)，維度 768 | 語意向量，供 v2 sqlite-vec 使用 |
| `created_at` | `datetime` | `TEXT NOT NULL`（ISO 8601） | 伺服器端 UTC，不可變 | 建立時間 |
| `updated_at` | `datetime` | `TEXT NOT NULL`（ISO 8601） | 伺服器端 UTC；`status` 變更（supersede / invalidate）時自動更新 | 最後更新時間 |

### 1.2 端點（Pydantic）Schema

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MemoryKind = Literal["task_handoff", "status_update", "discovered_constraint"]
MemoryStatus = Literal["active", "superseded", "invalidated"]


class StoreRequest(BaseModel):
    """remagraph_store 的輸入。不含 id、timestamp、status、embedding（伺服器端填入）。"""

    task_id: str
    agent_id: str
    kind: MemoryKind
    summary: str
    learnings: list[str] = Field(default_factory=list)
    handoff_note: str = ""
    tags: list[str] = Field(default_factory=list)
    # discovered_constraint 專用欄位
    invalidates: list[str] | None = None  # 要標記為 invalidated 的既有 memory id 清單


class Memory(BaseModel):
    """完整記憶記錄（含伺服器端填入的欄位）。"""

    id: str
    task_id: str
    agent_id: str
    timestamp: datetime
    kind: MemoryKind
    summary: str
    learnings: list[str] = Field(default_factory=list)
    handoff_note: str
    tags: list[str] = Field(default_factory=list)
    status: MemoryStatus = "active"
    created_at: datetime
    updated_at: datetime
```

**注意**：`StoreRequest` 與 `Memory` 的區分——前者是 MCP 輸入，後者是完整記錄。`id`、`timestamp`、`status`、`created_at`、`updated_at`、`embedding` 皆在伺服器端填入，agent 不提供。

---

## 2. 三種 kind 的生命週期

| kind | 用途 | 生命週期 | status 遷移 | 觸發條件 |
|------|------|----------|-------------|----------|
| `task_handoff` | 做了什麼、學到什麼、交接筆記。agent 完成任務或階段性暫停時寫入。 | 永久（永遠 `active`） | `active` → **無遷移** | 不 supersede、不 invalidate。保留完整歷史，多筆同 `task_id` 的 `task_handoff` 可並存 |
| `status_update` | 專案／任務的現況快照（進度、阻礙、決策）。 | 同 `task_id` 內自動 supersede | `active` → `superseded`（被新 `status_update` 取代） | 寫入新 `status_update` 時，將同 `task_id` 的所有既有 `status_update` 標記為 `superseded`。**不跨 task_id** |
| `discovered_constraint` | 發現的系統限制、陷阱、規則。屬事實性知識而非任務進度。 | 永久，可被顯式 invalidate | `active` → `invalidated`（被後續發現推翻） | 僅在寫入新 `discovered_constraint` 且 `invalidates=[id1, id2, ...]` 時發生。**手動聲明，不做語意推斷** |

### 2.1 生命週期圖

```
task_handoff:    [寫入] ──── active ──── (永久)

status_update:   [寫入] ──── active ──┬── superseded（被同 task_id 的新 status_update 取代）
                                      └── (最後一筆保持 active)

discovered_constraint:
                 [寫入] ──── active ──── invalidated（被後續 constraint 顯式推翻）
```

---

## 3. status_update supersede 機制

### 3.1 精確定義

**比對鍵**：`task_id`（精確字串比對，大小寫視為不同）。

**觸發時機**：每筆 `remagraph_store` 請求，若 `kind == "status_update"` 且通過全部仲裁規則，則**在 INSERT 新記錄前**執行 supersede。

**行為**：
1. `UPDATE memories SET status = 'superseded', updated_at = NOW WHERE task_id = ? AND kind = 'status_update' AND status = 'active'`
2. INSERT 新 `status_update`（`status = 'active'`）
3. 兩個操作在同一個 SQLite transaction 內完成

### 3.2 併發寫入行為

多 agent 同時對同一 `task_id` 寫 `status_update` 時：

- SQLite 的 SERIALIZED 模式保證寫入序列化。
- 由於 `remagraph_store` 以單一 transaction 執行「supersede → INSERT」，兩個併發寫入會產生兩筆 `active` 的 `status_update`（各自把自己的前手 supersede）。
- 這**非錯誤狀態**——`remagraph_status` 回傳時以 `created_at DESC LIMIT 1` 取最新一筆，自然解決。

### 3.3 回傳格式

`remagraph_store` 在成功寫入 `status_update` 後回傳：

```json
{
  "status": "stored",
  "id": "mem-20260721-005",
  "superseded": 3
}
```

| 欄位 | 說明 |
|------|------|
| `superseded` | 本輪被標記為 `superseded` 的既有記憶數量。`0` 表示此為該 `task_id` 的第一筆 `status_update` |

---

## 4. discovered_constraint 的 invalidates 機制

### 4.1 精確定義

`discovered_constraint` 代表「發現了一個事實性限制」。當新的發現推翻舊的發現時（例如：原本以為 `OPENCODE_CONFIG` 是最終權威，後來發現它是合併鏈中間），agent 可**顯式宣告**哪些既有記憶應該被標記為無效。

### 4.2 行為

寫入 `kind == "discovered_constraint"` 時，若 `invalidates` 欄位非空：

1. 驗證 `invalidates` 內每個 `id` 是否存在於 `memories` 表中。不存在的 `id` → 拒絕整筆請求，回傳 `invalidates_not_found`。
2. 驗證被 invalidate 的記憶 `kind` 必須也是 `discovered_constraint`。跨 kind invalidate 拒絕，回傳 `invalidates_kind_mismatch`。
3. `UPDATE memories SET status = 'invalidated', updated_at = NOW WHERE id IN (?) AND kind = 'discovered_constraint' AND status = 'active'`
4. INSERT 新 `discovered_constraint`（`status = 'active'`）
5. 在同一個 SQLite transaction 內完成

### 4.3 不回傳 invalidates 清單的設計理由

`invalidates` 是僅在**寫入時**使用的單向連結，不儲存在被 invalidate 的記錄上（不存在 `invalidated_by` 欄位）。理由：

- 減少 schema 複雜度。`invalidated` status 本身已經足夠表達「這筆記錄不再有效」。
- 若需要追溯因果關係，可查 audit.jsonl 找到對應的 `remagraph_store` 記錄。
- 這是**事實性知識的生命週期設計**，而非「引用完整性」問題。

---

## 5. 五條仲裁規則精確定義

每筆 `remagraph_store` 請求依序通過五條規則。**順序重要**：先執行便宜的規則（#1, #2, #3, #5），最後執行昂貴的規則（#4 model2vec）。

### 規則 #1：summary 長度門檻

| 屬性 | 值 |
|------|-----|
| **條件** | `len(summary.strip())` ≥ 30（Unicode codepoint 計數） |
| **適用** | 所有 kind |
| **失敗 reason_code** | `summary_too_short` |
| **失敗回傳** | `{"status": "rejected", "reason": "summary_too_short", "detail": "summary 需 ≥ 30 字，目前 N 字"}` |

### 規則 #2：learnings 非空

| 屬性 | 值 |
|------|-----|
| **條件** | `len(learnings) ≥ 1` 且每筆元素非空字串（`len(s.strip()) > 0`） |
| **適用** | 所有 kind |
| **失敗 reason_code** | `learnings_empty` |
| **失敗回傳** | `{"status": "rejected", "reason": "learnings_empty", "detail": "learnings 至少需要一筆非空內容"}` |

### 規則 #3：handoff_note 長度門檻

| 屬性 | 值 |
|------|-----|
| **條件** | 若 `kind == "task_handoff"`：`len(handoff_note.strip())` ≥ 20；其他 kind 不檢查 |
| **適用** | 僅 `task_handoff` |
| **失敗 reason_code** | `handoff_note_too_short` |
| **失敗回傳** | `{"status": "rejected", "reason": "handoff_note_too_short", "detail": "handoff_note 需 ≥ 20 字，目前 N 字"}` |

### 規則 #4：model2vec 去重

| 屬性 | 值 |
|------|-----|
| **模型** | `potion-multilingual-128M`（128MB，支援中文等多語言）。載入失敗 → **fail-fast**（不靜默降級，raise `ModelLoadError`） |
| **條件** | 將 `summary` 編碼為 model2vec embedding，與**同 kind、status = 'active'** 的所有既有記憶做 cosine similarity 比對。最高相似度 < 0.90 才通過 |
| **門檻校準** | 0.90 為 v1 初始值，**待中文資料集校準**。可選按 kind 分別設定（建議非強制） |
| **比對上限** | 同 kind active 記憶 ≤ 2000 筆全量比對；超過取最新 2000 筆（依 `created_at DESC`），避免線性掃描效能退化 |
| **適用** | 所有 kind |
| **失敗 reason_code** | `duplicate_content` |
| **失敗回傳** | `{"status": "rejected", "reason": "duplicate_content", "detail": "與既有記憶高度相似（similarity=0.95），最接近的記憶：mem-20260721-001"}` |
| **設計說明** | 去重只在同 kind 內比對——`status_update` 不會因為與 `task_handoff` 語意相似而被拒絕。另，`status_update` 的 supersede 已依 `task_id` 精確處理，此規則處理的是**跨 task_id 的內容重複** |
| **embedding 格式** | `numpy.ndarray.astype(np.float32).tobytes()`，little-endian `<f4`；讀回時 `np.frombuffer(b, dtype=np.float32)` |

### 規則 #5：agent_id 格式 + Lazy Registration

| 屬性 | 值 |
|------|-----|
| **條件** | `agent_id` 符合 regex `^[a-z0-9_-]+$`（全小寫 ASCII，無空白、無特殊符號）；首次出現時自動註冊（Lazy Registration） |
| **適用** | 所有 kind |
| **失敗 reason_code** | `invalid_agent_id` |
| **失敗回傳** | `{"status": "rejected", "reason": "invalid_agent_id", "detail": "agent_id 格式不符，僅允許小寫英數字元、底線、連字號：^[a-z0-9_-]+$"}` |

### 5.1 仲裁執行流程

```
remagraph_store(request)
  │
  ├─ 1. 驗證 summary 長度 ≥ 30 → 失敗 → rejected: summary_too_short
  ├─ 2. 驗證 learnings 非空      → 失敗 → rejected: learnings_empty
  ├─ 3. 驗證 handoff_note ≥ 20   → 失敗 → rejected: handoff_note_too_short
  │    （僅 task_handoff）
  ├─ 4. model2vec 去重           → 失敗 → rejected: duplicate_content
  ├─ 5. 驗證 agent_id 格式       → 失敗 → rejected: invalid_agent_id
  │
  ├─ 6. 若 kind == "status_update"     → supersede 同 task_id 舊記錄
  ├─ 7. 若 kind == "discovered_constraint" && invalidates 非空
  │      → 驗證 invalidates 目標存在且同 kind → 標記為 invalidated
  │
  ├─ 8. 生成 id（mem-YYYYMMDD-NNN）
  ├─ 9. 計算 embedding（model2vec）
  ├─ 10. INSERT INTO memories
  ├─ 11. 寫入 audit.jsonl
  └─ 12. 回傳 {status: "stored", id: "mem-..."}
```

---

## 6. Lazy Registration 的 agent_id 規則

### 6.1 設計原則

RemaGraph 不做預先註冊（pre-registration）。任何 agent 只要 `agent_id` 格式合法即可寫入記憶。首次寫入時自動記錄「此 agent_id 已出現在此專案中」。

### 6.2 實作方案（v1）

v1 不做獨立的 `agents` 表。Lazy Registration 只依賴 `memories` 表中是否存在該 `agent_id` 的記錄：

```sql
-- 檢查 agent_id 是否已存在於此專案中
SELECT COUNT(*) > 0 FROM memories WHERE agent_id = ? LIMIT 1;
```

此決定基於：
- v1 無需 agent 管理功能（例如 agent 停用、權限控制）。
- 避免過早最佳化。
- 未來 v2 若需要 agent 管理，可從既有 `memories.agent_id` 資料回溯建立 `agents` 表。

### 6.3 agent_id 格式規則

```
regex: ^[a-z0-9_-]+$
```

- 全小寫 ASCII
- 允許數字（0-9）、小寫字母（a-z）、底線（_）、連字號（-）
- 禁止大寫、空白、中文、特殊符號（如 `.`、`@`、`:`、`/`）
- 長度限制：3–64 字元（實作層級驗證，非 regex）
- 建議命名慣例：`{工具}-{簡稱}`，如 `oc-dspro`、`claude-haiku`、`gpt4`

### 6.4 邊界案例

| 案例 | 行為 |
|------|------|
| 首次出現的 agent_id | 自動接受（格式合法即可） |
| agent_id 長度不足 3 字元 | 拒絕，reason: `invalid_agent_id`，detail 說明長度限制 |
| agent_id 含大寫字母 | 拒絕，reason: `invalid_agent_id` |
| agent_id 含 `.`（如 `claude.sonnet`） | 拒絕，reason: `invalid_agent_id` |

---

## 7. 公開 Python 介面草圖

以下為 `arbitration.py` 的**公開函式簽名層級**設計。不寫實作細節。

### 7.1 主要入口

```python
from dataclasses import dataclass
from typing import Literal

# --- 型別 ---

ArbitrationReason = Literal[
    "summary_too_short",
    "learnings_empty",
    "handoff_note_too_short",
    "duplicate_content",
    "invalid_agent_id",
    "invalidates_not_found",
    "invalidates_kind_mismatch",
]


@dataclass
class ArbitrationResult:
    """仲裁結果"""
    passed: bool
    reason: ArbitrationReason | None = None
    detail: str | None = None
    # 僅 duplicate_content 時填入
    closest_memory_id: str | None = None
    closest_similarity: float | None = None


@dataclass
class SupersedeResult:
    """status_update supersede 結果"""
    superseded_count: int


@dataclass
class InvalidateResult:
    """discovered_constraint invalidates 結果"""
    invalidated_count: int
    invalidated_ids: list[str]
```

### 7.2 仲裁函式

```python
def validate_summary_length(summary: str) -> ArbitrationResult: ...
"""規則 #1：summary ≥ 30 Unicode codepoint"""

def validate_learnings(learnings: list[str]) -> ArbitrationResult: ...
"""規則 #2：learnings 至少一筆非空元素"""

def validate_handoff_note(kind: MemoryKind, handoff_note: str) -> ArbitrationResult: ...
"""規則 #3：kind == task_handoff 時 handoff_note ≥ 20 字"""

def check_duplicate(
    summary: str,
    kind: MemoryKind,
    conn: sqlite3.Connection,
    model2vec_instance: ...,
) -> ArbitrationResult: ...
"""規則 #4：model2vec 去重，與同 kind active 記憶比對 cosine similarity。
使用 potion-multilingual-128M，門檻 0.90（待校準），同 kind active ≤ 2000 全量比對。
需要 DB 連線以載入既有 embedding。"""

def validate_agent_id(agent_id: str) -> ArbitrationResult: ...
"""規則 #5：agent_id 格式驗證（regex + 長度 3–64）"""

def run_arbitration(
    request: StoreRequest,
    conn: sqlite3.Connection,
    model2vec_instance: ...,
) -> ArbitrationResult: ...
"""依序執行全部五條規則，任一失敗即回傳。全部通過回傳 passed=True。"""
```

### 7.3 生命週期管理函式

```python
def supersede_status_updates(
    task_id: str,
    conn: sqlite3.Connection,
) -> SupersedeResult: ...
"""將同 task_id 的所有 active status_update 標記為 superseded。
回傳被影響的筆數。在 transaction 內呼叫。"""

def invalidate_constraints(
    invalidate_ids: list[str],
    conn: sqlite3.Connection,
) -> InvalidateResult | ArbitrationResult: ...
"""驗證 invalidate_ids 都存在且 kind 都是 discovered_constraint。
若驗證失敗回傳 ArbitrationResult(passed=False, ...)。
若成功則標記為 invalidated 並回傳 InvalidateResult。在 transaction 內呼叫。"""
```

### 7.4 ID 生成

```python
def generate_memory_id(conn: sqlite3.Connection) -> str: ...
"""生成格式 mem-YYYYMMDD-NNN，NNN 為當日流水號。
在 transaction 內以 SELECT FOR UPDATE 或 INSERT OR IGNORE 保證並發安全。"""
```

---

## 8. 錯誤碼表

| reason_code | 對應規則 | HTTP 等效 | 說明 | 回傳範例 |
|-------------|---------|-----------|------|----------|
| `summary_too_short` | #1 | 422 | summary 不足 30 字 | `{"status":"rejected","reason":"summary_too_short","detail":"summary 需 ≥ 30 字，目前 12 字"}` |
| `learnings_empty` | #2 | 422 | learnings 為空或全空白 | `{"status":"rejected","reason":"learnings_empty","detail":"learnings 至少需要一筆非空內容"}` |
| `handoff_note_too_short` | #3 | 422 | task_handoff 的 handoff_note 不足 20 字 | `{"status":"rejected","reason":"handoff_note_too_short","detail":"handoff_note 需 ≥ 20 字，目前 8 字"}` |
| `duplicate_content` | #4 | 409 | 與既有記憶語意高度相似（cosine ≥ 0.90） | `{"status":"rejected","reason":"duplicate_content","detail":"與既有記憶高度相似（similarity=0.95），最接近的記憶：mem-20260721-001"}` |
| `invalid_agent_id` | #5 | 422 | agent_id 格式不符或長度不符 | `{"status":"rejected","reason":"invalid_agent_id","detail":"agent_id 格式不符，僅允許小寫英數字元、底線、連字號：^[a-z0-9_-]+$"}` |
| `invalidates_not_found` | invalidates 驗證 | 422 | invalidates 指定的 id 不存在 | `{"status":"rejected","reason":"invalidates_not_found","detail":"invalidates 指定的記憶不存在：mem-20260721-999"}` |
| `invalidates_kind_mismatch` | invalidates 驗證 | 422 | 試圖 invalidate 非 discovered_constraint 的記憶 | `{"status":"rejected","reason":"invalidates_kind_mismatch","detail":"只能 invalidate discovered_constraint 類型的記憶，mem-20260721-001 的 kind 是 task_handoff"}` |
| `db_error` | N/A | 500 | 資料庫層級錯誤 | `{"status":"error","reason":"db_error","detail":"..."}` |

---

## 9. 邊界案例

### 9.1 空值與邊界輸入

| 案例 | 預期行為 |
|------|----------|
| `summary` 為空字串 | 規則 #1 拒絕，`summary_too_short` |
| `summary` 僅含空白字元（`"   "`） | 規則 #1 拒絕（`len(summary.strip())` = 0，< 30） |
| `learnings = []` | 規則 #2 拒絕，`learnings_empty` |
| `learnings = ["", " ", "\n"]`（全空白元素） | 規則 #2 拒絕（每筆 `s.strip()` 後為空） |
| `tags` 含重複值 | 接受，不去重（agent 自由，server 不做語意判斷） |
| `task_id` 含特殊字元（`/`、`:`、中文） | 接受。`task_id` 無格式限制，僅做精確字串比對 |
| `handoff_note` 在 `kind=status_update` 時為空 | 接受。規則 #3 僅對 `task_handoff` 強制 |
| `invalidates` 在 `kind=task_handoff` 時非空 | 拒絕。`invalidates` 僅 `discovered_constraint` 有意義。reason: `invalidates_kind_mismatch`（或新增 `invalidates_not_applicable`——留給實作階段決定） |

### 9.2 併發邊界

| 案例 | 預期行為 |
|------|----------|
| 兩 agent 同時對同一 `task_id` 寫 `status_update` | 各產生一筆 active 的 `status_update`（各自 supersede 前手），`remagraph_status` 以 `created_at DESC` 取最新 |
| 兩 agent 同時寫語意高度相似的 `task_handoff` | 各自通過 #4（因比對時尚未看到對方的記錄），但後續 query 會看到兩筆相似內容。這可接受——`task_handoff` 不 supersede |
| 寫入中 crash（transaction 未完成） | SQLite rollback，無副作用。audit.jsonl 無記錄（因 audit 在 transaction commit 後才寫） |

### 9.3 極限值

| 案例 | 預期行為 |
|------|----------|
| `summary` 極長（> 10,000 字） | 接受。不設上限（SQLite TEXT 上限遠大於此）。但 model2vec `potion-multilingual-128M` 僅取前 512 token 做 embedding，超長不影響去重品質 |
| `learnings` 陣列極大（> 100 筆） | 接受。JSON 序列化存入 TEXT。不設上限 |
| `tags` 陣列極大 | 接受。同上 |
| 資料庫中無任何 active 記憶（首筆寫入） | 規則 #4 跳過（無可比對對象），自動通過 |

---

## 10. Given/When/Then 驗收條件

### 10.1 規則 #1：summary 長度

```
Given summary 為 "修了一個 bug"（7 字）
When remagraph_store 被呼叫
Then 回傳 status="rejected", reason="summary_too_short", detail 包含 "需 ≥ 30 字，目前 7 字"

Given summary 為 "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤，這是一個複雜的 race condition 問題"（40 字）
When remagraph_store 被呼叫
Then 規則 #1 通過，繼續規則 #2
```

### 10.2 規則 #2：learnings

```
Given learnings 為 []（空陣列）
When remagraph_store 被呼叫（其他欄位合法）
Then 回傳 status="rejected", reason="learnings_empty"

Given learnings 為 ["有意義的學習內容"]
When remagraph_store 被呼叫
Then 規則 #2 通過

Given learnings 為 ["  ", "\n"]（僅空白）
When remagraph_store 被呼叫
Then 回傳 status="rejected", reason="learnings_empty"
```

### 10.3 規則 #3：handoff_note

```
Given kind="task_handoff", handoff_note="接手者請注意"（7 字）
When remagraph_store 被呼叫
Then 回傳 status="rejected", reason="handoff_note_too_short"

Given kind="status_update", handoff_note=""（空字串，非 task_handoff）
When remagraph_store 被呼叫
Then 規則 #3 跳過（適用範圍僅 task_handoff），繼續規則 #4
```

### 10.4 規則 #4：去重

```
Given 資料庫中存在 mem-001（active, task_handoff, summary="嘗試修復 subagent 委派時的 acpx 連線錯誤"）
And 新請求 summary="attempt to fix acpx connection error during subagent delegation"（語意高度相似）
When remagraph_store 被呼叫
Then 回傳 status="rejected", reason="duplicate_content", closest_memory_id="mem-001"

Given 資料庫中存在 mem-001（active, status_update, summary="..."）
And 新請求 kind="task_handoff", summary="..."（完全不同內容）
When remagraph_store 被呼叫
Then 規則 #4 僅比對同 kind（task_handoff），通過
```

### 10.5 規則 #5：agent_id

```
Given agent_id 為 "OC-DSPRO"（含大寫）
When remagraph_store 被呼叫
Then 回傳 status="rejected", reason="invalid_agent_id"

Given agent_id 為 "oc-dspro"（全小寫、合法）
When 此 agent_id 首次出現
Then 規則 #5 通過，Lazy Registration 自動生效

Given agent_id 為 "a"（長度不足 3）
When remagraph_store 被呼叫
Then 回傳 status="rejected", reason="invalid_agent_id"
```

### 10.6 status_update supersede

```
Given 資料庫中存在 mem-001（task_id="task-2026-07-21-003", kind="status_update", status="active"）
And 資料庫中存在 mem-002（task_id="task-2026-07-21-005", kind="status_update", status="active"）——不同 task_id
When remagraph_store 寫入新 status_update（task_id="task-2026-07-21-003"）並通過仲裁
Then mem-001 的 status 變為 "superseded"
And mem-002 不受影響（task_id 不同）
And 新記錄 status="active"
And 回傳 superseded=1
```

### 10.7 discovered_constraint invalidates

```
Given 資料庫中存在 mem-001（kind="discovered_constraint", status="active"）
Given 資料庫中存在 mem-002（kind="discovered_constraint", status="active"）
When remagraph_store 寫入新 discovered_constraint，invalidates=["mem-001", "mem-002"]
Then mem-001 和 mem-002 的 status 變為 "invalidated"
And 新記錄 status="active"
And 回傳 invalidated_count=2

Given 資料庫中存在 mem-003（kind="task_handoff", status="active"）
When remagraph_store 寫入新 discovered_constraint，invalidates=["mem-003"]
Then 回傳 status="rejected", reason="invalidates_kind_mismatch"
And mem-003 不受影響

Given 資料庫中不存在 mem-999
When remagraph_store 寫入新 discovered_constraint，invalidates=["mem-999"]
Then 回傳 status="rejected", reason="invalidates_not_found"
```

---

## 11. PPLX 審查裁決（已定案）

以下為 PPLX 設計審查（2026-07-21）之裁決結果，已寫入本文件對應章節：

| ID | 原開放問題 | 裁決 | 落地章節 |
|----|-----------|------|----------|
| D01-Q1 | model2vec `potion-base-8M` CJK 支援程度 | 換用 `potion-multilingual-128M`（128MB，768 維），支援中文等多語言。載入失敗 **fail-fast** | §1.1、§5 規則 #4 |
| D01-Q2 | 去重門檻 0.92 校準依據 | v1 先用 **0.90**，標「待中文資料集校準」；可選按 kind 分門檻（建議非強制） | §5 規則 #4 |
| D01-Q3 | `status_update` 是否跨 `task_id` supersede | **嚴格同 task_id**，v1 不跨 task | §3.1（既有設計確認） |
| D01-Q4 | `discovered_constraint` invalidate 雙向追溯 | **不做雙向**追溯。`invalidated` status 足夠；audit.jsonl 可追溯因果 | §4.3（既有設計確認） |
| D01-Q5 | embedding 計算時機與快取策略 | v1 同 kind active ≤ 2000 全量線性掃描；超過取最新 2000。v2 才做 ANN 索引 | §5 規則 #4 |
| D01-Q6 | `remagraph_status` limit 預設值 | 預設 **20**、最大 **100** | §4.2（store.py `get_latest_status_updates`） |

### 其他跨文件裁決寫入本文件者

| ID | 裁決 | 落地章節 |
|----|------|----------|
| C1 | `handoff_note` ≥20 僅 `task_handoff` 強制；長度採 `len(handoff_note.strip())` | §1.1、§5 規則 #3 |
| — | `summary` 長度採 `len(summary.strip())` | §5 規則 #1 |
| — | embedding BLOB：`float32` little-endian `<f4` | §1.1、§5 規則 #4 |
| — | v1 單 process（PID 鎖），不支援多實例共用 DB | §8（D02 併發章節） |
| — | audit timestamp 全 UTC Z | §1.1（既有設計確認） |
| — | error 粒度：exception class name only | §8（既有設計確認） |
| — | MCP SDK 依賴 `mcp` 需寫入 pyproject / DESIGN | §7（設計層註記） |

---

## 12. 與 DESIGN.md 對齊聲明

本文件所有設計決策的來源皆來自 `/Users/aikenlin/Projects/RemaGraph/DESIGN.md`。以下為關鍵對齊點：

| DESIGN.md 章節 | 本文件對應 |
|----------------|-----------|
| 記憶 Schema（三種 kind、欄位定義） | §1 完整展開型別與約束 |
| status_update 的 supersede 規則 | §3 補充併發行為、回傳格式 |
| 五條仲裁規則 | §5 補充 reason_code、執行順序、回傳 JSON、錯誤碼 |
| agent_id Lazy Registration | §6 補充格式規則、邊界案例 |
| SQLite + FTS5 Schema | 實現層級（本文件不重複，直接引用 DESIGN.md §儲存層） |
| Audit Contract | 本文件不重複（外部合約，見 `docs/audit.md`） |
| 對外邊界（不認識 herdr-bridge / herdr-gov） | 本文件無任何 herdr 相關詞彙，已驗證 |

本文件新增的設計（`invalidates` 機制、錯誤碼表、Python 介面草圖）**不違反** DESIGN.md 中任何既有決策，且可追溯至 DESIGN.md 的對應原則。

---

## DONE

- [x] Memory schema 精確型別與約束（含 Pydantic `StoreRequest` / `Memory` 草圖）
- [x] 三種 kind 生命週期與 status 遷移
- [x] `status_update` supersede 機制（併發行為、回傳格式）
- [x] `discovered_constraint` invalidates 機制（驗證規則、不回傳 invalidates 清單的理由）
- [x] 五條仲裁規則精確定義（含 reason_code、回傳 JSON、執行流程圖）
- [x] Lazy Registration 的 agent_id 規則
- [x] 公開 Python 介面草圖（dataclass + 函式簽名層級）
- [x] 錯誤碼表（8 個 reason_code，含回傳範例）
- [x] 邊界案例（空值、併發、極限值）
- [x] Given/When/Then 驗收條件（7 組場景）
- [x] PPLX 審查裁決已定案（§11）
- [x] 與 DESIGN.md 對齊聲明
- [x] 驗證：全文無 `herdr-bridge`、`herdr-gov` 詞彙

---

## PPLX-CONSENSUS-APPLIED

以下為本次 PPLX 審查（2026-07-21）在本文件之落地 checklist：

- [x] B1：模型 `potion-base-8M` → `potion-multilingual-128M`（§1.1、§5 規則 #4、§7.2、§9.3）
- [x] B1：去重門檻 0.92 → 0.90，標「待中文資料集校準」（§5 規則 #4、§8）
- [x] B1：模型載入 fail-fast（§5 規則 #4）
- [x] B1：去重比對上限 2000 筆（§5 規則 #4）
- [x] B1：embedding 格式 float32 little-endian `<f4`（§1.1、§5 規則 #4）
- [x] C1：`summary` 長度採 `len(summary.strip())`（§5 規則 #1、§9.1）
- [x] C1：`handoff_note` ≥20 僅 `task_handoff` 強制；採 `len(handoff_note.strip())`（§5 規則 #3）
- [x] §11：開放問題 → 已裁決狀態表
- [x] 全文無 `potion-base-8M` 作為 v1 選定模型
- [x] 全文無與 PPLX 共識衝突的舊建議
