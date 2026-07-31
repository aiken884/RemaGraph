---
type: implementation-plan-review
date: 2026-07-21
reviewer: PPLX
---

# RemaGraph v1 實作計畫 — 請 PPLX 對抗式審查

## 背景
設計階段已凍結並經 PPLX 複審 APPROVE（commit 8905d23）。
本文件為**實作計畫 only**；使用者要求：審核到共識為止，**明確同意前禁止寫功能碼**。

## 請產出
1. Verdict: APPROVE / APPROVE_WITH_CHANGES / REJECT
2. Blocking issues（必須修才算計畫完成）
3. Non-blocking improvements
4. 與 DESIGN.md 凍結決策是否一致（尤其：stdio、multilingual-128M、trigram、0.90、has_more、handoff_note 僅 task_handoff）
5. 工作單元拆分／依賴圖／關鍵路徑是否合理；缺漏的 WU？
6. 驗證閘門是否足以防止「測試綠但行為錯」
7. 風險表是否足夠
8. 若 APPROVE_WITH_CHANGES：給出可直接改計畫的具體修訂條文

用台灣繁體中文。

## 實作計畫全文

# RemaGraph v1 實作計畫

| 項目 | 內容 |
|------|------|
| **狀態** | 計畫撰寫／待 PPLX 審核共識（**禁止實作**直至人類明確同意） |
| **Date** | 2026-07-21 |
| **SOT** | [`DESIGN.md`](../../DESIGN.md)（PPLX 設計共識已凍結，commit `8905d23`） |
| **設計展開** | [`docs/design/00-index.md`](../design/00-index.md) 及 D01–D05 |
| **設計複審** | [`docs/design/reviews/pplx-recheck-2026-07-21.md`](../design/reviews/pplx-recheck-2026-07-21.md) → **APPROVE** |

---

## 0. 硬性邊界（本文件與後續執行皆適用）

1. **本文件只規劃實作，不執行實作。** 進入寫碼／改 stub 行為前，必須另有使用者明確指示（例如「同意實作」）。
2. 實作必須對齊凍結設計；若與 `DESIGN.md` 衝突，**以 DESIGN.md 為準**並開 ADR／回報，不得靜默偏離。
3. **不耦合**任何具體外部指揮／治理系統（程式碼、README、tool 名稱皆不得綁定特定外部專案）。
4. 依賴新增限設計已列：`model2vec`、`mcp`、`pydantic`；optional `sqlite-vec` 僅 v2。
5. 派工紀律：實作由執行代理經路由政策派工；本計畫可被派工作為「實作藍圖」，**不是**授權立刻開工。

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
品質門檻：pytest 全綠、coverage ≥ 80、gitleaks 無發現；mutmut 追蹤 P0（arbitration + dedup）。

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
| 去重模型 | `potion-multilingual-128M`；載入 **fail-fast**；stdio **lazy load** |
| 去重門檻 | cosine ≥ **0.90**（待中文校準）；同 kind active ≤ **2000**（超出取最新 2000） |
| Embedding BLOB | float32 little-endian **`<f4>`**，v1 只存不查 |
| FTS5 | `tokenize='trigram'`；query **sanitize**；空 query → 空 results + warning |
| 仲裁 #1 | `len(summary.strip()) ≥ 30` |
| 仲裁 #3 | `handoff_note ≥ 20` **僅** `task_handoff` |
| agent_id | `^[a-z0-9_-]+$`，長度 3–64 |
| Search / status 預設 | `top_k`／`limit` 預設 **20**、最大 **100**；search 用 **`has_more`** 非精確 total |
| supersede | 嚴格同 `task_id` |
| 併發 | v1 單 process（stdio 序列化） |
| TDD 起手 | 先擴充 models + `test_models.py` |

詳細見 D01–D05 與 `00-index.md` §2。

---

## 3. 工作單元（Work Units）

原則：**依依賴由底向上**；可平行者標「∥」；每單元結束須通過該單元閘門後才合入下一層。

### WU-0 — 工程基線（∥ 可先做）

