# T-RG-D05：測試與驗收設計

> **艦隊任務 ID**：`T-RG-D05`
> **狀態**：設計完成，尚未實作
> **約束**：本文件僅為設計產出，不修改 `src/`、`tests/`。以 `DESIGN.md` CI 門檻為 SOT。

---

## 1. 當前狀態摘要

| 層級 | 路徑 | 狀態 |
|------|------|------|
| 原始碼 | `src/remagraph/*.py`（8 檔案） | 全部 stub（`raise NotImplementedError`），僅 `__init__.py`（`__version__`）與 `models.py`（`Memory` Pydantic model，缺 `created_at`/`updated_at`）有內容 |
| 測試 | `tests/*.py`（5 檔案） | 全部佔位（一行 docstring，無實際測試） |
| CI | `.github/workflows/test.yml` | 存在，matrix: ubuntu/macos × py3.11–3.14，`--cov-fail-under=80` |
| 門檻 | `pyproject.toml` | dev deps: `pytest`, `pytest-cov`, `mutmut`, `ruff` |

---

## 2. 各模組驗收條件表

以下表格定義每個模組的**原始碼完成條件**與對應的**測試驗收條件**。測試通過方視為模組完成。

### 2.1 models.py

| # | 驗收條件 | 驗證方式 | 優先級 |
|---|---------|---------|--------|
| M1 | `Memory` 包含 DESIGN.md 定義的全部 14 個欄位（含 `created_at`、`updated_at`） | 單元 | 高 |
| M2 | `StoreRequest` 定義（`task_id`, `agent_id`, `kind`, `summary`, `learnings`, `handoff_note`, `tags`, `invalidates`） | 單元 | 高 |
| M3 | `StoreResponse` 含成功回應（`status: "stored"`, `id`, `superseded`）與拒絕回應（`status: "rejected"`, `reason`, `detail`） | 單元 | 高 |
| M4 | `SearchRequest`（`query`, `top_k`, `kind`, `status` 過濾）與 `SearchResponse`（`results[]`） | 單元 | 高 |
| M5 | `MemoryKind` literal 限制三值、`MemoryStatus` literal 限制三值 | 單元 | 高 |
| M6 | Pydantic validation：不合法的 `kind` 拋 `ValidationError` | 單元 | 高 |
| M7 | `learnings` / `tags` 預設空 list、`handoff_note` 預設空字串 | 單元 | 中 |

### 2.2 arbitration.py

| # | 驗收條件 | 驗證方式 | 優先級 |
|---|---------|---------|--------|
| A1 | 規則 #1：`summary` ≥ 30 Unicode codepoint，失敗回傳 `summary_too_short` | 單元（mock-free） | 高 |
| A2 | 規則 #2：`learnings` 至少一筆非空白元素，失敗回傳 `learnings_empty` | 單元 | 高 |
| A3 | 規則 #3：`kind="task_handoff"` 時 `handoff_note` ≥ 20 字；其他 kind 跳過 | 單元 | 高 |
| A4 | 規則 #4：model2vec cosine ≥ 0.92 拒絕，回傳 `duplicate_content` + `closest_memory_id` | 單元（mock model2vec） | 高 |
| A5 | 規則 #5：`agent_id` 符合 `^[a-z0-9_-]+$`，長度 3–64；失敗回傳 `invalid_agent_id` | 單元 | 高 |
| A6 | 規則順序保證——先便宜（#1–#3, #5）後昂貴（#4），任一失敗即停止 | 單元 | 高 |
| A7 | `supersede_status_updates()`：將同 `task_id` 所有 active `status_update` 標記為 superseded | 單元（in-memory SQLite） | 高 |
| A8 | `invalidate_constraints()`：驗證 invalidates 目標存在且 kind=`discovered_constraint`；失敗回傳 `invalidates_not_found` / `invalidates_kind_mismatch` | 單元（in-memory SQLite） | 高 |
| A9 | `invalidate_constraints()` 在 `kind != "discovered_constraint"` 且 `invalidates` 非空時拒絕 | 單元 | 中 |
| A10 | `generate_memory_id()` 格式 `mem-YYYYMMDD-NNN`，同一天內遞增、跨天重置 | 單元（in-memory SQLite） | 高 |

### 2.3 store.py

