---
type: design-review
status: approve_with_changes
date: 2026-07-21
reviewer: PPLX/claude-sonnet-4-6
orchestrated_by: CommandTower
---

# RemaGraph 設計階段 — PPLX 對抗式審查報告

> 審查基準：DESIGN.md（SOT）× 5 份設計文件（D01–D05）
> 審查時間：2026-07-21
> 審查者：PPLX（外部工具輔助）

---

## 1. Verdict

### **APPROVE_WITH_CHANGES**

設計整體品質高，結構清晰，跨文件內部一致性良好，邊界設計（不耦合 herdr-*）執行徹底。但有 **3 個 Blocking Issues** 必須在設計文件定案前解決，另有數個值得修正的非阻塞問題。

---

## 2. Blocking Issues（必須修才算設計完成）

### 🔴 B1：`potion-base-8M` 明確不支援 CJK——去重核心功能對中文無效

**來源**：D01 §11 Q1、D02 §12 Q1

**問題**：根據官方 model2vec README，`potion-base-8M` 是**純英文模型**（Distilled from `bge-base-en-v1.5`，English only）。DESIGN.md 與 D01 均以此模型作為仲裁規則 #4（cosine ≥ 0.92 去重）的唯一實作。

RemaGraph 的使用情境是**中文 summary + learnings**（所有 DESIGN.md 範例均為中文），用英文 embedding 模型處理中文文字，cosine similarity 的分佈會嚴重失真：
- 不相關的中文句子可能因詞彙表外（OOV）問題而得到接近的向量
- 語意相近的中文句子反而可能相似度偏低
- 0.92 門檻在中文語境下**完全缺乏校準依據**

**可用替代方案**（model2vec 官方已提供）：
- `potion-multilingual-128M`（2025-05-23 發布）：101 語言，128M 參數，支援中文
- 或保留 `potion-base-8M` 但**限制 summary 必須為英文**（與 DESIGN.md 中文範例衝突）

**必要的設計決策**：
1. 宣告 RemaGraph v1 是否支援中文輸入
2. 若支援中文，改用 `potion-multilingual-128M` 或同等多語言模型，並更新 DESIGN.md 的模型名稱與 pyproject.toml 依賴

---

### 🔴 B2：SQLite FTS5 `unicode61` tokenizer 對中文的「保整塊」行為——全文搜尋對中文實際無效

**來源**：D02 §12 Q1、D05 §9 Q6

**問題**：根據官方 SQLite FTS5 文件與實測，`unicode61`（FTS5 預設 tokenizer）對 CJK 字元的處理是**將整塊連續 CJK 字元視為單一 token**，而非 bigram 分詞。

具體影響：
```
輸入「連線錯誤」→ 單一 token "連線錯誤"
查詢「連線」→ 無法匹配
查詢「連線錯誤」→ 精確匹配（但不匹配「網路連線」）
```

D02 §12 Q1 的「bigram 分詞」描述**有誤**——那是需要手動實作的自訂 tokenizer 行為，並非 `unicode61` 的預設行為。D05 §9 Q6 則錯誤判定「品質可接受」。

**RemaGraph 的核心功能 `remagraph_search` 在中文輸入下，召回率極低。**

**設計決策選項**：
1. **v1 採用 trigram tokenizer**：`tokenize='trigram'`（SQLite 3.44+ 內建，Python 3.11 bundled SQLite ≥ 3.41，需確認版本）——最低成本
2. **手動 bigram 前處理**：寫入/查詢時對中文做 bigram 分詞（如 D02 ref 文章所示）——額外實作但效果最佳
3. **宣告 v1 僅支援英文查詢**（與 DESIGN.md 中文範例衝突）

**必要的設計決策**：在 DESIGN.md 或 D02 中明確記載 tokenizer 選擇及中文支援策略，並更新 FTS5 DDL。

---

### 🔴 B3：Unix socket daemon 的 transport 設計與現實 MCP 生態嚴重脫節

**來源**：D03 §12 Q1