| 項目 | 內容 |
|------|------|
| 範圍 | 確認 `pyproject.toml` 依賴（`model2vec`、`mcp>=1.0`、`pydantic`、dev tools）；entry point（如 `remagraph` CLI／`python -m remagraph`）；ruff 設定；CI 確認 SQLite 支援 trigram 的檢查步驟（文件化或 job step） |
| 依賴 | 無 |
| 驗收 | editable install 成功；`python -c "import remagraph"`；CI workflow 不需在本 WU 全綠（核心仍 stub） |
| 對應設計 | DESIGN 部署／pyproject；D03 啟動參數；D05 CI |

### WU-1 — models（型別合約）

| 項目 | 內容 |
|------|------|
| 範圍 | 完整 `Memory`（含 timestamp／created_at／updated_at 等凍結欄位）、`StoreRequest`／`StoreResponse`、`SearchRequest`／`SearchResponse`（含 `has_more`）、kind／status literals |
| 依賴 | WU-0 |
| 驗收 | `tests/test_models.py` 覆蓋 D05 §2.1 M1–M7；TDD：先測後填 |
| 對應設計 | D01 §1；D03 tool schema；D05 §2.1 |

### WU-2 — db（連線、DDL、migration）

| 項目 | 內容 |
|------|------|
| 範圍 | state 目錄建立與權限；SQLite connect（WAL）；完整 DDL（含 `timestamp`、`memories_au`、FTS5 `trigram`、indexes）；`_meta` schema_version；冪等 init |
| 依賴 | WU-1（型別可選） |
| 驗收 | in-memory 或 temp dir：schema 存在、trigram tokenizer 可用（或明確 skip + 文件）；權限測試 |
| 對應設計 | D02 §1–3；D04 FS 初始化 |

### WU-3 — arbitration（規則 #1–#3、#5，無 embedding）

| 項目 | 內容 |
|------|------|
| 範圍 | 便宜規則：summary／learnings／handoff_note／agent_id；reason_code；順序 fail-fast |
| 依賴 | WU-1 |
| 驗收 | `test_arbitration.py` 單元、mock-free；覆蓋 A1–A3、A5–A6（門檻字數用凍結值） |
| 對應設計 | D01 §5–8；D05 §2.2 |

### WU-4 — dedup（規則 #4）

| 項目 | 內容 |
|------|------|
| 範圍 | model2vec `potion-multilingual-128M`；encode；cosine；同 kind active 載入；0.90；2000 上限；fail-fast 載入；`<f4>` bytes |
| 依賴 | WU-1、WU-2（讀既有 embedding） |
| 驗收 | mock 模型單元測 D2–D6；可選整合測真實模型（標 slow／可 skip CI 網路） |
| 對應設計 | D02 §6–7；D05 §2.5（**門檻以 0.90 為準**，若 D05 殘留 0.92 以 DESIGN／本計畫為準） |

### WU-5 — store + supersede／invalidate 編排

| 項目 | 內容 |
|------|------|
| 範圍 | 仲裁全通過後 insert；FTS 同步；status_update supersede；invalidates；transaction；回傳 StoreResponse；embedding 寫入 |
| 依賴 | WU-2、WU-3、WU-4 |
| 驗收 | D05 S1–S7；transaction 失敗無半成品 |
| 對應設計 | D01 §3–4；D02 §4；D05 §2.3 |

### WU-6 — search + status 查詢

| 項目 | 內容 |
|------|------|
| 範圍 | BM25 + trigram；sanitize；kind／status／tags 過濾；top_k／has_more；`remagraph_status` 語意（active status_update 去重） |
| 依賴 | WU-2、WU-5（需有資料） |
| 驗收 | D05 R1–R7；中文 query；空 query；limit 預設／上限 |
| 對應設計 | D02 §5；D03 tool；D05 §2.4 |

### WU-7 — audit

| 項目 | 內容 |
|------|------|
| 範圍 | audit.jsonl append；schema 欄位；stored／error；no traceback；權限；best-effort 不回滾 DB |
| 依賴 | WU-5 編排點（commit 後寫 audit） |
| 驗收 | D05 U1–U8；合約與 `docs/audit.md` 一致 |
| 對應設計 | D04；`docs/audit.md` |

