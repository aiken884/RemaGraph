# RemaGraph v2 規劃藍圖（草案）

> 狀態：`[x]` PPLX 已審核（2026-07-22，第一次評估：單人 side project）
> 狀態：`[ ]` PPLX 第二次審核（2026-07-22，更新前提：即將公開、陌生多數使用者）
> 來源：設計文件（D01–D05）中 tagged v2 items、v1 closeout 已知限制

---

## 1. 搜尋與語意強化

| ID | 項目 | 來源 | 說明 |
|----|------|------|------|
| S1 | **sqlite-vec 語意搜尋** | D01 §5, D02 §7 | v1 embedding 只存不查；v2 建立 ANN index，支援「找相似記憶」 |
| S2 | **Hybrid search**（BM25 + vector） | D02 §7.2 | 結合全文檢索與語意相似度，改善召回品質 |
| S3 | **中文分詞** | 實作計畫 §11 | v1 trigram tokenizer 對中文語義無感；v2 可加 jieba 或 model2vec 補充 |
| S4 | **Embedding 維度 256→768** | D02 §7.2 | 升級 model2vec 模型，需 migration script |

**依賴**：S1→S2（hybrid 需先有 vector search）→S4（升維度可能需重算 embedding）

---

## 2. 可靠度與營運

| ID | 項目 | 來源 | 說明 |
|----|------|------|------|
| O1 | **Audit rotation** | D04 §9, PPLX 共識 | audit.jsonl 按月滾動，避免單檔無限增長 |
| O2 | **Superseded 記錄清理** | D04 §9.2 | 定期清理舊 superseded 記錄，保留最近 N 筆 |
| O3 | **max_db_size 設定** | D04 §9.2 | SQLite `PRAGMA max_page_count` 防 DB 無上限成長 |
| O4 | **多 process 共用 DB** | PPLX 審查 Q7 | 移除 PID 鎖、支援 multi-instance WAL |
| O5 | **Migration 框架驗證** | D02 §2.2 | v1→v2 migration 流程已在設計中預留但未實測 |

---

## 3. 安全與治理

| ID | 項目 | 來源 | 說明 |
|----|------|------|------|
| A1 | **Rate limiting** | D04 §9.2 | per agent_id / per task_id 呼叫上限 |
| A2 | **task_id 格式驗證** | D04 §9.2 | 例如 `task-YYYY-MM-DD-NNN`，降低惡意輸入風險 |
| A3 | **路徑穿越防禦** | D04 §8 | v1 無檔案關聯功能無攻擊面；v2 若加入需預先防禦 |
| A4 | **Agent 管理 table** | D01 §6.3 | 從 `memories.agent_id` 回溯建立獨立 agents 表 |

---

## 4. 傳輸與部署

| ID | 項目 | 來源 | 說明 |
|----|------|------|------|
| T1 | **Unix socket daemon** | DESIGN.md, PPLX 審查 | 替代 stdio 作為預設 transport，支援 persistent 背景服務 |
| T2 | **Tool schema 版本化** | 實作計畫 §11 | MCP tool 輸入／輸出 schema 納入版本管理 |

---

## 5. 開發基礎

| ID | 項目 | 來源 | 說明 |
|----|------|------|------|
| D1 | **mypy CI gate** | P1-1 | mypy 已在 dev deps，但未進 merge 閘門 |
| D2 | **CONTRIBUTING + PR template** | P1-4 | 開源前必備 |
| D3 | **ADR 決策紀錄** | P1-3 | 重大偏離設計時寫入 `docs/decisions/` |
| D4 | **PyPI release pipeline** | P1-6 | HITL release 流程自動化 |

---

## 依賴圖

```
                ┌──────┐
                │  D1  │ (mypy CI gate — 最簡單、可先做)
                └──┬───┘
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
  ┌──────┐    ┌──────┐     ┌────────┐
  │ O1   │    │ S3   │     │  T1    │
  │ O2   │    │(分詞) │     │(socket)│
  │ O3   │    └──┬───┘     └───┬────┘
  └──────┘       ▼             │
            ┌──────────┐      │
            │ S1       │      │
            │(vec搜尋) │      │
            └────┬─────┘      │
                 ▼            │
            ┌──────────┐      │
            │ S2       │      │
            │(hybrid)  │      │
            └────┬─────┘      │
                 ▼            ▼
            ┌──────────┐ ┌────────┐
            │ S4       │ │ T2     │
            │(dim升級) │ │(schema)│
            └──────────┘ └────────┘
```

---

## 6. 問題給 PPLX

1. **以上 15 項中，哪些是現階段（one-person side project、無公開使用者）值得投入的？**
2. **哪些應永久 deferred 直到有外部使用者或商業需求？**
3. **若只選 3 項做 v2，你的排序是什麼？理由？**
4. **S1（sqlite-vec 語意搜尋）和 T1（Unix socket daemon）哪個對 RemaGraph 的價值更大？**
5. **O1–O3（營運面）是否該等有實際容量問題再處理，而非提前做？**

---

## 7. PPLX 審核結論（2026-07-22，第一次評估：單人 side project）
...

---

## 8. PPLX 第二次審核結論（2026-07-22，更新前提：即將公開、陌生多數使用者）

> **前提變更**：即將 Apache-2.0 開源，會面臨陌生多數使用者。
> 治理、安全性、可靠度需求大幅提升。

### 新排序原則

安全治理 + 可靠度營運 + 開發基礎 → 壓過「搜尋強化」這類功能性增強。

### 公開前必做核心（5 項）

