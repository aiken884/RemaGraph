# T-RG-D00：RemaGraph 設計文件索引

> **艦隊任務 ID**：`T-RG-D00`
> **狀態**：設計文件 2026-07-21 凍結；**v1 實作已落地**；**v2 治理/安全/可靠度/CLI 強化已實作**（0.2.0，見 [`docs/reviews/v2-release-prep.md`](../reviews/v2-release-prep.md)）；發行前準備進行中（已有真實使用場景實際運作中，暫不公開發布）
> **用途**：索引全部設計文件、記錄 PPLX 共識裁決完成狀態、追蹤各文件與 `DESIGN.md` 的差異

---

## 1. 設計文件索引

| 文件 | 艦隊 ID | 範圍 | 狀態 |
|------|---------|------|------|
| [`DESIGN.md`](../../DESIGN.md) | — | 總體設計規格書（SOT） | 共識裁決已回寫（見 DF1） |
| `docs/design/00-index.md` | T-RG-D00 | 本文件：索引 + 裁決狀態追蹤 | ✅ 完成 |
| `docs/design/01-data-model-arbitration.md` | T-RG-D01 | 記憶 Schema、三種 kind、五條仲裁規則、錯誤碼表 | 共識裁決已回寫（見 DF2） |
| `docs/design/02-storage-search-dedup.md` | T-RG-D02 | SQLite + FTS5 Schema、查詢邏輯、去重、embedding BLOB | 共識裁決已回寫（見 DF2） |
| `docs/design/03-mcp-server-runtime.md` | T-RG-D03 | MCP Tool JSON Schema、部署形態（stdio 優先）、生命週期、錯誤映射、日誌規範 | 共識裁決已回寫（見 DF3） |
| `docs/design/04-audit-security.md` | T-RG-D04 | Audit Contract、威脅模型、依賴面分析、secret 紀律 | 共識裁決已回寫（見 DF3） |
| `docs/design/05-test-acceptance.md` | T-RG-D05 | 驗收條件、測試矩陣、coverage/mutmut 策略、邊界案例 | 共識裁決已回寫（見 DF3） |

---

## 2. PPLX 共識裁決總表（2026-07-21）

來源：`docs/design/reviews/pplx-consensus-actions-2026-07-21.md`
審查報告：`docs/design/reviews/pplx-design-review-2026-07-21.md`
裁決：**APPROVE_WITH_CHANGES**

### 2.1 Blocking 項目（全部已完成）

| ID | 項目 | 狀態 | 寫入文件 |
|----|------|:----:|----------|
| B1 | 去重模型 `potion-base-8M` → `potion-multilingual-128M`，宣告 v1 支援中文，fail-fast | ✅ | DESIGN.md, D01, D02, D04 |
| B2 | FTS5 `tokenize='trigram'`（非 unicode61），修正「bigram」錯誤描述，降級方案記載，CI trigram 檢查 | ✅ | D02, D05 |
| B3 | Transport：v1 以 stdio 為主；Unix socket daemon 為進階／路線圖 | ✅ | DESIGN.md, D03, D05 |

### 2.2 矛盾裁決（全部已寫入）

| ID | 裁決內容 | 狀態 | 寫入文件 |
|----|---------|:----:|----------|
| C1 | `handoff_note` ≥ 20 僅 `task_handoff` 強制 | ✅ | D01 |
| C2 | 修正 D02 CJK tokenizer 描述 | ✅ | D02 |
| C3 | `remagraph_status` limit 預設 20、最大 100 | ✅ | DESIGN.md, D03 |
| C4 | `remagraph_search` top_k 預設 20、最大 100 | ✅ | DESIGN.md, D03 |
| C5 | 補 `memories_au` AFTER UPDATE trigger | ✅ | D02 |
| C6 | DDL 補 timestamp 欄位（與 created_at 語意區分） | ✅ | D02 |
| C7 | Transport：stdio 為主（見 B3） | ✅ | D03 |
| C8 | StoreResponse 可含 `superseded` / `invalidated_count` | ✅ | DESIGN.md, D03 |

### 2.3 開放問題裁決（全部已寫入各設計檔「已裁決」小節）

| 裁決內容 | 寫入文件 |
|---------|----------|
| supersede 嚴格同 task_id，v1 不跨 task | D01 |
| invalidate 不做雙向追溯 | D01 |
| dedup：同 kind active ≤ 2000 全量；超過取最新 2000 | D02 |
| embedding BLOB：`float32` little-endian `<f4` | D02 |
| audit rotation：DEFER v2 | D04 |
| audit ts：全 UTC Z | D04 |
| error 粒度：exception class name only | D04 |
| v1 單 process（PID 鎖），不支援多實例共用 DB | D03, D04 |
| query="" → 空 results + warning，不拋錯 | D03, D05 |
| summary 長度：`len(summary.strip())` | D01 |
| search 回應：`has_more` 取代精確 `total_matches`（v1） | D03 |
| FTS query 必須 sanitize | D03, D05 |
| model2vec：stdio lazy load；daemon 則 eager | D03 |
| 去重門檻：v1 先用 0.90（標「待中文資料集校準」） | D02 |
| TDD：先 `test_models.py`（設計層註記） | D05 |
| mutmut 限縮 arbitration + dedup | D05 |
| MCP SDK 依賴（`mcp>=1.0.0`） | DESIGN.md, D03 |

---

## 3. Blocking 完成定義驗證