### WU-8 — server（MCP stdio）

| 項目 | 內容 |
|------|------|
| 範圍 | `mcp` SDK stdio；註冊三 tool；錯誤映射；lazy load 模型；優雅關閉；日誌（無 secret） |
| 依賴 | WU-5、WU-6、WU-7 |
| 驗收 | MCP 整合測（stdio）；tool 契約符合 D03 |
| 對應設計 | D03；D05 server 節 |

### WU-9 — 測試補強與 CI 對齊

| 項目 | 內容 |
|------|------|
| 範圍 | 補齊矩陣；coverage ≥ 80；mutmut 限縮 arbitration+dedup（非阻塞追蹤）；ruff；gitleaks 既有 |
| 依賴 | WU-1–WU-8 |
| 驗收 | 本地與 CI matrix 綠；mutmut 報告產物可選上傳 |
| 對應設計 | D05；DESIGN CI 章節 |

### WU-10 — 文件與發佈準備（非 release）

| 項目 | 內容 |
|------|------|
| 範圍 | README 安裝／MCP config（stdio）；CHANGELOG 草稿；確認邊界無外部具名系統耦合 |
| 依賴 | WU-8 |
| 驗收 | README 可複製即用；**不**自動 publish |
| 對應設計 | DESIGN 部署；D03 config 範例 |

---

## 4. 依賴圖（摘要）

```
WU-0 ─┬─► WU-1 ─► WU-3 ─┐
      │                 ├─► WU-5 ─► WU-6 ─┐
      └─► WU-2 ─► WU-4 ─┘                 ├─► WU-8 ─► WU-9 ─► WU-10
                              WU-7 ───────┘
```

- **可平行**：WU-3 與 WU-2／WU-4 在 models 穩定後可平行；WU-7 可與 WU-6 部分平行（介面先定）。
- **關鍵路徑**：WU-1 → WU-2 → WU-4 → WU-5 → WU-8 → WU-9

---

## 5. 建議派工與路由提示（執行時，非現在）

| 單元類型 | task_type | sensitivity 建議 | 說明 |
|----------|-----------|------------------|------|
| 清晰轉譯（models／db DDL） | implement | S0–S1 | 設計已詳，偏機械 |
| 仲裁／去重核心 | implement | S1 | 正確性敏感 |
| MCP server 整合 | implement | S1 | 協定邊界 |
| 對抗式審查 | review／audit | S1–S2 | 每 WU 完成後四眼；**route() 選 tier，禁止手挑** |

並行：無檔案衝突的 WU 可多 agent 同模型多實例；共享檔案用 worktree 隔離。

---

## 6. 驗證閘門（每 WU 與 pre-merge）

### 6.1 每 WU 完成

- [ ] 對應 D05 驗收列通過（單元／整合）
- [ ] 無外部具名系統耦合字串進入 src／公開文件（除歷史禁止宣告）
- [ ] 不引入未核准依賴
- [ ] orchestrator／審查者 guilty-until-proven（測試綠 ≠ 自動通過）

### 6.2 Pre-merge（對 main）

| Gate | 條件 |
|------|------|
| lint | ruff exit 0 |
| tests | pytest 全綠 |
| coverage | `--cov=src/remagraph --cov-fail-under=80` |
| secret | gitleaks 無 findings |
| dep | pip-audit 無未處理 HIGH/CRITICAL（或已記錄豁免） |
| mutation | mutmut 對 arbitration+dedup **追蹤**（v1 非硬阻斷，但不得默默刪除） |
| design alignment | 抽樣對照本計畫 §2 凍結表 |

### 6.3 內容驗收（防「exit 0 但半殘」）

成功判定必須驗 **產出內容**，例如：

- store 後 SQLite 可讀到預期欄位與 FTS 可查
- audit.jsonl 出現 `action=remagraph_store` 且 `status=stored`
- search 中文 query 有合理命中（trigram）
- 仲裁拒絕回傳正確 reason_code