**問題**：根據 2025–2026 年 MCP spec 演進，MCP transport 已明確收斂為：
- **本地使用 → stdio**（官方主流，所有主要 client 支援）
- **遠端使用 → Streamable HTTP**（取代 HTTP+SSE，2025-03-26 起）
- **Unix socket → 非標準，無 MCP spec 定義，主流 client 不支援**

DESIGN.md 的「獨立 Unix socket daemon process（比照 CodeGraph）」在 MCP 生態中屬孤立方案：
- Claude Desktop、Cursor、VS Code、OpenCode 等主流 client **均使用 stdio**
- D03 §12 Q1 自己也指出「90% 的 MCP client 只支援 stdio transport」

這不只是「保留 Unix socket 作 v2」的問題，而是 **DESIGN.md 的核心部署描述就是錯的**，會造成實作者誤判優先序。

**必要的設計修正**：
- DESIGN.md「部署形態」章節應改為：**v1 主要使用 stdio transport**，Unix socket daemon 為可選進階模式（或移至 vN 路線圖）
- D03 需對應更新 §2、§4、§12 Q1 的推薦

---

## 3. Non-blocking Improvements（建議改善）

### 🟡 N1：DESIGN.md DDL 缺少 `timestamp` 欄位（D02 指出）
D02 §1.1 的指摘正確。`timestamp`（MCP 回傳用）與 `created_at`（內部審計用）語意不同，應補上。雖可在實作時補，但 SOT（DESIGN.md）應先更新。

### 🟡 N2：FTS5 缺少 `memories_au`（AFTER UPDATE）trigger（D02 指出）
若未來 UPDATE 文字欄位，FTS5 index 會失步。D02 §1.2 的建議正確，應預防性加入。

### 🟡 N3：`remagraph_search` 的 `total_matches` 效能問題
D03 §12 Q5 指出 COUNT(*) 查詢在大資料量下的效能問題，建議改用 `LIMIT top_k + 1` 的「has more」語意，或直接移除 `total_matches` 欄位（v1 不需要精確計數）。

### 🟡 N4：audit.jsonl 的 `ts` 精度不一致
DESIGN.md 的 `timestamp`（記憶寫入時間）精確到秒（`YYYY-MM-DDTHH:MM:SSZ`），但 audit.jsonl 的 `ts` 精確到毫秒（`.sssZ`）。兩者語意不同可接受，但應在 DESIGN.md 中明確標注，避免實作者混淆。

### 🟡 N5：`remagraph_status` 的 `limit` 缺預設值
DESIGN.md 未定義預設值，D01 Q6 建議 10 或 20。D05 Q3 建議 20。應在 DESIGN.md 明確定義（建議 20，最大 100）。

### 🟡 N6：規則 #3 的 `handoff_note` 適用 kind 不一致
DESIGN.md 規則 #3 說「`handoff_note` ≥ 20 字」未限定 kind。D01 §5 補充「僅對 `task_handoff` 強制」，D03 的 request schema 也說「其他 kind 可空」。**D01/D03 比 DESIGN.md 更精確**，應回寫 DESIGN.md。

### 🟡 N7：`agent_id` 長度上限缺於 DESIGN.md
DESIGN.md 只定義格式 `^[a-z0-9_-]+$`，未定義長度限制。D01/D03 補充了 3–64 字元。應回寫 DESIGN.md。

### 🟡 N8：model2vec 初始化失敗的降級策略未定義
D04 §9 Q5 提出，若 `potion-base-8M`（或多語言替代模型）無法下載，應如何處理。DESIGN.md 無任何說明。建議在 DESIGN.md 或 D02 中明確定義：**fail-fast（啟動失敗）** 是最安全選項，避免靜默降級造成去重失效而 agent 不知情。

### 🟡 N9：FTS5 query 注入防護
D03 §12 Q6 提出此問題。雖然 RemaGraph 是本地工具，但 FTS5 特殊字元（`*`, `"`, `AND`, `OR`, `NOT`）若未 sanitize，會造成非預期查詢行為（非安全問題，但影響穩定性）。D02 §5 已有 `sanitize_fts5_query()` 設計，應在 DESIGN.md 的「查詢範例」章節加一行說明。

