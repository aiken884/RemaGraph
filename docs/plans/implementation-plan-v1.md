# RemaGraph v1 實作計畫

| 項目 | 內容 |
|------|------|
| **狀態** | **v1 已實作並合入 `main`**（WU-0～10）；本檔仍是實作藍圖 SOT，歷史凍結語境見 §0 |
| **實作收斂** | [`docs/reviews/v1-closeout-status.md`](../reviews/v1-closeout-status.md)；證據 HEAD 含 `ef666a5` |
| **Date** | 2026-07-21（計畫凍結）／2026-07-21（實作收斂） |
| **Version** | v1.2.1（v1.2 + 實作完成狀態對齊；不改 §2 凍結決策） |
| **SOT** | [`DESIGN.md`](../../DESIGN.md)（設計凍結 commit `8905d23`） |
| **設計展開** | [`docs/design/00-index.md`](../design/00-index.md) 及 D01–D05 |
| **治理框架** | Vault《通用專案治理框架與檢查清單》→ 本計畫 §11 + [`docs/governance/checklist.md`](../governance/checklist.md) |
| **設計複審** | [`docs/design/reviews/pplx-recheck-2026-07-21.md`](../design/reviews/pplx-recheck-2026-07-21.md) → **APPROVE** |
| **計畫初審** | [`docs/plans/reviews/pplx-impl-plan-review-2026-07-21.md`](reviews/pplx-impl-plan-review-2026-07-21.md) → APPROVE_WITH_CHANGES → v1.1 修訂 |

---

## 0. 硬性邊界（本文件與後續執行皆適用）

1. **歷史**：本文件初稿階段只規劃、不執行實作；進入寫碼前須人類明確「同意實作」。**v1 已於 2026-07-21 經 Goal 授權並由艦隊完成。**
2. 實作必須對齊凍結設計；若與 `DESIGN.md` 衝突，**以 DESIGN.md 為準**並開 ADR／回報，不得靜默偏離。
3. **不耦合**任何具體外部指揮／治理系統（程式碼層/API層不得直接 import/依賴任何特定外部專案）；跨專案協作僅透過 `examples/` 範例對接與公開穩定介面（如 Audit Contract）進行。
4. 依賴新增限設計已列：`model2vec`、`mcp`、`pydantic`；optional `sqlite-vec` 僅 v2。建議 **pin 上界**（如 `model2vec>=0.1.0,<2.0`、`mcp>=1.0,<2`）於 WU-0。
5. 派工紀律：實作由執行代理依專案路由政策派工；本計畫為「實作藍圖」。
6. **本計畫 APPROVE ≠ 開工令**（歷史規則保留）；v1 開工已另有人類 Goal 同意。**PyPI publish 仍須另開 HITL**，不因 v1 完成而自動授權。

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
| 範圍 | `pyproject.toml` 依賴 pin 上界；entry point；ruff；CI 加入 SQLite≥3.38 + trigram 斷言步驟；**治理 P0 基建**：`.env.example`（可選 env 說明）、pre-commit（ruff + gitleaks）、**提交 `uv.lock`**（強制，CI 使用 `uv sync --frozen`）、確認 gitleaks／pip-audit workflow 行為 |
| 依賴 | 無 |
| 驗收 | editable install；`import remagraph`；CI 有 SQLite 版本 gate + **trigram gate（見下方 §「Trigram CI Gate」）**；`uv.lock` 已提交且 CI frozen sync 正常；`docs/governance/checklist.md` 中 WU-0 綁定項可勾 |
| 對應設計 | DESIGN 部署／pyproject；D03；D05 CI；治理 P0-1／P0-5／P0-7 |

**Trigram CI Gate（WU-0 定義，WU-0 實作）**：

```python
# ci/test_trigram_gate.py — CI 必須通過此 gate
def test_fts5_trigram_available():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content, tokenize='trigram')")
    conn.execute("INSERT INTO t VALUES ('hello world test')")
    rows = conn.execute("SELECT * FROM t WHERE t MATCH 'ell'").fetchall()
    assert len(rows) == 1  # trigram 支援子字串匹配

def test_fts5_trigram_rejects_bigram():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content, tokenize='trigram')")
    conn.execute("INSERT INTO t VALUES ('測試中文 trigram')")
    rows = conn.execute("SELECT * FROM t WHERE t MATCH 'gl'").fetchall()
    assert len(rows) == 0  # bigram 不應匹配 trigram
```

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
| 範圍 | 矩陣補齊；coverage ≥80；mutmut arbitration+dedup **目標 ≥70%**；ruff；gitleaks；**冒煙測試套件進 CI 且失敗 block**（§11.3）；CI 順序：smoke → lint → full tests+cov |
| 依賴 | WU-1–WU-8 |
| 驗收 | 本地與 CI 綠；mutation 報告可追蹤；P0-3 檢查清單對應項可勾 |
| 對應設計 | D05；治理 P0-3／P1-1 |