---

## 7. 風險與緩解

| 風險 | 影響 | 緩解 |
|------|------|------|
| `potion-multilingual-128M` 下載失敗／CI 無網 | 去重無法跑 | fail-fast；CI 用 mock 模型測邏輯 + 可選 nightly 真實模型 |
| SQLite 無 trigram（過舊） | 中文 FTS 失敗 | WU-2 偵測；文件降級 bigram 前處理（v1 可不實作自動降級，但必須明確錯誤） |
| 0.90 門檻誤判 | 誤拒／漏重 | 標待校準；記錄 false positive 案例；不在 v1 靜默改門檻 |
| MCP SDK 版本漂移 | tool 註冊失敗 | pin `mcp>=1.0,<下一個 major`；整合測鎖行為 |
| 模型體積／首次延遲 | UX | stdio lazy load；文件說明首次下載 |
| 範圍蔓延（socket daemon、vector） | 拖期 | 非目標清單強制；PR 檢查 |

---

## 8. 交付物檢查清單（實作階段結束時）

- [ ] 三 MCP tool 可用（stdio）
- [ ] state 目錄與 audit 合約可被外部以 task_id 驗證
- [ ] coverage ≥ 80、CI 綠
- [ ] README 更新
- [ ] **仍不**自動 PyPI publish（HITL）

---

## 9. 明確：目前禁止事項

| 禁止 | 說明 |
|------|------|
| 將本計畫 APPROVE 視為開工令 | 仍需人類「同意實作」 |
| 修改 stub 為「假實作」騙過 coverage | 禁止 |
| 繞過 route() 手挑實作模型 | 禁止 |
| 在未審核共識前改本計畫重大架構 | 需再送 PPLX |

---

## 10. 修訂紀錄

| 版本 | 日期 | 說明 |
|------|------|------|
| v1.0-draft | 2026-07-21 | 初稿，送 PPLX 審核 |

## Search queries triggered

- model2vec potion-multilingual-128M embedding model specs 2025
- MCP SDK stdio transport tool registration 2025
- SQLite FTS5 trigram tokenizer requirements version
## Output

# RemaGraph v1 實作計畫 — PPLX 對抗式審查報告

**審查日期**：2026-07-21
**審查基準**：DESIGN.md 凍結決策（commit 8905d23）、D01–D05、本計畫全文、外部技術查證結果

---

## 1. Verdict

> ### ⚠️ APPROVE_WITH_CHANGES

計畫整體架構健全、與凍結設計對齊程度高，但存在 **3 項 Blocking issues** 必須修正後方可開工，另有 **9 項 Non-blocking improvements** 建議納入。

---

## 2. Blocking Issues（必修，否則計畫不完整）

### B-1｜embedding 維度未明文鎖定，BLOB 大小可被誤算

**問題**：計畫 §2 凍結表僅記「`<f4>` float32 little-endian」，WU-4 說「`<f4>` bytes」，但從未明文寫下 **256 維**（已查證：`potion-multilingual-128M` 輸出維度為 256-dim，見 HuggingFace Model Card）。

實作者若未查原始設計，可能寫出維度錯誤的 `struct.pack`、BLOB 長度驗收、或未來 sqlite-vec schema。1 個 vector = **256 × 4 = 1024 bytes**，應明文寫入凍結表與 WU-4 驗收條件。

> ⚠️ 另注：pub.dev Dart 實作顯示 `potion-multilingual-128M` 維度為 768；HuggingFace 官方頁面顯示 256。**計畫必須明確引用 Python `model2vec` 官方輸出維度並加以鎖定（建議加一行 assert 測試），不得有歧義。**

**修訂條文**：
```
凍結表新增一列：
| Embedding 維度 | 256-dim float32（`<f4>`），BLOB = 1024 bytes；
|                | WU-4 必含 assert len(emb) == 256 單元測試 |
```

---

### B-2｜WU-7（audit）依賴關係圖錯置，導致「DB commit 前寫 audit」風險