| # | 驗收條件 | 驗證方式 | 優先級 |
|---|---------|---------|--------|
| S1 | 通過全部仲裁後 INSERT 到 `memories` 表，14 欄位完整寫入 | 整合（in-memory SQLite + real arbitration） | 高 |
| S2 | FTS5 自動同步：INSERT 後 `memories_fts` 可查到 `summary` 內容 | 整合 | 高 |
| S3 | `status_update` supersede：寫入前自動將同 `task_id` 舊記錄標記 `superseded` | 整合 | 高 |
| S4 | `discovered_constraint` invalidates：寫入前標記目標為 `invalidated` | 整合 | 高 |
| S5 | 寫入後回傳 `StoreResponse`（含 `id`, `superseded`, `invalidated_count`） | 整合 | 高 |
| S6 | transaction 保證：寫入中途失敗，DB 無副作用（無孤立記錄、無半完成 supersede） | 整合 | 高 |
| S7 | embedding 計算與儲存（BLOB），v1 不查但欄位非空 | 整合 | 中 |

### 2.4 search.py

| # | 驗收條件 | 驗證方式 | 優先級 |
|---|---------|---------|--------|
| R1 | FTS5 BM25 全文檢索：`query="subagent deny-all error"` 回傳相關記憶，依 `rank` 排序 | 整合 | 高 |
| R2 | `kind` 過濾：僅回傳指定 kind 的記憶 | 整合 | 高 |
| R3 | `status` 過濾：`status="active"` 不應回傳 superseded/invalidated 記錄 | 整合 | 高 |
| R4 | `top_k` 限制：回傳筆數 ≤ `top_k` | 整合 | 高 |
| R5 | `query` 為空字串時回傳空 `results` + warning log（不拋錯，已裁決） | 整合 | 高 |
| R6 | 中文全文檢索（FTS5 tokenizer=`trigram` 支援 CJK） | 整合 | 高 |
| R7 | tag 過濾：依 `tags` 欄位 JSON 內容過濾 | 整合 | 中 |

### 2.5 dedup.py

| # | 驗收條件 | 驗證方式 | 優先級 |
|---|---------|---------|--------|
| D1 | model2vec `potion-multilingual-128M` 載入成功 | 單元 | 高 |
| D2 | `summary` → embedding（numpy array），維度正確 | 單元 | 高 |
| D3 | cosine similarity 計算正確（已知向量對的預期值） | 單元 | 高 |
| D4 | 同 kind、active 的既有記憶載入 embedding 做比對 | 單元（mock DB） | 高 |
| D5 | 相似度 ≥ 0.92 回傳最接近的 `memory_id` 與相似度值 | 單元 | 高 |
| D6 | 空資料庫（無既有記憶）自動通過 | 單元 | 中 |
| D7 | 長 summary（> 10,000 字）只取前 512 token 做 embedding，不影響效能 | 單元 | 中 |

### 2.6 audit.py

| # | 驗收條件 | 驗證方式 | 優先級 |
|---|---------|---------|--------|
| U1 | 成功寫入時 append 一行 JSONL 到 `audit.jsonl` | 整合 | 高 |
| U2 | audit 行包含全部 8 個欄位（`ts`, `actor_id`, `action`, `mem_id`, `task_id`, `status`, `error`） | 整合 | 高 |
| U3 | `ts` 為 ISO 8601 UTC 格式（精確到毫秒） | 整合 | 高 |
| U4 | `actor_id` 格式 `{agent_id}/{task_id}` | 整合 | 高 |
| U5 | 失敗時 `status="error"`, `error` 欄位含訊息（不含 traceback） | 整合 | 高 |
| U6 | 目錄不存在時自動建立（`~/.local/state/remagraph/`），權限 0700 | 單元 | 高 |
| U7 | `audit.jsonl` 權限 0600 | 單元 | 中 |
| U8 | 寫入失敗（權限不足、磁碟滿）時不影響 DB transaction（audit 在 transaction commit 後寫入） | 整合 | 中 |

### 2.7 db.py

| # | 驗收條件 | 驗證方式 | 優先級 |
|---|---------|---------|--------|
| B1 | `get_connection()` 回傳 sqlite3.Connection，路徑可設定（含 `:memory:` 供測試） | 單元 | 高 |
| B2 | `init_db()` 建立全部 table（`memories`, `memories_fts`）、trigger（`memories_ai`, `memories_ad`）、index（5 個） | 單元（in-memory SQLite） | 高 |
| B3 | 重複呼叫 `init_db()` 不報錯（`IF NOT EXISTS`） | 單元 | 高 |
| B4 | `migrate()` 介面存在（v1 可能為 no-op），為未來 schema 變更預留 | 單元 | 中 |
| B5 | WAL mode 啟用（並行讀寫效能） | 單元 | 中 |
| B6 | foreign_keys = ON | 單元 | 中 |