### WU-10 — 文件與治理交付

| 項目 | 內容 |
|------|------|
| 範圍 | README 安裝／stdio MCP config；短 query／首次模型下載；`CHANGELOG.md` 建 `[Unreleased]`（P1-5）；可選 `docs/architecture.md` 指向 DESIGN；更新 `docs/governance/checklist.md` 進度；說明 v1 tool schema 不做版本化（v2 債） |
| 依賴 | WU-8 |
| 驗收 | README 可複製即用；**不**自動 publish（HITL release）；P0-6／P1-5 可勾 |
| 對應設計 | DESIGN；D03；治理 P0-6／P1-5 |

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
**每完成一個功能 WU** 必須再 dispatch 一次審查任務（§11.4），與實作分開派工。

---

## 6. 驗證閘門

### 6.1 每 WU

- [x] 對應驗收列通過（WU-0～10；見 closeout）
- [x] 無外部具名系統耦合
- [x] 無未核准依賴
- [x] guilty-until-proven
- [x] **P0-4 對抗式審查完成**（異質 tier／不同實例；結論可追溯）— [`docs/reviews/v1-adversarial-dispatch-summary.md`](../reviews/v1-adversarial-dispatch-summary.md)
- [x] 該 WU 綁定之 `docs/governance/checklist.md` 項已更新

### 6.2 Pre-merge（對齊治理 P0-3／P0-5／P0-7）

建議 CI 順序（治理附錄 Q8）：**smoke → lint → full tests + coverage →（可選）mutmut 報告**。

| Gate | 條件 | 治理對應 |
|------|------|----------|
| smoke | §11.3 冒煙全綠 | P0-3 |
| lint | ruff 0 | P0-3／P1-1 |
| tests | pytest 全綠 | P0-3 |
| coverage | ≥ 80 | P0-3（專案採 P1 門檻） |
| secret | gitleaks clean | P0-5 |
| dep | pip-audit 無未處理 HIGH/CRITICAL | P0-7 |
| mutation | arbitration+dedup 目標 ≥70% 或 PR 說明 | 專案強化 |
| sqlite | version ≥ 3.38 + trigram | 設計凍結 |
| design | 抽樣 §2 凍結表 | P0-6 |
| adversarial | 功能 PR 有異質審查紀錄 | P0-4 |

### 6.3 內容驗收（防半殘；對齊治理「驗內容不是只驗 exit code」）

- [x] store 後 SQLite 可讀預期欄位；FTS 可查
- [x] store 成功 → BLOB `np.frombuffer(blob, dtype='<f4').shape == (EMBEDDING_DIM,)`（`EMBEDDING_DIM=256`）
- [x] audit：`action=remagraph_store` 且 `status=stored`（成功）或 `error`+reason（拒絕）— **audit.jsonl**
- [x] search 中文有合理命中；短 query 空 + warning
- [x] **remagraph_status 同 task_id 多筆只回最新 1 筆**
- [x] top_k=1 且有 2 筆 → `has_more=True`；僅 1 筆 → `has_more=False`
- [x] 仲裁拒絕 reason_code 正確
- [x] 冒煙套件覆蓋 §11.3 清單且斷言實際輸出內容

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
| **Trigram CJK 短查詢靜默失敗** | 中文 ≤2 字 query 無法形成完整 trigram，查詢被靜默忽略 | WU-6 驗收須測 1字、2字、3字、混合查詢各一組；≤2字失敗必須在 tool response 中回傳明確錯誤而非空結果 |
| **Trigram BM25 對中文語義無感** | 同字不同義無法區分，BM25 評分失真 | 記錄為 v1 已知限制，v2 考慮 jieba 分詞或 model2vec 語義補充；WU-6 記錄 ≥3 組中文 query 的實際 top-3 召回作為 v1 baseline |
| **model2vec 模型下載失敗／CI 無網** | 去重不可用，server 崩潰 | fail-fast → tool 回傳結構化錯誤不崩潰；CI 使用 `HF_HUB_OFFLINE=1` + 預載 cache 策略 |
| trigram 1–2 字元空回 | 難診斷 | 短 query 統一 warning 路徑，與 CJK 短查詢合併處理 |