| 驗證條件 | 狀態 |
|---------|:----:|
| 全文無「v1 以 Unix socket 為主」舊敘述 | ✅ |
| 全文無 `potion-base-8M` 作為 v1 選定模型 | ✅ |
| 矛盾表已落文件 | ✅（本文件 §2.2 + 各設計檔 §「已裁決」） |
| `00-index.md` 存在 | ✅ |
| 各改過檔案文末有 `## PPLX-CONSENSUS-APPLIED` | ✅ |

---

## 4. 與 DESIGN.md 的差異摘要

以下為各設計文件相對於 `DESIGN.md` 總體規格書的新增或細化內容。

### 4.1 D01（資料模型與仲裁）

- 細化：完整記憶 Schema 欄位表（14 欄位含型別、約束、範例）
- 細化：三種 kind 的生命週期狀態機（含 supersede / invalidate）
- 細化：五條仲裁規則的完整驗證邏輯（含邊界值）
- 擴充：8 個錯誤碼的完整定義（reason_code + detail 格式）
- 新增：`timestamp` 欄位與 `created_at` 語意區分（C6）
- 新增：Lazy Registration 的實作細節
- 裁決：supersede 僅限同 task_id、invalidate 不做雙向追溯

### 4.2 D02（儲存與搜尋）

- 細化：完整 DDL（含 `memories_au` AFTER UPDATE trigger、`timestamp` 欄位）
- 修正：FTS5 tokenizer 從 `unicode61` 改為 `trigram`（支援中文）
- 新增：embedding BLOB 格式規範（`float32` little-endian `<f4`）
- 新增：dedup 策略詳述（同 kind active ≤ 2000 全量、相似度門檻 0.90 待校準）
- 細化：BM25 排名機制與 `matched_fields` 計算
- 裁決：去重相似度門檻 0.90（標「待中文資料集校準」）

### 4.3 D03（MCP Server 執行期）

- **重大變更**：部署形態從「Unix socket daemon」改為「**stdio 優先**」（B3）
- 細化：三個 MCP tool 的完整 JSON Schema（含所有欄位、型別、約束、回傳範例）
- 變更：`total_matches` → `has_more`（v1 裁決）
- 新增：FTS query sanitize 要求
- 新增：MCP SDK 依賴（`mcp>=1.0.0`）
- 新增：model2vec lazy load 策略（stdio 模式）
- 新增：lifecycle、PID 鎖、日誌規範、錯誤映射、優雅關閉
- 細化：啟動參數、環境變數、MCP config 範例

### 4.4 D04（審計與安全）

- 細化：audit.jsonl 完整 Schema（含 Python dataclass、欄位表）
- 細化：Audit Contract 合約邊界（含消費方 grep 範例）
- 細化：不回存 traceback 原則（含程式碼範例）
- 新增：威脅模型（T1–T5）
- 新增：依賴面分析（model2vec 供應鏈風險、pip-audit）
- 新增：檔案系統安全初始化流程
- 裁決：fail-fast 模型載入（不靜默降級）
- 裁決：audit rotation DEFER v2、ts 全 UTC Z、v1 單 process

### 4.5 D05（測試與驗收）

- 細化：8 個模組 64 條驗收條件
- 細化：測試矩陣（單元／DB 整合／MCP 整合／FS 整合，58 個最低案例）
- 細化：coverage ≥ 80 模組覆蓋策略（含不可犧牲清單）
- 細化：mutmut P0–P3 分級與 CI 行為
- 擴充：56 個邊界案例（分 5 類）
- 細化：CI workflow 建議（test.yml 補強 + mutmut.yml + 門檻總覽）
- 裁決：FTS5 `trigram` tokenizer（修正「unicode61 是 bigram」錯誤描述）
- 裁決：TDD 先 `test_models.py`、mutmut 限縮 arbitration + dedup
- 裁決：MCP 整合測試以 stdio 為主

### 4.6 設計文件與 DESIGN.md 的互動規則

1. **DESIGN.md 為 SOT**：所有設計文件的最終權威來源。若發現矛盾，以 DESIGN.md 為準並回報
2. **設計文件是展開**：`docs/design/` 下的文件將 DESIGN.md 中一句話的設計意圖展開為具體實作規格
3. **裁決以 DESIGN.md 為最終記錄**：共識裁決的結論已同步回寫 DESIGN.md（DF1），設計文件記錄裁決過程與理由
4. **「已裁決」小節為過渡機制**：各設計文件中的 §「已裁決」記錄 PPLX 審查期間的決策；未來若進入 stable release，這些裁決應整併回主文

---

## 5. 維護指引

- **新增設計文件時**：在本文件 §1 新增索引條目；在 §4 新增差異摘要
- **PPLX 審查後**：更新 §2 裁決總表；各文件 §「已裁決」；本文件 §3 完成定義驗證
- **修改 DESIGN.md 時**：檢查各設計文件是否需要同步更新；在本文件 §4 記錄變更
- **裁決過渡完成後**（stable release）：各文件 §「已裁決」內容整併回主文，本文件 §2 可簡化為 release note 參考

---

## PPLX-CONSENSUS-APPLIED

本文件為新建（2026-07-21），無需額外 checklist。以下為本文件包含的 PPLX 共識資訊：

- [x] 五份設計文件完整索引（§1）
- [x] PPLX 共識裁決總表：Blocking 3 項（全部已完成）+ 矛盾裁決 8 項 + 開放問題裁決 16 項（§2）
- [x] Blocking 完成定義驗證（§3）：4 項條件全部通過
- [x] 與 DESIGN.md 差異摘要：6 份文件的變更總覽（§4）