### 🟡 N10：pyproject.toml 缺少 MCP 框架依賴
DESIGN.md 的 `pyproject.toml` 只列 `model2vec`，但實作 MCP server 需要 MCP Python SDK（如 `mcp>=1.0`）。此為遺漏。

---

## 4. 跨文件矛盾表

| # | 矛盾點 | 文件A | 文件B | 裁決 |
|---|--------|-------|-------|------|
| C1 | `handoff_note` 規則 #3 是否適用全 kind | DESIGN.md：未限定 kind，字面上全 kind 強制 | D01 §5、D03 §1.1：僅 `task_handoff` 強制 | **D01/D03 正確**，DESIGN.md 應回寫 |
| C2 | FTS5 CJK 分詞行為描述 | D02 §12 Q1：「bigram 分詞」 | SQLite 官方文件：`unicode61` 保整塊，非 bigram | **D02 描述有誤**，應修正（見 B2） |
| C3 | `remagraph_status` `limit` 預設值 | D01 Q6：建議 10 或 20 | D05 Q3：建議 20 | **採 D05：預設 20，最大 100** |
| C4 | `remagraph_search` 預設 `top_k` | DESIGN.md 範例：5 | D02 §12 Q3、D05：建議 20 | **採 D02/D05：預設 20，最大 100**；DESIGN.md 範例僅示意，不代表預設值 |
| C5 | `memories_fts` 欄位同步策略 | DESIGN.md DDL：只有 INSERT/DELETE trigger | D02 §1.2：缺少 UPDATE trigger | **D02 正確**，應補上 |
| C6 | `timestamp` 欄位存在性 | DESIGN.md DDL：無此欄位 | D02 §1.1：明確指出應補 `timestamp` | **D02 正確**，DESIGN.md DDL 應補欄位 |
| C7 | 部署 transport 優先序 | DESIGN.md：Unix socket daemon 為主 | D03 §12 Q1：承認 90% client 只用 stdio | **D03 自打嘴巴**，見 B3 |
| C8 | `StoreResponse` 欄位 | D02/D03：含 `superseded[]` 和 `invalidated_count` | DESIGN.md：只有 `status` 和 `id` | **D02/D03 更完整**，應回寫 DESIGN.md |

---

## 5. 開放問題裁決表

### D01 開放問題

| # | 問題 | 推薦答案 | 信心度 | 理由 |
|---|------|---------|--------|------|
| Q1 | `potion-base-8M` CJK 支援程度 | ❌ **不支援 CJK**，必須換用 `potion-multilingual-128M` | ⬛⬛⬛⬛⬛ 95% | 官方明確標注 English only；multilingual-128M 為官方 2025-05 發布的正式多語言替代，支援 101 語言含中文。代價：128M vs 8M，但對去重功能的正確性是必要的 |
| Q2 | 去重門檻 0.92 的校準依據 | **DEFER（v1 先用 0.90，記錄為待校準）**；建議按 kind 分別設定：`task_handoff: 0.90`、`status_update: 0.88`、`discovered_constraint: 0.92` | ⬛⬛⬛⬜⬜ 60% | 換用多語言模型後，原有 0.92 完全缺乏校準依據。需要真實中文資料集測試。先設較保守值，上線後依誤判率調整 |
| Q3 | `status_update` supersede 是否跨 `task_id` | **v1 嚴格限同 `task_id`，不擴展** | ⬛⬛⬛⬛⬜ 80% | 父子 task 關係屬業務語意，不該由記憶系統推斷。`task_id` 為自由字串，呼叫方可自行用命名慣例（如 `task-epic1-sub001`）表達層級。v1 複雜度控管優先 |
| Q4 | `discovered_constraint` invalidate 雙向追溯 | **v1 不做雙向**，audit.jsonl 記錄已足夠 | ⬛⬛⬛⬛⬜ 80% | audit.jsonl 已有 `action` 欄位可擴展，外部系統可從 audit log 重建關係圖。在 `Memory` 物件上加 `invalidated_by` 欄位會使查詢複雜化而效益低 |
| Q5 | embedding 計算時機與快取（10,000+ 筆） | **v1 全量線性掃描**，同時加上簡單優化：僅比對同 kind 的 active 記憶；**設定上限 2,000 筆**（超過時隨機取樣） | ⬛⬛⬛⬜⬜ 65% | MCP tool 非即時 API，200–500ms 可接受。但需設上限防止失控。v2 再引入 ANN 索引 |
| Q6 | `remagraph_status` `limit` 預設值 | **預設 20，最大 100** | ⬛⬛⬛⬛⬜ 85% | 10 筆對多 agent 多 task 環境太少；100 筆上限防止大型部署下的效能問題 |