---

## 8. 交付物檢查清單（實作階段結束時）

- [x] 三 MCP tool 可用（stdio）
- [x] Audit Contract 可驗證（`docs/audit.md` + smoke）
- [x] coverage ≥80、CI 綠（含冒煙）— 本地 192+smoke 4 於收斂時重驗
- [x] README 更新；CHANGELOG `[Unreleased]` 存在
- [x] `docs/governance/checklist.md` P0 項無未解釋的 `[ ]`（僅剩**人類 GitHub 設定面** open 項，已標註 owner）
- [x] **不**自動 PyPI publish

---

## 9. 明確禁止事項

| 禁止 | 說明 |
|------|------|
| 將本計畫 APPROVE 視為開工令 | 仍需人類「同意實作」 |
| 假實作騙 coverage | 禁止；成功須驗內容（治理精神） |
| 繞過 route() | 禁止 |
| 跳過 P0-4 對抗審查即合入 | 禁止 |
| 未再審核改重大架構 | 需再送 PPLX |
| 未達 P0 就公開邀請外部貢獻 | 禁止（治理：先 P0 再 P1） |

---

## 10. 修訂紀錄

| 版本 | 日期 | 說明 |
|------|------|------|
| v1.0-draft | 2026-07-21 | 初稿送 PPLX |
| v1.1 | 2026-07-21 | 依 PPLX 初審：B-1 維度鎖定、B-2 依賴圖／WU-7、B-3 status 去重驗收；A–F + 關鍵 N |
| v1.2 | 2026-07-21 | 融入 Vault《通用專案治理框架與檢查清單》：§11 對齊、冒煙定義、對抗審查流程、P0/P1/P2 映射；新增 `docs/governance/checklist.md`；強化 WU-0／9／10 與 pre-merge 閘門 |
| v1.2.1 | 2026-07-21 | 實作收斂：狀態改「v1 已實作」；§6／§8 勾選；§11 現況表對齊；見 `docs/reviews/v1-closeout-status.md` |

---

## 11. 通用專案治理框架融入（RemaGraph 適配）

> **來源**：`ObsidianVault/通用專案治理框架與檢查清單.md`（P0／P1／P2）。  
> **活檢查表**：[`docs/governance/checklist.md`](../governance/checklist.md)（狀態以該檔為準，本節為計畫綁定規則）。  
> **原則**：按部就班——**v1 實作必須關閉 P0**；P1 在實作後半／首次對外前；P2 延後至公開社群或合規需要。

### 11.1 優先序與本專案階段對照

| 級 | 框架定義 | RemaGraph 現況與計畫 | 執行時機 |
|----|----------|----------------------|----------|
| **P0** | 任何專案無例外 | **v1 已關閉實作期 P0**（測試／冒煙／對抗審查／SPDX 等）；僅剩人類 GitHub branch protection 設定面 | WU-0～WU-10 **已完成** |
| **P1** | 規模化前 3～6 月 | cov 80、CHANGELOG、Dependabot **已有**；mypy 強制／ADR 目錄／CONTRIBUTING 仍待 public 前 | 首次 public 前補齊 |
| **P2** | 社群／敏感 | 私人 side project；SECURITY／CoC／SBOM 等 | **明確延後**（清單標 `[-]`） |

### 11.2 P0 七項 → 實作綁定

| ID | 治理項 | 已有 | 計畫落地 |
|----|--------|------|----------|
| P0-1 | Git 流程 | private repo、有意義 commit | 實作期 branch／PR 慣例；公開前 branch protection |
| P0-2 | License | Apache-2.0 `LICENSE` | 維持；可選 SPDX 標頭於 WU-0／10 |
| P0-3 | 測試+覆蓋率+**冒煙** | **已真測**；smoke→lint→test；cov≥80 | 維持；mutmut 非 blocking 追蹤 |
| P0-4 | 審查+**對抗式驗證** | orchestrator 派工紀律＋設計期 PPLX | 每功能 WU：異質審查；紀錄可追溯 |
| P0-5 | Secret | gitleaks CI；無 secret 進庫設計 | WU-0：`.env.example`、pre-commit gitleaks |
| P0-6 | 決策文件 | DESIGN + design docs + 計畫 + PPLX 紀錄 | WU-10 README；重大偏離寫 ADR |
| P0-7 | 依賴掃描 | pyproject、pip-audit CI | WU-0 pin／lock；HIGH+ fail |

