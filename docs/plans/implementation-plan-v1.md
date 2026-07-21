# RemaGraph v1 實作計畫

| 項目 | 內容 |
|------|------|
| **狀態** | **PPLX 複審 APPROVE — 計畫已凍結**（**禁止實作**直至人類明確同意） |
| **Date** | 2026-07-21 |
| **Version** | v1.1（依 PPLX 初審修訂 A–F + 關鍵 N） |
| **Orchestrated-by** | CommandTower |
| **SOT** | [`DESIGN.md`](../../DESIGN.md)（設計凍結 commit `8905d23`） |
| **設計展開** | [`docs/design/00-index.md`](../design/00-index.md) 及 D01–D05 |
| **設計複審** | [`docs/design/reviews/pplx-recheck-2026-07-21.md`](../design/reviews/pplx-recheck-2026-07-21.md) → **APPROVE** |
| **計畫初審** | [`docs/plans/reviews/pplx-impl-plan-review-2026-07-21.md`](reviews/pplx-impl-plan-review-2026-07-21.md) → APPROVE_WITH_CHANGES → 本版修訂 |

---

## 0. 硬性邊界（本文件與後續執行皆適用）

1. **本文件只規劃實作，不執行實作。** 進入寫碼／改 stub 行為前，必須另有使用者明確指示（例如「同意實作」）。
2. 實作必須對齊凍結設計；若與 `DESIGN.md` 衝突，**以 DESIGN.md 為準**並開 ADR／回報，不得靜默偏離。
3. **不耦合** herdr-bridge／herdr-gov（程式碼、README、tool 名稱皆不得綁定）。
4. 依賴新增限設計已列：`model2vec`、`mcp`、`pydantic`；optional `sqlite-vec` 僅 v2。建議 **pin 上界**（如 `model2vec>=0.1.0,<2.0`、`mcp>=1.0,<2`）於 WU-0。
5. 指揮塔／艦隊紀律：實作由艦隊經 `route()` 派工；本計畫可被派工作為「實作藍圖」，**不是**授權立刻開工。
6. **本計畫 APPROVE ≠ 開工令**；仍需人類「同意實作」。

---

## 1. 目標與非目標

### 1.1 v1 目標（完成定義）

可 `pip install`（或 editable install）後，以 **stdio MCP** 暴露三 tool：

| Tool | 行為摘要 |
|------|----------|
| `remagraph_store` | 五條仲裁 → SQLite + FTS5 + embedding BLOB + audit.jsonl |
| `remagraph_search` | FTS5 BM25 + trigram、過濾、`top_k`、`has_more` |
| `remagraph_status` | active `status_update` 依 `task_id` 去重取最新，`limit` 預設 20／最大 100 |

state：`~/.local/state/remagraph/`（db + audit，權限 0700／0600）。  
品質門檻：pytest 全綠、coverage ≥ 80、gitleaks 無發現；mutmut 追蹤 P0（arbitration + dedup），**mutation score 目標 ≥ 70%**（低於則 PR 必須說明原因，不得靜默合入）。

### 1.2 非目標（v1 不做）

- Unix socket daemon 作為預設（僅文件／選配進階，非 v1 必交付）
- sqlite-vec 語意搜尋、audit rotation、多 process 共用 DB
- 跨 `task_id` supersede、invalidate 雙向追溯
- PyPI 正式 release 流程自動化（可另開 HITL release 計畫）
- 中文去重門檻正式校準（v1 用 0.90 並標「待校準」）

---

## 2. 凍結決策摘要（實作者必讀）