**問題**：依賴圖（§4）中 `WU-7 ───────►` 的箭頭接往 `WU-8`，與 §WU-7 文字說明「commit 後寫 audit」矛盾。圖面上 WU-7 看似獨立、與 WU-5 無直接箭頭，實際上 WU-7 **必須 depends on WU-5**（需要 store 的 transaction commit 結果），否則實作者可能在 transaction 尚未 commit 時就寫入 `status=stored`，造成資料不一致。

**修訂條文**：
```
依賴圖修正：
WU-5 ──► WU-7（audit 必在 WU-5 transaction commit 後觸發）

文字補充：「WU-7 depends on WU-5；
audit append 必須發生在 SQLite transaction.commit() 成功之後；
commit 拋出例外時，audit 寫 action=remagraph_store, status=error。」
```

---

### B-3｜`remagraph_status` 的「去重語意」未在 WU-6 驗收條件中被可測試化

**問題**：WU-6 驗收條件僅寫「D05 R1–R7；中文 query；空 query；limit 預設／上限」，但 `remagraph_status` 的核心語意——**同 `task_id` 的多筆 `status_update` 只取最新一筆（active）**——沒有出現在任何 WU 的「內容驗收」條件中。§6.3 的「防半殘」範例也沒有列出這條。

若沒有明確測試「同 task_id 插入 3 筆 status_update → status 只回 1 筆最新」，coverage 100% 也可能通過但行為完全錯誤。

**修訂條文**：
```
WU-6 驗收補充：
- [ ] 同 task_id 有 n≥3 筆 active status_update → remagraph_status 只回最新 1 筆
- [ ] task_id 有 superseded + active 混合 → 只算 active
- [ ] limit=1 + has_more 語意正確（存在第 2 筆時 has_more=true）

§6.3 內容驗收補充：
- remagraph_status 對同 task_id 多筆去重，結果筆數符合預期
```

---

## 3. Non-blocking Improvements（建議，不改也可開工但有技術債）

### N-1｜trigram 最短查詢長度限制未說明

FTS5 trigram tokenizer 需要查詢字串 **≥ 3 個字元**（或 3 個字節），1–2 字元的中文查詢（如單字詞「愛」）會靜默返回空結果而非報錯。計畫 §2 凍結表及 WU-6 均未提及此邊界。建議 WU-6 驗收加入：「1 字元 / 2 字元中文 query → 回傳空 results + warning（同空 query 處理路徑）」，並在 README 文件化。

### N-2｜`has_more` 的計算方式未指定，實作者可能有三種理解

`has_more` 的正確實作方式是「**查 top_k+1 筆，若有第 top_k+1 筆則 has_more=true，只回前 top_k**」，但計畫中從未寫明此演算法。若實作者用 `COUNT(*)` 或其他方式，行為不一致且浪費 I/O。建議在凍結表或 WU-6 明文規定。

### N-3｜fail-fast 載入的定義不夠具體（「何時」fail-fast？）

計畫寫「載入 fail-fast；stdio lazy load」，但沒有說明：
- lazy load 的觸發點是**第一次 store 呼叫**？還是 server 啟動後第一次呼叫任何 tool？
- fail-fast 是指「載入失敗時 raise 並讓整個 server 退出」，還是「回傳 tool-level error 讓 MCP caller 知道」？

stdio MCP 下 server 崩潰對 host 影響很大，建議 WU-4 / WU-8 明文指定：「模型載入失敗 → tool 回傳結構化錯誤，server **不** 崩潰退出；但若連續 N 次失敗則 log CRITICAL」。

### N-4｜`agent_id` regex 未涵蓋大小寫拒絕的測試案例

凍結：`^[a-z0-9_-]+$`（小寫）。WU-3 驗收未明列「Agent_ID（含大寫）→ 拒絕」測試。建議 WU-3 加：`test_agent_id_uppercase_rejected`。

### N-5｜supersede 的「嚴格同 task_id」在跨 kind 情境的拒絕行為未測試

凍結說 supersede 限同 `task_id`，但沒有說明「同 task_id、不同 kind 的 supersede 是否允許」。若設計意圖是同 task_id + 同 kind，WU-5 的驗收應補一條負面測試。