---

### D02 開放問題

| # | 問題 | 推薦答案 | 信心度 | 理由 |
|---|------|---------|--------|------|
| Q1 | FTS5 BM25 CJK 分詞問題 | **v1 改用 `trigram` tokenizer**（`tokenize='trigram'`） | ⬛⬛⬛⬛⬜ 80% | `trigram` 是 SQLite 3.44+ 內建功能，零外部依賴，對 CJK 自動做 3-gram 切分，無需手動前處理。Python 3.11+ bundled SQLite 版本需確認（macOS/Ubuntu CI 環境的 SQLite 版本是否 ≥3.44）。若不支援，降級方案：手動 bigram 前處理 |
| Q2 | `status_update` supersede 跨 `task_id` | **同 D01 Q3，v1 不擴展** | ⬛⬛⬛⬛⬜ 80% | — |
| Q3 | `remagraph_search` 預設 `top_k` | **預設 20，最大 100** | ⬛⬛⬛⬛⬜ 85% | 搜尋需要更多候選供 agent 篩選；與 `remagraph_status` 的 `limit=20` 統一邏輯 |
| Q4 | dedup 效能邊界（5,000+ 筆） | **v1 同 kind active 記憶 ≤ 2,000 筆時全量比對；超過時取最新 2,000 筆** | ⬛⬛⬛⬜⬜ 65% | 超過 2,000 筆 active 同 kind 記憶屬極端情境。限制取樣筆數而非全量可接受，並在文件記載此限制 |
| Q5 | embedding BLOB endianness | **明確指定 `np.float32` + little-endian**（`tobytes()` 前 `.astype('<f4')`，讀回時 `np.frombuffer(b, dtype='<f4')`） | ⬛⬛⬛⬛⬜ 85% | D02 的建議正確，但應更明確指定 `'<f4'`（little-endian float32）而非依賴系統預設 |
| Q6 | audit.jsonl rotation 策略 | **DEFER to v2**，v1 不做 rotation；文件記載「單一 append-only 檔案，不做 rotation」 | ⬛⬛⬛⬛⬜ 85% | v1 定位為個人 side project，日活動量有限。加入 rotation 會引入額外複雜度（rotation 時外部系統如何處理舊檔案？）。v2 可考慮按月滾動 |

---

### D03 開放問題