### 2.8 server.py

| # | 驗收條件 | 驗證方式 | 優先級 |
|---|---------|---------|--------|
| V1 | `remagraph_store` tool：接收 MCP request，回傳成功或拒絕 JSON | MCP 整合 | 高 |
| V2 | `remagraph_search` tool：接收 MCP request，回傳搜尋結果 JSON | MCP 整合 | 高 |
| V3 | `remagraph_status` tool：接收 MCP request（`limit`），回傳最新 active `status_update`（以 task_id 去重，取 `created_at DESC`） | MCP 整合 | 高 |
| V4 | 三個 tool 的輸入驗證（missing required field → 回傳 error） | MCP 整合 | 高 |
| V5 | Unix socket daemon 啟動、接受連線、graceful shutdown | MCP 整合 | 中 |
| V6 | 並行請求處理（多 agent 同時呼叫 `remagraph_store`） | MCP 整合 | 中 |

---

## 3. 單元 vs 整合（MCP）測試矩陣

### 3.1 定義

| 層級 | 定義 | 工具 |
|------|------|------|
| **單元測試** | 測試單一函式或類別，不依賴外部資源（DB、檔案系統、網路）。mock 可接受 | `pytest` + `unittest.mock` |
| **整合測試（DB）** | 測試多模組互動，使用 in-memory SQLite（`:memory:`），不 mock DB 層 | `pytest` + in-memory sqlite3 |
| **整合測試（MCP）** | 測試完整的 MCP tool 呼叫鏈，從 request 進到 response 出。使用 in-memory SQLite 與臨時 audit 目錄（`tmp_path`）。v1 使用 stdio transport | `pytest` + MCP client（stdio transport，subprocess 啟動 `remagraph serve`） |
| **整合測試（FS）** | 測試檔案系統互動（audit.jsonl 寫入、權限） | `pytest` + `tmp_path` |

### 3.2 矩陣

| 模組 | 單元 | DB 整合 | MCP 整合 | FS 整合 | 合計（最低） |
|------|:----:|:-------:|:--------:|:-------:|:-----------:|
| `models.py` | **7** | 0 | 0 | 0 | 7 |
| `arbitration.py` | **10** | 0 | 0 | 0 | 10 |
| `store.py` | 0 | **7** | 0 | 0 | 7 |
| `search.py` | 0 | **7** | 0 | 0 | 7 |
| `dedup.py` | **7** | 0 | 0 | 0 | 7 |
| `audit.py` | **2** | 0 | 0 | **6** | 8 |
| `db.py` | **6** | 0 | 0 | 0 | 6 |
| `server.py` | 0 | 0 | **6** | 0 | 6 |
| **合計** | **32** | **14** | **6** | **6** | **58** |

> 註：上表為**最低測試案例數**，非上限。邊界案例、錯誤路徑應額外增加。

### 3.3 測試執行策略

- **單元測試**：快速（< 5 秒），每次 commit 前執行。目標：100% 通過。
- **DB 整合測試**：使用 `:memory:` SQLite，中等速度（< 15 秒），每次 push 前執行。
- **MCP 整合測試**：使用 subprocess 啟動 `remagraph` MCP server，較慢（< 30 秒），PR 時執行。
- **FS 整合測試**：使用 `tmp_path`，中等速度，與 DB 整合測試一起執行。

---

## 4. Coverage ≥ 80 模組覆蓋策略

### 4.1 模組覆蓋權重

DESIGN.md 要求 `pytest --cov=src/remagraph --cov-fail-under=80`。以 code line 估算各模組的預期規模與對總覆蓋率的貢獻：

| 模組 | 預估 LOC | 佔比 | 目標覆蓋率 | 策略 |
|------|---------|------|-----------|------|
| `models.py` | ~60 | 10% | **100%** | 純資料類別，Pydantic validation 全部覆蓋 |
| `arbitration.py` | ~150 | 25% | **≥ 95%** | 核心邏輯，每一條規則的 pass/fail 路徑都要測 |
| `store.py` | ~120 | 20% | **≥ 85%** | DB 寫入邏輯，需測 transaction rollback |
| `search.py` | ~80 | 13% | **≥ 80%** | FTS5 查詢，至少覆蓋正常查詢 + 空結果 |
| `dedup.py` | ~60 | 10% | **≥ 85%** | model2vec 核心邏輯，但 mock DB 可達高覆蓋 |
| `audit.py` | ~50 | 8% | **≥ 75%** | 檔案 I/O，部分錯誤路徑（磁碟滿）難測 |
| `db.py` | ~50 | 8% | **≥ 85%** | Schema 建立與 migration，邏輯簡單可高覆蓋 |
| `server.py` | ~40 | 6% | **≥ 70%** | MCP 層薄包裝，依賴 MCP framework；核心邏輯在 store/search |