### N-6｜WU-9 的 mutmut 範圍「非阻斷」論述可能造成工程紀律鬆弛

計畫寫「mutmut 非硬阻斷，但不得默默刪除」，但沒有定義「可接受的 mutation score 下界」或「何種 mutant 逃逸算可接受」。建議補充：「arbitration + dedup 的 mutation score 目標 ≥ 70%（v1 追蹤用），低於此值需在 PR 說明原因，不得靜默合入」。

### N-7｜CI 環境的 SQLite 版本下界未鎖定

trigram tokenizer 要求 SQLite **≥ 3.38.0**（2022-02-22 發布）。計畫 §7 風險表提到「過舊 SQLite」但未在 WU-0 / WU-2 的 CI 設定中加入版本檢查步驟（如 `python -c "import sqlite3; assert sqlite3.sqlite_version_info >= (3,38,0)"`）。WU-0 的 entry「CI 確認 SQLite 支援 trigram 的檢查步驟」算有提到，但未給出具體斷言，建議補齊。

### N-8｜`audit.jsonl` 的 `best-effort 不回滾 DB` 需要冪等保護

若 audit append 在寫到一半時 crash（例如 JSON 行不完整），下次讀取會得到損壞的 jsonl。建議 WU-7 明文要求：「每筆 audit 為完整單行 JSON + `\n`，append 前先在記憶體序列化完成再 write，降低半行風險；v1 不需 fsync，但文件說明此 trade-off」。

### N-9｜WU-10 缺少「tool schema 版本聲明」驗收

README 的 MCP config 範例若未標明 tool schema 版本，未來改動 schema 時難以做 host 端相容性管理。建議 WU-10 加：「README 中 tool 範例須包含 `_schema_version` 或等效標記，或明文說明 v1 不做版本化（並記為 v2 技術債）」。

---

## 4. 與 DESIGN.md 凍結決策一致性核查

| 凍結項目 | 計畫中的對應 | 判定 |
|---|---|---|
| **stdio 優先** | §1.1、WU-8、WU-10 均以 stdio 為主；socket 列非目標 | ✅ 一致 |
| **multilingual-128M** | §2 凍結表、WU-4 均明文指定 | ✅ 一致，但維度未鎖定（→ B-1） |
| **trigram** | §2 凍結表、WU-2 DDL 範圍、WU-6 驗收 | ✅ 一致，但 1–2 字元邊界未處理（→ N-1） |
| **0.90 門檻** | §2 凍結表、WU-4 明文；並標「D05 殘留 0.92 以 DESIGN 為準」 | ✅ 一致，主動點名潛在矛盾值得肯定 |
| **has_more（非 total）** | §1.1 search 欄、§2 凍結表 | ✅ 一致，但計算演算法未定義（→ N-2） |
| **handoff_note ≥ 20 僅 task_handoff** | §2 凍結表仲裁 #3、WU-3 範圍 | ✅ 一致 |
| **`<f4>` BLOB，v1 只存不查** | §2 凍結表、WU-4 | ✅ 一致，但維度數字缺失（→ B-1） |
| **supersede 嚴格同 task_id** | §2 凍結表、WU-5 | ✅ 一致，跨 kind 測試缺（→ N-5） |
| **top_k / limit 預設 20 最大 100** | §2 凍結表、WU-6 | ✅ 一致 |
| **agent_id 格式** | §2 凍結表、WU-3 | ✅ 一致，大寫拒絕測試缺（→ N-4） |

**總體**：10 項凍結決策全數在計畫中有對應，無靜默偏離。主動標注「D05 殘留 0.92 以 DESIGN 為準」是正確做法，值得保留。

---

## 5. 工作單元拆分、依賴圖、關鍵路徑審查

### 5.1 拆分合理性

整體由底向上、依賴清晰，WU-0 至 WU-10 的層次結構符合「先型別、再資料層、再業務層、再 I/O 層」的標準順序。

### 5.2 依賴圖問題