| 排序 | ID | 項目 | 理由 |
|------|----|------|------|
| 1 | **A3** | 路徑穿越防禦 | 缺失 = 直接安全事故 |
| 2 | **A1** | Rate limiting | 公開後無法預測使用行為，最小自我保護 |
| 3 | **O5** | Migration 框架驗證 | 沒有可靠 migration 就無法安全升級 |
| 4 | **D1** | mypy CI gate | 開源後最實用的品質與安全防線 |
| 5 | **D2** | CONTRIBUTING + PR template | 對社群講清楚標準的機制 |

若預期外部 PR 較慢，可將 D2 換成 **O1（audit rotation）**。

### O1–O3 評估：全部拉進核心

| ID | 判定 | 理由 |
|----|------|------|
| O1 | **核心** | 公開後 audit log 是安全事件唯一 source of truth |
| O2 | **核心，簡化版** | 避免 DB 長期肥大影響查詢效能 |
| O3 | **核心** | 開源環境不可控，max_db_size 是基本營運 guardrail |

### 上線後再做（不擋 MVP）

- **搜尋強化**：S1 sqlite-vec / S2 Hybrid / S3 分詞 / S4 升維
- **安全補強**：A2 task_id 驗證 / A4 Agent 管理 table
- **營運強化**：O2 完整版
- **部署演進**：T1 Unix socket / T2 schema 版本化
- **開發治理**：D3 ADR / D4 PyPI release pipeline

### Now → Soon → Later（更新版）

```
公開 MVP 必做（5 項） → A3 → A1 → O5 → D1 → D2 (或 O1)
v2.x 短期補上        → O1 / O3 / O2（簡化版）/ A2
開放上線後迭代        → S1-S4 / T1 / T2 / A4 / D3 / D4

---

## 9. 治理框架對齊與執行計畫

> 依據 Vault《通用專案治理框架與檢查清單》對齊 RemaGraph v2 執行範圍。
> P0 基礎已全數 `[x]`（v1 closeout），本計畫涵蓋 P0 剩餘實作面 + P1 全項 + 安全營運強化。

### 9.1 P0 追補（治理框架要求但 v1 尚未實體化）

| ID | 治理項 | RemaGraph 現況 | v2 動作 |
|----|--------|----------------|---------|
| P0-4b | Review 清單已定義 | v1 adversarial summary 有記錄但無持續流程 | 建 `docs/reviews/review-guidelines.md`，明寫審查範圍與 checkpoints |
| P0-4c | 對抗審查持續啟用 | 僅 v1 實作期執行過 | CI 或 PR template 加入對抗審查觸發機制 |
| P0-6b | `docs/architecture.md` | 指向 DESIGN.md，無獨立架構文件 | 建 `docs/architecture.md`（系統圖 + 元件說明） |

### 9.2 P1 規模化準備（公開前必須補齊）

| ID | 治理項 | RemaGraph 現況 | v2 動作 | 對應藍圖 ID |
|----|--------|----------------|---------|-------------|
| P1-1 | 嚴格 lint + type checker + coverage 80% | ruff 全綠；mypy 在 dev deps 未進 CI；coverage 已 ≥80 | **mypy CI gate**（strict mode，進 merge 閘門） | D1 |
| P1-2 | 依賴安全升級流程 | Dependabot 已啟用 | 補 `dependabot.yml` 策略文件（automerge 規則、重大升級審查流程） | —（既有強化）|
| P1-3 | ADR 決策紀錄 | `docs/decisions/` 不存在 | 建目錄 + 第一篇 ADR（v2 規劃決策紀錄） | —（新增）|
| P1-4 | CONTRIBUTING + PR template | 不存在 | 建 `CONTRIBUTING.md` + `.github/PULL_REQUEST_TEMPLATE.md` | D2 |
| P1-5 | CHANGELOG | 已有 `[Unreleased]` | 確認格式對齊 Keep a Changelog | —（既有）|
| P1-6 | 發布自動化 | 無 PyPI pipeline | 建 `.github/workflows/publish.yml`（tag 觸發、trusted publishing） | —（新增）|

### 9.3 安全與營運強化（PPLX 第二次評估核心）

| 藍圖 ID | 項目 | 治理對應 | v2 動作 |
|---------|------|---------|---------|
| A3 | 路徑穿越防禦 | P0-4（安全審查） | 所有檔案操作 API 加入路徑正則驗證 |
| A1 | Rate limiting | P0-4（安全審查） | per agent_id / per task_id token bucket |
| O5 | Migration 框架驗證 | P1-1（可靠度） | `db.py` migration 路徑實測 + CI 測試 |
| O1 | Audit rotation | P1-2（營運可靠） | audit.jsonl 按日期分檔 |
| O3 | max_db_size | P1-2（營運可靠） | SQLite PRAGMA max_page_count |
| O2 | Superseded 清理 | P1-2（營運可靠） | 定期清理舊 superseded（簡化版）|
| A2 | task_id 格式驗證 | P0-4（安全審查） | 正則驗證 + Pydantic validator |

### 9.4 執行順序（建議）

```
Phase 1（公開前必做）
  P1-4  CONTRIBUTING + PR template  ─┐
  P1-6  PyPI publish pipeline        ─┤ 治理基礎
  P0-6b docs/architecture.md         ─┘
       ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  A3    路徑穿越防禦  ─┐
  A1    Rate limiting  ─┤ 安全防線
  A2    task_id 驗證   ─┘
       ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  D1    mypy CI gate  ─── 品質閘門
  O5    Migration 驗證 ─── 可靠度

Phase 2（短期補上）
  O1    Audit rotation
  O3    max_db_size
  O2    Superseded 清理（簡化版）
  P1-3  ADR 目錄 + 第一篇
  P1-2  Dependabot 策略補強

Phase 3（上線後迭代）
  S1-S4 搜尋強化
  T1     Unix socket daemon
  T2     Tool schema 版本化
  A4     Agent 管理 table
  D3     ADR 持續紀錄
```
```