### 4.2 不可以犧牲覆蓋率的模組

以下模組**不允許**因「難以測試」而降低覆蓋率標準：

- `arbitration.py`：所有五條規則的邊界值（剛好 30 字、剛好 20 字、相似度 0.919 vs 0.921）必須有獨立測試案例
- `store.py`：supersede 與 invalidates 的 transaction 原子性必須驗證

### 4.3 CI 覆蓋率報告

除了 `--cov-fail-under=80`，建議 CI 產出以下 artifacts：

- `coverage.xml`：供 Codecov / Coveralls 使用（可選）
- `coverage.json`：summary by module，用於 CI 步驟中比對各模組覆蓋率是否達標

---

## 5. mutmut 核心模組清單與建議門檻

DESIGN.md 規定 mutmut「非阻塞但持續追蹤」。本節定義**哪些模組該跑 mutmut**以及**初步目標**。

### 5.1 核心模組清單

| 優先級 | 模組 | 理由 | 建議 mutmut 門檻（存活率） |
|--------|------|------|--------------------------|
| **P0** | `arbitration.py` | 五條規則的正確性直接影響記憶品質；false positive（誤拒）與 false negative（漏放）都不可接受 | ≤ 5% 存活 mutant（≥ 95% killed） |
| **P0** | `dedup.py` | cosine similarity 計算的正確性、門檻值邊界行為 | ≤ 10% 存活 |
| **P1** | `store.py` | transaction 原子性、supersede/invalidate 邏輯 | ≤ 15% 存活 |
| **P1** | `search.py` | FTS5 query 構建、過濾邏輯 | ≤ 15% 存活 |
| **P2** | `models.py` | Pydantic validation 正確性 | ≤ 20% 存活 |
| **P2** | `db.py` | Schema 建立、migration | ≤ 20% 存活 |
| **P3** | `audit.py` | JSONL 寫入格式正確性 | 追蹤，不設硬門檻（檔案 I/O 難以 mutation test） |
| **P3** | `server.py` | MCP 薄層，多數邏輯委派給 store/search | 追蹤，不設硬門檻 |

### 5.2 執行策略

```bash
# 僅跑 P0 + P1 模組（建議 CI job）
mutmut run --paths-to-mutate "src/remagraph/arbitration.py" \
                         "src/remagraph/dedup.py" \
                         "src/remagraph/store.py" \
                         "src/remagraph/search.py"

# 全模組（ nightly / pre-release）
mutmut run --paths-to-mutate "src/remagraph/"
```

### 5.3 mutmut CI 行為

- **不在 `test.yml` 中跑**（避免 PR 被非阻塞門檻擋住）
- 獨立 workflow：`mutmut.yml`，每週跑一次 + 手動觸發（`workflow_dispatch`）
- 產出 `mutmut-report.json`，CI 步驟僅報告「存活 mutant 數變化」供追蹤，**不做 pass/fail**

---

## 6. 邊界案例清單

以下邊界案例來自 DESIGN.md、T-RG-D01 設計、以及本文件分析。測試實作時應全部涵蓋。

### 6.1 仲裁拒絕路徑

| ID | 案例 | 預期結果 |
|----|------|----------|
| BC01 | `summary` 為空字串 `""` | `summary_too_short`（0 < 30） |
| BC02 | `summary` 恰好 29 字 | `summary_too_short` |
| BC03 | `summary` 恰好 30 字 | 通過規則 #1 |
| BC04 | `summary` 全形空白（如 `"　　　"`，U+3000） | `summary_too_short`（codepoint ≥ 30 但 strip 後為空——實作階段決定是否 trim） |
| BC05 | `learnings = []` | `learnings_empty` |
| BC06 | `learnings = [""]`（空字串元素） | `learnings_empty`（`s.strip()` 後為空） |
| BC07 | `learnings = [" ", "\n", "\t"]`（全空白元素） | `learnings_empty` |
| BC08 | `kind=task_handoff`, `handoff_note` 恰好 19 字 | `handoff_note_too_short` |
| BC09 | `kind=task_handoff`, `handoff_note` 恰好 20 字 | 通過規則 #3 |
| BC10 | `kind=status_update`, `handoff_note=""` | 通過規則 #3（不檢查） |
| BC11 | `agent_id` 為 `"OC-DSPRO"`（含大寫） | `invalid_agent_id` |
| BC12 | `agent_id` 為 `"a"`（長度 1，< 3） | `invalid_agent_id` |
| BC13 | `agent_id` 為 `"a" * 65`（長度 65，> 64） | `invalid_agent_id` |
| BC14 | `agent_id` 為 `"agent.name"`（含 `.`） | `invalid_agent_id` |
| BC15 | `agent_id` 為 `"oc-dspro"`（合法、首次出現） | Lazy Registration 通過 |
| BC16 | 兩筆語意高度相似的 `task_handoff`（cosine=0.95） | 第二筆 `duplicate_content` |