| 主題 | 凍結值 |
|------|--------|
| Transport | **stdio 優先**；socket 進階／路線圖 |
| 去重模型 | `potion-multilingual-128M`；載入 **fail-fast**；stdio **lazy load**（見下） |
| 去重門檻 | cosine ≥ **0.90**（待中文校準）；同 kind active ≤ **2000**（超出取最新 2000） |
| Embedding BLOB | float32 little-endian **`<f4>`**，v1 只存不查 |
| Embedding 維度 | **以 WU-4 對 pin 版 model2vec 首次 encode 實測為準**，寫入單一常數 `EMBEDDING_DIM`；單元測 `assert emb.shape == (EMBEDDING_DIM,)` 與 `len(blob)==EMBEDDING_DIM*4`。文獻有 256／768 分歧（HF vs D02）；**禁止靜默假設，必須 assert 鎖定** |
| FTS5 | `tokenize='trigram'`；query **sanitize**；空 query → 空 results + warning；**1–2 字元查詢同空 query 路徑**（trigram 最短長度） |
| 仲裁 #1 | `len(summary.strip()) ≥ 30` |
| 仲裁 #3 | `handoff_note ≥ 20` **僅** `task_handoff` |
| agent_id | `^[a-z0-9_-]+$`，長度 3–64（**大寫必須拒絕**） |
| Search / status 預設 | `top_k`／`limit` 預設 **20**、最大 **100**；search 用 **`has_more`** |
| `has_more` 演算法 | **查 `top_k+1` 筆**；若存在第 `top_k+1` 筆則 `has_more=true`，只回傳前 `top_k`（禁止用全表 COUNT 當預設） |
| supersede | 嚴格同 `task_id` 且僅作用於 **status_update kind**（他 kind 不 supersede） |
| model2vec 失敗語意 | lazy load 觸發點＝**第一次需要 embedding 的 store 路徑**；失敗 → **tool 回傳結構化錯誤，server 不崩潰**；連續失敗 log CRITICAL |
| 併發 | v1 單 process（stdio 序列化） |
| TDD 起手 | 先擴充 models + `test_models.py` |
| SQLite | CI／執行環境 **≥ 3.38** 且支援 FTS5 trigram：`assert sqlite3.sqlite_version_info >= (3, 38, 0)` 並驗證 `tokenize='trigram'` |

詳細見 D01–D05 與 `00-index.md` §2。若 D05 殘留 cosine 0.92，**以 DESIGN／本表 0.90 為準**。

---

## 3. 工作單元（Work Units）

原則：**依依賴由底向上**；可平行者標「∥」；每單元結束須通過該單元閘門後才合入下一層。

### WU-0 — 工程基線（∥ 可先做）

| 項目 | 內容 |
|------|------|
| 範圍 | `pyproject.toml` 依賴 pin 上界；entry point；ruff；CI 加入 SQLite≥3.38 + trigram 斷言步驟 |
| 依賴 | 無 |
| 驗收 | editable install；`import remagraph`；CI 有 SQLite 版本 gate |
| 對應設計 | DESIGN 部署／pyproject；D03；D05 CI |

### WU-1 — models（型別合約）

| 項目 | 內容 |
|------|------|
| 範圍 | 完整 `Memory`、`StoreRequest`／`StoreResponse`、`SearchRequest`／`SearchResponse`（含 `has_more`）、literals |
| 依賴 | WU-0 |
| 驗收 | `test_models.py` 覆蓋 D05 M1–M7；TDD |
| 對應設計 | D01；D03 schema |

### WU-2 — db（連線、DDL、migration）

| 項目 | 內容 |
|------|------|
| 範圍 | state 目錄與權限；WAL；完整 DDL（timestamp、memories_au、trigram、indexes）；`_meta`；冪等 init；**已存在 DB 重啟不破壞資料** |
| 依賴 | WU-1（可選） |
| 驗收 | schema／trigram 可用；重開連線資料仍在；權限測試 |
| 對應設計 | D02；D04 FS |

### WU-3 — arbitration（規則 #1–#3、#5）

| 項目 | 內容 |
|------|------|
| 範圍 | summary／learnings／handoff_note／agent_id；reason_code；順序 fail-fast |
| 依賴 | WU-1 |
| 驗收 | A1–A3、A5–A6；**含大寫 agent_id 拒絕** |
| 對應設計 | D01 §5–8 |