| # | 問題 | 推薦答案 | 信心度 | 理由 |
|---|------|---------|--------|------|
| Q1 | Unix socket daemon vs stdio only | **v1 主要實作 stdio**，Unix socket 移至 DESIGN.md vN 路線圖 | ⬛⬛⬛⬛⬛ 95% | 見 B3。MCP 生態已明確收斂 stdio（本地）。Unix socket 無標準 MCP spec 支援，主流 client 無法使用 |
| Q2 | MCP 協定 JSON-RPC 版本 | **固定 MCP spec 2025-03-26（Streamable HTTP 正式化版）**，使用官方 Python MCP SDK 最新穩定版 | ⬛⬛⬛⬛⬜ 80% | 使用官方 SDK 可自動處理 spec 版本，無需手動 pin JSON-RPC 版本 |
| Q3 | model2vec 初始化成本 | **daemon 啟動時 eager load**（首次請求延遲不可接受）；**stdio 模式 lazy load**（process 生命週期短，啟動速度優先） | ⬛⬛⬛⬜⬜ 70% | stdio 模式每次呼叫都是新 process，eager load 浪費；daemon 模式長存，啟動時 load 一次合理 |
| Q4 | stdio 模式 concurrency | **嚴格序列化（FIFO）**，不需要 request queue 複雜機制 | ⬛⬛⬛⬛⬛ 95% | stdio 是單一串流，MCP spec 亦假設序列化。agent 呼叫 MCP tool 是 await-based，不需要並發處理 |
| Q5 | `total_matches` 效能 | **v1 移除 `total_matches`**，改用 `has_more: bool`（`LIMIT top_k + 1` 取 k+1，若回傳 k+1 筆則 `has_more=true`） | ⬛⬛⬛⬛⬜ 80% | 精確計數對 agent 使用場景價值低；`has_more` 已足夠讓 agent 決定是否縮小查詢範圍 |
| Q6 | FTS5 query 注入防護 | **必須在 server 端 sanitize**：移除/跳脫 FTS5 特殊字元，或將 query 包成 phrase query（`"..."`） | ⬛⬛⬛⬛⬜ 80% | 雖然是本地工具，但 agent 產生的 query 字串不可控，FTS5 語法錯誤會造成例外而非空結果，影響穩定性 |
| Q7 | 多實例 WAL checkpoint | **v1 不支援多實例共用 DB**（PID 鎖強制單一 daemon）；WAL checkpoint 使用 SQLite automatic checkpoint（預設每 1000 pages）| ⬛⬛⬛⬛⬜ 85% | 多實例共用 DB 屬 v2+ 場景，v1 PID 鎖已阻擋。WAL automatic checkpoint 在單 process 下已足夠 |

---

### D04 開放問題

| # | 問題 | 推薦答案 | 信心度 | 理由 |
|---|------|---------|--------|------|
| Q1 | audit.jsonl rotation 策略 | **DEFER to v2**（同 D02 Q6） | ⬛⬛⬛⬛⬜ 85% | — |
| Q2 | audit `ts` 時區 | **全 UTC（`Z` 後綴），不支援 local time** | ⬛⬛⬛⬛⬛ 95% | UTC 是分散式系統的唯一正確選擇。local time 帶 offset 在 DST 切換時有 ambiguity |
| Q3 | `error` 欄位粒度 | **v1 只記錄 exception class name**，不做細緻分類 | ⬛⬛⬛⬛⬜ 80% | 外部消費方只需判斷 stored/error，不需要區分 SQLite error code。細緻分類增加維護負擔，且可能洩漏內部實作細節 |
| Q4 | 多 instance audit.jsonl 競爭 | **v1 PID 鎖保證單一 process，不需要額外保護** | ⬛⬛⬛⬛⬜ 85% | 見 D03 Q7。`O_APPEND` 的 POSIX atomicity 問題在單 process 下不存在 |
| Q5 | model2vec 下載失敗降級策略 | **fail-fast（啟動失敗 / 第一次呼叫失敗並回傳明確錯誤）**，不靜默降級 | ⬛⬛⬛⬛⬜ 85% | 靜默降級（跳過規則 #4 或改用 Jaccard）會讓 agent 以為去重有在運作，實際沒有。fail-fast 讓問題可見，使用者可手動快取模型或離線使用 |
| Q6 | gitignore 與 audit.jsonl | **不需在專案 .gitignore 處理**（state_dir 在 repo 外） | ⬛⬛⬛⬛⬛ 95% | D04 的分析正確。若未來支援 per-project `.remagraph/` 模式，再在文件中提醒 |

---

### D05 開放問題