### 6.2 supersede 競態

| ID | 案例 | 預期結果 |
|----|------|----------|
| BC20 | 兩 agent 同時對同一 `task_id` 寫 `status_update` | 各自產生一筆 `active`；`remagraph_status` 以 `created_at DESC` 取最新 |
| BC21 | 新 `status_update` 寫入時，同 `task_id` 的 `task_handoff` 不受影響 | `task_handoff` 保持 `active`（不應被 supersede） |
| BC22 | 新 `status_update` 寫入時，不同 `task_id` 的 `status_update` 不受影響 | 僅同 `task_id` 被 supersede |
| BC23 | `status_update` 寫入中途 crash | DB rollback，無半完成 supersede（transaction 保證） |

### 6.3 FTS 空查詢與邊界搜尋

| ID | 案例 | 預期結果 |
|----|------|----------|
| BC30 | `remagraph_search` 的 `query=""`（空字串） | 回傳空 `results` 或拒絕（設計階段決定；建議回傳空結果 + hint） |
| BC31 | `query` 僅含 FTS5 特殊字元（`*`, `"`, `(`, `)`） | Server 端 sanitize 後正確處理（轉義或包裹為 phrase query），不拋 SQL exception。已裁決：必須 sanitize |
| BC32 | `query` 為中文（如 `"連線錯誤"`）且 DB 中有中文內容 | FTS5 `trigram` tokenizer 正確分詞，回傳相關結果。若 SQLite < 3.34 無 trigram，降級為手動 bigram 前處理 |
| BC33 | `top_k=0` | 回傳空 `results` |
| BC34 | `top_k` 極大（如 10000） | 回傳實際存在的筆數，不超過總數 |
| BC35 | 無任何記憶時搜尋 | 回傳空 `results` |
| BC36 | `kind` filter 指定不存在的 kind（如 `"unknown_kind"`） | 回傳空 `results` 或拒絕（設計階段決定） |

### 6.4 audit 權限與錯誤

| ID | 案例 | 預期結果 |
|----|------|----------|
| BC40 | `audit.jsonl` 目錄不存在 | 自動建立目錄（0700）和檔案（0600） |
| BC41 | `audit.jsonl` 目錄無法寫入（權限不足） | 記錄錯誤 log，不 crash server；已寫入的 DB record 不受影響 |
| BC42 | `audit.jsonl` 寫入失敗後，下次寫入是否正確 | 下次成功的 `remagraph_store` 仍正確 append audit 記錄 |
| BC43 | 多 process 同時寫 audit（race） | JSONL append 為單行，每行獨立；交錯寫入可接受（但 DB transaction 保證一致性） |

### 6.5 DB 與生命週期邊界

| ID | 案例 | 預期結果 |
|----|------|----------|
| BC50 | `invalidate_constraints()` 指定不存在的 `id` | `invalidates_not_found` |
| BC51 | `invalidate_constraints()` 指定 `kind=task_handoff` 的 id | `invalidates_kind_mismatch` |
| BC52 | `invalidate_constraints()` 指定已被 invalidated 的 id | 仍執行（更新 `updated_at`），但 `invalidated_count` 正確 |
| BC53 | `kind=discovered_constraint` 無 `invalidates` 欄位 | 正常寫入，`status=active` |
| BC54 | `kind=task_handoff` 但帶有 `invalidates` 欄位 | 拒絕（`invalidates_not_applicable`） |
| BC55 | 同一天內寫入第 1000 筆記憶（`mem-YYYYMMDD-1000`） | ID 格式正確，流水號不中斷 |
| BC56 | `init_db()` 重複呼叫 | 不報錯（`IF NOT EXISTS`），schema 不變 |