### WU-4 — dedup（規則 #4）

| 項目 | 內容 |
|------|------|
| 範圍 | model2vec；encode；cosine 0.90；2000 上限；fail-fast 載入；`<f4>`；**鎖定 EMBEDDING_DIM**；document HF cache（`HF_HOME` 等） |
| 依賴 | WU-1、WU-2 |
| 驗收 | `assert emb.shape == (EMBEDDING_DIM,)`；`len(blob)==EMBEDDING_DIM*4`；D2–D6（門檻 0.90） |
| 對應設計 | D02 §6–7 |

### WU-5 — store + supersede／invalidate

| 項目 | 內容 |
|------|------|
| 範圍 | 仲裁後 insert；FTS；supersede（同 task_id 之 status_update）；invalidates；transaction；StoreResponse；embedding 寫入 |
| 依賴 | WU-2、WU-3、WU-4 |
| 驗收 | S1–S7；負面：他 kind 不誤 supersede；transaction 失敗無半成品 |
| 對應設計 | D01；D02 store |

### WU-6 — search + status

| 項目 | 內容 |
|------|------|
| 範圍 | BM25 + trigram；**sanitize 為可單測函式**；過濾；top_k／has_more（top_k+1 演算法）；status 去重；短 query |
| 依賴 | WU-2、WU-5 |
| 驗收 | R1–R7；**同 task_id ≥3 筆 status_update → 只回最新 1 筆**；superseded+active 混合只計 active；has_more 邊界；1–2 字元中文 → 空 + warning |
| 對應設計 | D02 search；D03 tools |

### WU-7 — audit

| 項目 | 內容 |
|------|------|
| 範圍 | audit.jsonl；schema；stored／error；no traceback；權限；**commit 成功後** append；best-effort 不回滾 DB；記憶體完整序列化後再 write |
| 依賴 | **WU-5**（強制：在 store transaction `commit()` 成功後寫 stored；commit 失敗寫 error） |
| 驗收 | U1–U8；仲裁拒絕亦有 error audit + reason |
| 對應設計 | D04；`docs/audit.md` |

### WU-8 — server（MCP stdio）

| 項目 | 內容 |
|------|------|
| 範圍 | mcp SDK stdio；三 tool；錯誤映射；lazy load；失敗不崩潰；優雅關閉；日誌無 secret |
| 依賴 | WU-5、WU-6、WU-7 |
| 驗收 | MCP 整合測 stdio；契約符合 D03 |
| 對應設計 | D03 |

### WU-9 — 測試補強與 CI

| 項目 | 內容 |
|------|------|
| 範圍 | 矩陣補齊；coverage ≥80；mutmut arbitration+dedup **目標 ≥70%**；ruff；gitleaks |
| 依賴 | WU-1–WU-8 |
| 驗收 | 本地與 CI 綠；mutation 報告可追蹤 |
| 對應設計 | D05 |

### WU-10 — 文件

| 項目 | 內容 |
|------|------|
| 範圍 | README 安裝／stdio MCP config；說明 v1 tool schema **不做版本化**（記 v2 技術債）或標 `_schema_version`；短 query／首次模型下載說明 |
| 依賴 | WU-8 |
| 驗收 | README 可複製即用；**不** publish |
| 對應設計 | DESIGN；D03 |

---

## 4. 依賴圖

```
WU-0 ─┬─► WU-1 ─► WU-3 ─────────────┐
      │                              ├─► WU-5 ─┬─► WU-6 ─┐
      └─► WU-2 ─► WU-4 ─────────────┘         │         ├─► WU-8 ─► WU-9 ─► WU-10
                                               └─► WU-7 ─┘
```

- **WU-7 depends on WU-5**：audit 僅在 SQLite `commit()` 成功後寫 `status=stored`；commit 失敗寫 `status=error`。
- **關鍵路徑**：WU-1 → WU-2 → **WU-4（高風險：模型／維度）** → WU-5 → WU-8 → WU-9
- **可平行**：WU-3 與 WU-2／WU-4（models 穩定後）