| # | 問題 | 推薦答案 | 信心度 | 理由 |
|---|------|---------|--------|------|
| Q1 | TDD vs 先補 models.py | **TDD：先寫 `test_models.py`** | ⬛⬛⬛⬛⬜ 85% | D05 的建議正確 |
| Q2 | `query=""` 的行為 | **回傳空 `results`，不拋錯，加 warning log** | ⬛⬛⬛⬛⬜ 85% | D05 的建議正確 |
| Q3 | `remagraph_status` `limit` 預設值 | **預設 20，最大 100**（同 D01 Q6） | ⬛⬛⬛⬛⬜ 85% | — |
| Q4 | `summary` 長度計算 | **`len(summary.strip())`** | ⬛⬛⬛⬛⬛ 95% | D05 的建議正確。純 `len()` 讓 agent 可用空白填充繞過門檻 |
| Q5 | MCP 整合測試 transport | **stdio transport**（同 B3 裁決） | ⬛⬛⬛⬛⬛ 95% | Unix socket 測試僅在 macOS/Linux，CI 矩陣包含可能的跨平台問題 |
| Q6 | FTS5 tokenizer | **改用 trigram**（同 B2 裁決），不用 `unicode61` 預設值 | ⬛⬛⬛⬛⬜ 80% | D05「品質可接受」的判斷**有誤**，見 B2 |
| Q7 | mutmut CI 速度 | **限縮到 `arbitration.py` + `dedup.py`，`--runner pytest -n auto`** | ⬛⬛⬛⬛⬜ 80% | D05 的建議正確 |
| Q8 | server.py coverage 豁免 | **MCP SDK boilerplate 可在 `pyproject.toml` 設 exclude**，其餘走 MCP 整合測試 | ⬛⬛⬛⬛⬜ 80% | D05 的建議正確 |

---

## 6. 對 DESIGN.md 的建議修正

以下修正項目標注「應回寫 DESIGN.md」，因設計文件已比 SOT 更精確：

| # | 修正類型 | 位置 | 內容 |
|---|---------|------|------|
| R1 | **應回寫（B1）** | §MCP 介面 + §輕量仲裁 + §pyproject.toml | 將 `potion-base-8M` 改為 `potion-multilingual-128M`；更新依賴版本；說明中文支援 |
| R2 | **應回寫（B2）** | §儲存層：SQLite + FTS5 → FTS5 DDL | FTS5 建表語句加入 `tokenize='trigram'`；補充中文支援說明 |
| R3 | **應回寫（B3）** | §部署形態 | 改為「v1 主要使用 stdio transport；Unix socket daemon 為進階模式（vN 路線圖）」 |
| R4 | **應回寫（C1/N6）** | §輕量仲裁 規則 #3 | 補充「僅對 `kind=task_handoff` 強制，其他 kind 可空」 |
| R5 | **

## Citations

- https://github.com/MinishLab/model2vec
- https://dev.to/foxck016077/sqlite-fts5-wont-tokenize-chinese-heres-the-7-line-bigram-fix-that-did-4fcc
- https://rollbrains.com/mcp/mcp-transports-compared/
- https://note.com/ayato_studio/n/n61c1ccefbab4?hl=en-US
- https://startdebugging.net/2026/07/mcp-stdio-vs-http-vs-sse-transport-which-to-choose/
- https://github.com/microsoft/mcp-for-beginners/blob/main/translations/pcm/03-GettingStarted/05-stdio-server/README.md
- https://github.com/MinishLab/model2vec-rs
- https://github.com/am009/simper_fts5
- https://huggingface.co/models?library=model2vec
- https://www.sqlite.org/fts5.html
- https://audrey.feldroy.com/articles/2025-01-13-SQLite-FTS5-Tokenizers-unicode61-and-ascii
- https://stackoverflow.com/questions/52422437/why-sqlite-fts5-unicode61-tokenizer-does-not-support-cjkchinese-japanese-korean
- https://www.youtube.com/watch?v=iJihcJJBeAU
- https://www.sqlite.net.cn/fts5.html
- https://github.com/groue/GRDB.swift/issues/413