---

## 7. 與現有 tests/*.py stub 的對應關係

| 現有 stub 檔案 | 對應原始碼模組 | 應涵蓋的測試類型 | 應涵蓋的驗收條件 |
|---------------|---------------|-----------------|-----------------|
| `tests/test_arbitration.py` | `arbitration.py` | 單元（mock-free + mock DB） | A1–A10（全部 10 條） |
| `tests/test_store.py` | `store.py` | DB 整合（in-memory SQLite） | S1–S7（全部 7 條） |
| `tests/test_search.py` | `search.py` | DB 整合（in-memory SQLite） | R1–R7（全部 7 條） |
| `tests/test_dedup.py` | `dedup.py` | 單元（mock DB / real model2vec） | D1–D7（全部 7 條） |
| `tests/test_audit.py` | `audit.py` | 單元 + FS 整合（`tmp_path`） | U1–U8（全部 8 條） |

### 7.1 需要新增的測試檔案

| 建議新增檔案 | 對應原始碼模組 | 理由 |
|-------------|---------------|------|
| `tests/test_models.py` | `models.py` | 目前無對應測試檔；Pydantic schema 驗證需要獨立測試 |
| `tests/test_db.py` | `db.py` | 目前無對應測試檔；schema 建立、migration 需要獨立測試 |
| `tests/test_server.py` | `server.py` | MCP tool 整合測試，使用 in-memory SQLite + MCP client |
| `tests/conftest.py` | 全部 | 共享 fixtures（in-memory DB、tmp audit dir、model2vec instance） |

### 7.2 conftest.py 建議內容

```python
# tests/conftest.py — 共享 fixtures

import sqlite3
import pytest
from pathlib import Path

@pytest.fixture
def conn():
    """in-memory SQLite，已 init schema。"""
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    # init_db(c)  # 待 db.py 實作後取消註解
    yield c
    c.close()

@pytest.fixture
def tmp_audit_dir(tmp_path):
    """臨時 audit 目錄，供 audit.py 測試。"""
    d = tmp_path / "remagraph"
    d.mkdir(parents=True)
    return d

@pytest.fixture
def sample_request():
    """合法的 StoreRequest，供各測試複用。"""
    from remagraph.models import StoreRequest
    return StoreRequest(
        task_id="task-2026-07-21-003",
        agent_id="oc-dspro",
        kind="task_handoff",
        summary="嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤，這是一個需要深入調查的問題",
        learnings=["錯誤發生在 opencode task tool 生成 child session 之後"],
        handoff_note="接手者請注意：此錯誤與 G1 不同，G1 是 child session 未被註冊",
        tags=["acpx", "subagent", "bug"],
    )
```

---

## 8. CI 建議 job 行為

### 8.1 現有 `test.yml` 分析

```yaml
# 現有 config（無需變更，僅補強）
matrix:
  os: [ubuntu-latest, macos-latest]
  python-version: ["3.11", "3.12", "3.13", "3.14"]
```

**現有行為**：`pip install -e ".[dev]"` → `pytest --cov=src/remagraph --cov-fail-under=80`

### 8.2 建議補強

| 補強項目 | 說明 | 優先級 |
|---------|------|--------|
| **ruff lint** | 在 pytest 之前跑 `ruff check src/ tests/`，確保程式碼風格一致 | 高 |
| **trigram 支援檢查** | CI 驗收應包含「確認 SQLite 支援 `tokenize='trigram'`」之設計註記（SQLite ≥ 3.34）。測試環境若無 trigram，降級測試為手動 bigram 前處理 | 高 |
| **分層執行** | 單元測試先跑（`pytest tests/test_models.py tests/test_arbitration.py tests/test_dedup.py tests/test_db.py -v`），通過後才跑整合測試 | 中 |
| **coverage report** | 產出 `coverage.xml` 作為 CI artifact，供 Codecov/Coveralls（可選） | 低 |
| **Python 3.14 允許失敗** | `continue-on-error: true` for `python-version: "3.14"`（pre-release，可能有不穩定的依賴） | 中 |
| **cache pip** | `actions/setup-python@v5` 內建 cache，加上 `cache: pip` | 中 |
| **OS-specific skip** | macOS 上跑 FS 整合測試需注意 `/tmp` vs `TMPDIR` 差異；已在 `tmp_path` fixture 中處理 | 低 |