### 11.3 RemaGraph 冒煙測試定義（P0-3）

對齊框架「關鍵路徑、快速、失敗即 block」，**斷言實際行為／產出，不只 exit code**：

| # | 步驟 | 通過條件 |
|---|------|----------|
| S1 | 套件／server 啟動 | `import remagraph` 成功；stdio MCP 可握手（或等價 entry） |
| S2 | 最小 store | 合法 `task_handoff`（達字數門檻）→ `status=stored` 且 DB 可讀到該 id |
| S3 | 仲裁拒絕可觀測 | 過短 summary → `rejected` + 正確 reason；可選 audit `error` |
| S4 | search | 對 S2 內容 query（含中文）→ results 非空或可解釋命中 |
| S5 | status | 回傳結構合法；若寫過 status_update 則可見最新 |
| S6 | audit 合約 | **強制** 使用 `REMAGRAPH_STATE_DIR` 環境變數或等效機制將 state 指向 pytest `tmp_path`，禁止使用 `~/.local/state/remagraph/` 或任何使用者級路徑。該 `tmp_path` 下的 audit.jsonl 出現對應 `remagraph_store` 記錄 |

- 實作建議路徑：`tests/smoke/`（名稱可調），**WU-8 後必須本機全綠，WU-9 進 CI**。  
- 冒煙目標：**< 1～2 分鐘**；完整矩陣其後跑。

### 11.4 對抗式審查流程（P0-4 × orchestrator）

對齊框架 Q7 與內部 orchestrator 派工紀律：

1. 實作：`route()` 選 implement tier，完成 WU 程式與測試。  
2. 審查：`route()` 再選 **review／audit** tier（**不同實例**；能力地板達標；避免與實作者同一未隔離 session）。  
3. 審查者 **guilty-until-proven**：攻擊真實路徑（stdio、state 權限、FTS 中文、仲裁、audit）。  
4. 產出：PR 或 `docs/reviews/WU-xx-adversarial.md`（問題列表 + 是否已修）。  
5. **未 LGTM 不得標 WU 完成、不得 merge。**

### 11.5 P1／P2 刻意範圍

| 級 | 納入 v1 實作期 | 延後 |
|----|----------------|------|
| P1 | ruff、cov 80、CHANGELOG Unreleased、**Dependabot 已落地**；ADR 僅在有決策時 | 完整 CONTRIBUTING、mypy 強制 CI、自動 publish |
| P2 | 無強制 | SECURITY、CoC、簽章、SBOM、CodeQL、GOVERNANCE |

公開開源或邀請外部貢獻前：回頭用 `docs/governance/checklist.md` 把 P1 缺口關閉。

### 11.6 檔案結構對齊（框架 §2）

| 框架建議 | RemaGraph |
|----------|-----------|
| README / LICENSE | 已有 |
| tests/ | 已填實；含 `tests/smoke/` |
| docs/architecture | DESIGN.md 為 SOT（未另建 architecture.md，刻意 `[-]`） |
| docs/decisions | 無重大偏離設計 → 尚未建目錄（P1-3 有決策時再建） |
| .github/workflows | test／gitleaks／pip-audit／mutmut；Dependabot |
| .pre-commit-config.yaml | **已有**（ruff + gitleaks） |
| .env.example | **已有** |
| CHANGELOG | **已有** `[Unreleased]` |
| CONTRIBUTING／SECURITY／… | P1／P2 延後 |

### 11.7 完成定義加嚴（治理）

v1「可交付」除 §1.1 功能完成外，另需：

1. `docs/governance/checklist.md` 之 **P0** 無未解釋的 `[ ]`（`[~]` 須註明剩餘工作與 owner）。→ **已對齊**；open 項僅人類 GitHub 設定。  
2. 冒煙 §11.3 在 CI 綠。→ **已落地**。  
3. 至少一份功能 WU 的對抗審查樣例留存（證明流程可跑）。→ **`docs/reviews/v1-adversarial-dispatch-summary.md`**。  
4. 仍遵守：無人類 release 同意不 PyPI publish。→ **維持**。