```
現行圖（有問題）：
WU-7 ───────┘  ← 看起來像獨立路徑，接 WU-8

正確應為：
WU-5 ──► WU-7 ──► WU-8
```

→ 已列為 **B-2**。

### 5.3 關鍵路徑確認

`WU-1 → WU-2 → WU-4 → WU-5 → WU-8 → WU-9` 正確。

然而關鍵路徑中 **WU-4（dedup）** 是最高風險節點：依賴網路下載模型、依賴正確的 embedding 維度、依賴 WU-2 的 BLOB 讀取。建議在關鍵路徑標注此風險（目前 §7 風險表有，但圖中沒有標示）。

### 5.4 缺漏的 WU

| 缺漏項目 | 影響 | 建議 |
|---|---|---|
| **FTS query sanitize 的獨立單元** | 目前 sanitize 混在 WU-6 中，但它是安全邊界，應有獨立可測試函式 | 可在 WU-6 內明文列為子任務，不需新 WU |
| **schema migration 測試** | WU-2 提到 `_meta schema_version` 但沒有驗收「舊版 DB upgrade 路徑」 | v1 若從無到有則不需 migration，但需驗收「已存在 DB 重啟不 re-init」 |
| **model cache 路徑管理** | `potion-multilingual-128M` 首次下載到哪裡、如何快取，未在任何 WU 中處理 | 建議 WU-4 加：「document model cache dir（HuggingFace default cache 或環境變數覆蓋）」 |

---

## 6. 驗證閘門充分性審查

### 優點

- §6.1 的「guilty-until-proven」原則明確，是對抗「測試綠但行為錯」的正確態度。
- §6.3 的內容驗收範例（store 後 SQLite 可讀、audit 可查、中文 trigram）是有效的行為驗證。
- gitleaks + pip-audit 雙重 secret/dep 掃描合理。

### 不足之處

| 缺口 | 說明 |
|---|---|
| **status 去重行為無內容驗收** | 已列 B-3 |
| **audit 錯誤路徑無內容驗收** | §6.3 只列 happy path；應補「仲裁拒絕 → audit 有 status=error + reason_code」 |
| **embedding BLOB 格式無內容驗收** | 應補「讀出 BLOB 後 np.frombuffer(blob, dtype='<f4').shape == (256,)」 |
| **has_more 邊界無內容驗收** | 應補「top_k=1 且有 2 筆結果 → has_more=True；top_k=1 且只有 1 筆 → has_more=False」 |
| **mutation score 無下界** | 已列 N-6 |

---

## 7. 風險表充分性審查

現有 6 條風險，覆蓋核心場景。建議補充：

| 新增風險 | 影響 | 緩解建議 |
|---|---|---|
| **model2vec API 在新版本改變 encode() 輸出格式** | embedding 維度或型別改變，BLOB 靜默損壞 | pin `model2vec` 版本上界；WU-4 加維度 assert |
| **audit.jsonl 行寫入中途 crash（SIGKILL）** | jsonl 末行損壞，讀取時 json.loads 拋出 | 已列 N-8，此處列為風險條目 |
| **FTS5 trigram 1–2 字元中文 query 靜默空回** | 使用者體驗差，難以診斷 | 已列 N-1，此處列為風險條目 |

---

## 8. APPROVE_WITH_CHANGES 修訂條文（可直接貼入計畫）

以下為可直接修改計畫文件的條文，按章節位置排列：

---

### 修訂 A｜§2 凍結決策摘要表新增一列

```markdown
| Embedding 維度 | **256-dim**（`potion-multilingual-128M` Python model2vec 輸出）；
|                | BLOB = 256 × 4 = **1024 bytes**（`<f4>` little-endian）；
|                | WU-4 必含 `assert embedding.shape == (256,)` 單元測試 |
```

---

### 修訂 B｜WU-4 驗收條件補充

```markdown
驗收（補充）：
- [ ] `assert embedding.shape == (256,)`（防模型版本漂移）
- [ ] BLOB bytes 長度 == 1024
- [ ] document model cache dir 與環境變數覆蓋方式（如 `HF_HOME`）
```