---

## 5. 建議派工與路由提示（執行時，非現在）

| 單元類型 | task_type | sensitivity 建議 |
|----------|-----------|------------------|
| models／db DDL | implement | S0–S1 |
| 仲裁／去重 | implement | S1 |
| MCP server | implement | S1 |
| 對抗式審查 | review／audit | S1–S2 |

一律 `route()`，禁止手挑模型。

---

## 6. 驗證閘門

### 6.1 每 WU

- [ ] 對應驗收列通過
- [ ] 無 herdr 耦合
- [ ] 無未核准依賴
- [ ] guilty-until-proven

### 6.2 Pre-merge

| Gate | 條件 |
|------|------|
| lint | ruff 0 |
| tests | pytest 全綠 |
| coverage | ≥ 80 |
| secret | gitleaks clean |
| dep | pip-audit 無未處理 HIGH/CRITICAL |
| mutation | arbitration+dedup 目標 ≥70% 或 PR 說明 |
| sqlite | version ≥ 3.38 + trigram |
| design | 抽樣 §2 凍結表 |

### 6.3 內容驗收（防半殘）

- [ ] store 後 SQLite 可讀預期欄位；FTS 可查
- [ ] store 成功 → BLOB `np.frombuffer(blob, dtype='<f4').shape == (EMBEDDING_DIM,)`
- [ ] audit：`action=remagraph_store` 且 `status=stored`（成功）或 `error`+reason（拒絕）
- [ ] search 中文有合理命中；短 query 空 + warning
- [ ] **remagraph_status 同 task_id 多筆只回最新 1 筆**
- [ ] top_k=1 且有 2 筆 → `has_more=True`；僅 1 筆 → `has_more=False`
- [ ] 仲裁拒絕 reason_code 正確

---

## 7. 風險與緩解

| 風險 | 影響 | 緩解 |
|------|------|------|
| 模型下載失敗／CI 無網 | 去重不可用 | fail-fast tool 錯誤；mock 測邏輯；可選 nightly 真實模型 |
| SQLite 無 trigram | 中文 FTS 失敗 | WU-0/2 版本斷言；明確錯誤 |
| 0.90 誤判 | 誤拒／漏重 | 標待校準；不靜默改門檻 |
| MCP SDK 漂移 | 註冊失敗 | pin 上界；整合測 |
| 模型體積／延遲 | UX | lazy load；README 說明 |
| 範圍蔓延 | 拖期 | 非目標清單 |
| model2vec encode 格式變更 | BLOB 損壞 | pin 上界；維度 assert |
| audit 半行損壞 | 讀取失敗 | 完整序列化再 write；文件 trade-off |
| trigram 1–2 字元空回 | 難診斷 | 短 query 統一 warning 路徑 |

---

## 8. 交付物檢查清單（實作階段結束時）

- [ ] 三 MCP tool 可用（stdio）
- [ ] Audit Contract 可驗證
- [ ] coverage ≥80、CI 綠
- [ ] README 更新
- [ ] **不**自動 PyPI publish

---

## 9. 明確禁止事項

| 禁止 | 說明 |
|------|------|
| 將本計畫 APPROVE 視為開工令 | 仍需人類「同意實作」 |
| 假實作騙 coverage | 禁止 |
| 繞過 route() | 禁止 |
| 未再審核改重大架構 | 需再送 PPLX |

---

## 10. 修訂紀錄

| 版本 | 日期 | 說明 |
|------|------|------|
| v1.0-draft | 2026-07-21 | 初稿送 PPLX |
| v1.1 | 2026-07-21 | 依 PPLX 初審：B-1 維度鎖定、B-2 依賴圖／WU-7、B-3 status 去重驗收；A–F + 關鍵 N（has_more 演算法、短 query、fail-fast 語意、mutmut 70%、SQLite 3.38、audit 寫入、pin 上界） |