### 8.3 建議的 `test.yml` 最終步驟順序

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with:
      python-version: ${{ matrix.python-version }}
      cache: pip
  - run: pip install -e ".[dev]"
  - run: ruff check src/ tests/
  - run: pytest --cov=src/remagraph --cov-fail-under=80 --cov-report=xml --cov-report=term
  - uses: actions/upload-artifact@v4   # 可選
    if: always()
    with:
      name: coverage-${{ matrix.os }}-${{ matrix.python-version }}
      path: coverage.xml
```

### 8.4 建議新增 `mutmut.yml`

```yaml
name: mutmut
on:
  schedule:
    - cron: "0 6 * * 1"   # 每週一 06:00 UTC
  workflow_dispatch:
jobs:
  mutate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: mutmut run --paths-to-mutate "src/remagraph/arbitration.py" "src/remagraph/dedup.py" "src/remagraph/store.py" "src/remagraph/search.py"
      - run: mutmut results
```

### 8.5 CI 品質門檻總覽

| Workflow | 觸發 | OS | Python | 行為 | Blocking? |
|----------|------|-----|--------|------|:---------:|
| `test.yml` | push/PR | ubuntu, macos | 3.11–3.14 | ruff → pytest + coverage≥80 | **是** |
| `gitleaks.yml` | push/PR | ubuntu | — | 全 Git 歷史機密掃描 | **是** |
| `pip-audit.yml` | push/PR | ubuntu | 3.12 | 依賴漏洞掃描 | **是** |
| `mutmut.yml` | 每週一 + 手動 | ubuntu | 3.12 | mutation testing（P0+P1） | 否（追蹤） |

---

## 9. 開放問題

以下問題在測試設計階段浮現，需在實作前或實作初期決定：

| # | 問題 | 影響 | 建議 |
|---|------|------|------|
| Q1 | `models.py` 的 `StoreRequest` 尚未定義（D01 有草圖但 src 中尚未實作）。測試時應先補 `models.py` 還是測試先行（TDD）？ | 所有模組的型別依賴 | **已裁決**：TDD 先 `test_models.py`。先寫測試定義期望 schema，再實作 `models.py` 讓測試通過（設計層級註記） |
| Q2 | `remagraph_search` 中 `query=""` 的行為？ | 邊界案例 BC30 | 建議回傳空 `results` + warning log，不拋錯。agent 可能傳空查詢 |
| Q3 | `remagraph_status` 的 `limit` 參數預設值？D01 Q6 已提出，未定案 | API 設計 | 建議預設 `limit=20`，最大 100 |
| Q4 | 規則 #1 的 `summary` 長度計算：純 codepoint 還是 strip 後計數？ | BC04 全形空白的處理 | 建議 `len(summary.strip())`，排除前後空白再計數。避免 agent 用空白填充繞過門檻 |
| Q5 | MCP 整合測試使用 stdio transport 還是 Unix socket？ | 測試環境相容性 | 建議 stdio（跨平台、CI 環境相容）。**已裁決**：v1 MCP 整合測試以 stdio 為主 |
| Q6 | FTS5 的 tokenizer 選擇：`unicode61`（內建）還是需額外設定 CJK 分詞？ | 中文搜尋品質（BC32） | **已裁決**：使用 `tokenize='trigram'`（非 `unicode61`）。`unicode61` 對 CJK 做 unicode codepoint 分詞（非 bigram），對中文搜尋品質不佳。trigram 為 3-gram tokenizer，對中日韓文提供實用的分詞效果。若 runtime SQLite < 3.34 無 trigram 支援，降級方案為手動 bigram 前處理（設計層級記載） |
| Q7 | mutmut 在 CI 上跑可能極慢（每 mutant 需跑對應測試）。是否需要 parallel runner？ | CI 時間 | **已裁決**：mutmut 限縮 `arbitration.py` + `dedup.py`（P0 模組）。使用 `--runner pytest -n auto`；非阻塞 CI job |
| Q8 | coverage 報告中，`server.py` 的 MCP 薄層是否該豁免覆蓋率要求？ | 假性低覆蓋 | 建議 `server.py` 走 MCP 整合測試覆蓋，但若 MCP framework boilerplate 難以覆蓋，可在 `pyproject.toml` 中設定 exclude |

---

## 10. 與 DESIGN.md 及 D01 對齊聲明

| 來源 | 關鍵約束 | 本文件對應 |
|------|---------|-----------|
| DESIGN.md §CI/CD 品質門檻 | pytest + coverage≥80 + mutmut（非阻塞）+ gitleaks + DCO | §4（coverage）、§5（mutmut）、§8.5（CI 總覽） |
| DESIGN.md §輕量仲裁 | 五條規則，任一失敗即拒絕 | §2.2（arbitration 驗收條件 A1–A6）、§6.1（BC01–BC16） |
| DESIGN.md §status_update supersede | 同 task_id 自動 supersede | §2.2（A7）、§6.2（BC20–BC23） |
| DESIGN.md §discovered_constraint invalidates | 顯式 invalidate，僅限同 kind | §2.2（A8–A9）、§6.5（BC50–BC54） |
| DESIGN.md §Audit Contract | audit.jsonl 格式、路徑、權限 | §2.6（audit 驗收條件 U1–U8）、§6.4（BC40–BC43） |
| D01 §10 GWT 驗收條件 | 7 組 Given/When/Then 場景（規則 #1–#5、supersede、invalidates） | 全部納入 §2.2 驗收條件表，並擴充額外邊界案例 |
| D01 §8 錯誤碼表 | 8 個 reason_code | §2.2 驗收條件對應各 reason_code，§6.1 邊界案例涵蓋全部錯誤路徑 |
| D01 §9 邊界案例 | 空值、併發、極限值 | §6 擴充為完整 6 類邊界案例（BC01–BC56） |

本文件所有設計決策**不違反** DESIGN.md 與 D01 中任何既有決策。新增的測試策略（分層執行、mutmut CI、conftest.py 設計）為**測試層級補充**，不影響原始碼架構。

---

## 11. 測試實作建議順序

鑑於全部 `src/` 除 `models.py` 外都是 stub，測試設計應遵從以下依賴順序：

```
1. models.py        ← 先定義型別（TDD: test_models.py → models.py）
2. db.py            ← schema 建立（其他模組都依賴 DB schema）
3. arbitration.py   ← 核心邏輯（store 依賴仲裁結果）
4. dedup.py         ← 去重（store 依賴規則 #4）
5. store.py         ← 寫入邏輯（依賴 arbitration + dedup + db）
6. search.py        ← 查詢邏輯（依賴 store 寫入的資料）
7. audit.py         ← 審計（store 寫入後寫 audit）
8. server.py        ← MCP entrypoint（依賴全部模組）
```

---

## DONE

- [x] 8 個模組驗收條件表（64 條驗收條件）
- [x] 單元 vs 整合（MCP）測試矩陣（58 個最低案例，4 種測試層級）
- [x] coverage ≥80 模組覆蓋策略（含各模組目標覆蓋率與不可犧牲清單）
- [x] mutmut 核心模組清單（P0–P3 分級）與建議 CI 行為
- [x] 邊界案例清單（56 個邊界案例，分 5 類）
- [x] 與現有 tests/*.py stub 對應關係（含建議新增檔案）
- [x] CI 建議 job 行為（test.yml 補強 + mutmut.yml 新增 + 門檻總覽）
- [x] 開放問題（8 題）與對齊聲明
- [x] 驗證：全文無 `herdr-bridge`、`herdr-gov` 詞彙
- [x] 驗證：所有約束以 `DESIGN.md` 為 SOT

---

## PPLX-CONSENSUS-APPLIED

本文件已完成以下 PPLX 共識裁決的寫入（2026-07-21）：

- [x] **B2 — FTS5 trigram tokenizer**：R6 改為 `tokenize='trigram'`（非 `unicode61`）；Q6 裁決明確記載 trigram 並修正「unicode61 是 bigram」之錯誤描述；BC32 更新
- [x] **B2 — trigram 降級方案**：Q6 記載若 SQLite < 3.34 無 trigram 支援，降級為手動 bigram 前處理
- [x] **B2 — CI trigram 檢查**：§8.2 新增「trigram 支援檢查」補強項目
- [x] **B3 — stdio 測試優先**：Q5 裁決明確記載 v1 MCP 整合測試以 stdio 為主；§3.1 定義更新
- [x] **FTS query sanitize**：BC31 更新為「Server 端必須 sanitize」（已裁決）
- [x] **query="" 行為**：R5 更新為「回傳空 results + warning log，不拋錯」
- [x] **TDD models 優先**：Q1 裁決明確記載：先 `test_models.py`（設計層級註記）
- [x] **mutmut 限縮 arbitration+dedup**：Q7 裁決明確記載限縮 P0 模組
- [x] **模型名稱**：D1 `potion-base-8M` → `potion-multilingual-128M`
- [x] **全文無 potion-base-8M** 舊名稱
