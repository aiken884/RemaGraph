# PPLX 共識行動清單（設計修正）

- Date: 2026-07-21
- Verdict: **APPROVE_WITH_CHANGES**
- Source: `docs/design/reviews/pplx-design-review-2026-07-21.md`
- Orchestrated-by: CommandTower
- route(): `opencode-deepseek-pro` / `opencode-go/deepseek-v4-pro`

## 硬性規則

1. **只改設計文件**（`DESIGN.md`、`docs/design/**`、必要時 `docs/audit.md`、`pyproject.toml` 中的依賴宣告文字與設計註解）。
2. **禁止實作** `src/**` 功能碼、`tests/**` 測試本體（可讀）。
3. 語言：台灣式繁體中文。
4. 完成後在每個改過的檔案相關小節或文末附 `## PPLX-CONSENSUS-APPLIED` 與 checklist。

---

## Blocking（必須完成）

### B1 — 去重模型改多語言
- 全專案設計將 `potion-base-8M` → **`potion-multilingual-128M`**
- 宣告 v1 **支援中文**（與 DESIGN 範例一致）
- 更新 DESIGN.md 仲裁規則 #4、pyproject 依賴說明、D01/D02/D04 相關段
- 模型載入失敗：**fail-fast**（不靜默降級）

### B2 — FTS5 中文 tokenizer
- FTS5 DDL 使用 **`tokenize='trigram'`**（非預設 unicode61）
- 修正 D02「unicode61 是 bigram」之錯誤描述
- 修正 D05 Q6「品質可接受」之錯誤裁決
- 記載：若 runtime SQLite < 3.34/無 trigram 支援，降級方案為手動 bigram 前處理（文件層級）
- CI 驗收應包含「確認 SQLite 支援 trigram」之設計註記

### B3 — Transport：stdio 為主
- DESIGN.md 部署形態改為：**v1 主要 stdio**；Unix socket daemon 為進階／路線圖
- D03 全面改優先序：stdio first；socket optional
- D05 MCP 整合測試以 **stdio** 為主

---

## 矛盾裁決（已定案，必須寫入文件）

| ID | 裁決 |
|----|------|
| C1 | `handoff_note` ≥20 **僅** `task_handoff` 強制 |
| C2 | 修正 D02 CJK tokenizer 描述 |
| C3 | `remagraph_status` limit 預設 **20**、最大 **100** |
| C4 | `remagraph_search` top_k 預設 **20**、最大 **100** |
| C5 | 補 `memories_au` AFTER UPDATE trigger |
| C6 | DDL 補 **timestamp** 欄位（與 created_at 語意區分） |
| C7 | 見 B3 |
| C8 | StoreResponse 可含 `superseded` / `invalidated_count`（回寫 DESIGN 範例） |

## 開放問題裁決（寫入各設計檔「已裁決」小節）

- supersede **嚴格同 task_id**，v1 不跨 task
- invalidate **不做雙向**追溯
- dedup：同 kind active ≤2000 全量；超過取最新 2000
- embedding BLOB：`float32` little-endian `<f4`
- audit rotation：**DEFER v2**
- audit ts：**全 UTC Z**
- error 粒度：exception class name only
- v1 **單 process**（PID 鎖），不支援多實例共用 DB
- query="" → 空 results + warning，不拋錯
- summary 長度：`len(summary.strip())`
- search 回應：`has_more` 取代精確 `total_matches`（v1）
- FTS query **必須 sanitize**
- model2vec：stdio **lazy load**；若保留 daemon 則 eager（與 B3 一致時以 stdio lazy 為準）
- 去重門檻：v1 先用 **0.90**（標「待中文資料集校準」）；可選按 kind 分門檻記載為建議非強制
- TDD：先 `test_models.py`（設計層註記即可）
- mutmut 限縮 arbitration+dedup
- MCP SDK 依賴需寫入 DESIGN/pyproject 設計片段（`mcp`）

## Non-blocking 一併寫入（不另開票）

- N4 ts 精度：記憶 timestamp 到秒 vs audit 到毫秒 — 文件標注
- N9 sanitize 已含
- N10 mcp 依賴

---

## 建議檔案分工（可並行）

| 任務 | 主要檔案 |
|------|----------|
| DF1 | `DESIGN.md`（B1–B3 + C/R 回寫 + pyproject 片段） |
| DF2 | `docs/design/01-*.md` + `02-*.md` |
| DF3 | `docs/design/03-*.md` + `04-*.md` + `05-*.md` |
| DF4 | 新建 `docs/design/00-index.md`（索引 + 裁決總表 + 與 DESIGN 差異） |

完成定義：Blocking 全清、矛盾表已落文件、`00-index.md` 存在、全文無「v1 以 Unix socket 為主」舊敘述、全文無 `potion-base-8M` 作為 v1 選定模型。