---

### 修訂 C｜WU-6 驗收條件補充

```markdown
驗收（補充）：
- [ ] 同 task_id 插入 n≥3 筆 active status_update → status 回傳僅最新 1 筆
- [ ] task_id 含 superseded + active 混合 → 僅計 active
- [ ] top_k=N，結果 > N → has_more=True；結果 ≤ N → has_more=False
  （實作：查 top_k+1 筆，取前 top_k 回傳，有第 top_k+1 筆則 has_more=true）
- [ ] 1 字元 / 2 字元中文 query → 回傳空 results + warning（同空 query 處理路徑）
```

---

### 修訂 D｜§4 依賴圖修正

```
修正後：
WU-0 ─┬─► WU-1 ─► WU-3 ─────────────┐
      │                              ├─► WU-5 ─┬─► WU-6 ─┐
      └─► WU-2 ─► WU-4 ─────────────┘         │         ├─► WU-8 ─► WU-9 ─► WU-10
                                               └─► WU-7 ─┘

說明：WU-7 depends on WU-5（audit 在 transaction commit 後寫入）
```

---

### 修訂 E｜§6.3 內容驗收補充

```markdown
- [ ] 仲裁拒絕 → audit.jsonl 有 `status=error` + 正確 `reason_code`
- [ ] store 成功 → 讀出 BLOB，`np.frombuffer(blob, '<f4>').shape == (256,)`
- [ ] remagraph_status 同 task_id 多筆去重，回傳筆數符合預期
- [ ] top_k=1 且有 2 筆資料 → `has_more=True`
```

---

### 修訂 F｜§7 風險表新增三列

```markdown
| model2vec encode() 輸出格式在版本升級後改變 | BLOB 靜默損壞 | pin `model2vec` 版本上界（如 `<2.0`）；WU-4 加維度 assert |
| audit.jsonl 在 SIGKILL 時末行損壞 | json.loads 拋出，審計讀取失敗 | 每筆先在記憶體完整序列化再 write；文件說明此 trade-off |
| FTS5 trigram 1–2 字元查詢靜默空回 | 中文單字搜尋永遠無結果，難診斷 | WU-6 加邊界測試；search handler 偵測短 query 並回傳 warning |
```

---

## 9. 最終結論

| 面向 | 評分（5 分制） | 說明 |
|---|---|---|
| 凍結設計對齊 | 4.5 / 5 | 全數對應，主動點名潛在矛盾，僅維度數字缺漏 |
| WU 拆分合理性 | 4.0 / 5 | 層次清晰，依賴圖有一處錯置（B-2） |
| 驗證閘門充分性 | 3.5 / 5 | 有 guilty-until-proven 意識但核心行為驗收有缺口 |
| 風險表覆蓋 | 4.0 / 5 | 主要風險有覆蓋，需補 3 條 |
| 計畫可執行性 | 4.0 / 5 | 修訂 A–F 落地後可直接開工 |

**完成修訂 A–F（處理 B-1、B-2、B-3）後，升級為 APPROVE。N-1 至 N-9 建議在對應 WU 開工前納入，不構成阻斷條件。**

## Citations

- https://github.com/MinishLab/model2vec
- https://huggingface.co/minishlab/potion-multilingual-128M?library=model2vec
- https://www.directia.fr/modele-minishlab-potion-multilingual-128m/
- https://huggingface.co/777Radik/potion-multilingual-128M-int8
- https://pub.dev/packages/model2vec
- https://huggingface.co/minishlab/potion-multilingual-128M/tree/53456a9dd92e5ca495eda2675d3c0b9f92eaaff9
- https://huggingface.co/minishlab/potion-multilingual-128M/commit/c31260ae140c83f3e93dfa9ead80fca7f909e5f6
- https://huggingface.co/alikia2x/potion-multilingual-128M-int8
- https://zenn.dev/kanseilink/articles/kanseilink-fts5-trigram-cjk-20260507?locale=en
- https://davidmuraya.com/blog/sqlite-fts5-trigram-name-matching/
