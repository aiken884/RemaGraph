# 任務分解：RemaGraph 設計階段落地

- **Date**: 2026-07-21
- **Phase**: Design only（禁止實作程式碼）
- **SOT**: `DESIGN.md`
- **完成定義**: 設計文件落地 → PPLX 審核 → 共識與修正完成 → 等使用者明確同意後才可進入實作
- **Orchestrated-by**: CommandTower

## 範圍（In）

依 `DESIGN.md` 補齊**可審查、可實作前凍結**的設計文件，放在 `docs/design/`：

| 任務 ID | 產出檔案 | 範圍 |
|---------|----------|------|
| T-RG-D01 | `docs/design/01-data-model-arbitration.md` | Memory schema、三種 kind、生命週期 supersede/invalidate、五條仲裁規則精確語意與錯誤碼 |
| T-RG-D02 | `docs/design/02-storage-search-dedup.md` | SQLite+FTS5 schema/trigger、store/search API、model2vec 去重、embedding BLOB 策略 |
| T-RG-D03 | `docs/design/03-mcp-server-runtime.md` | 三個 MCP tool 契約、Unix socket daemon、設定、啟動/關閉、狀態目錄權限 |
| T-RG-D04 | `docs/design/04-audit-security.md` | audit.jsonl schema、Audit Contract、威脅模型、權限、最小洩漏 |
| T-RG-D05 | `docs/design/05-test-acceptance.md` | 模組驗收條件、測試矩陣、CI 門檻對齊 DESIGN、突變測試範圍 |

另產出（可由任一軌或後續整合）：

- `docs/design/00-index.md` — 文件索引、開放問題清單、與 DESIGN.md 差異表

## 範圍外（Out）

- ❌ 實作 `src/remagraph/**` 功能碼
- ❌ 修改既有 stub 以外的行為（不要「順便實作」）
- ❌ 依賴 herdr-bridge / herdr-gov
- ❌ PyPI release / 開源準備以外的業務決策
- ❌ 修改指揮塔章程

## 每份設計文件必含章節

1. 目標與非目標  
2. 公開介面（函式/型別/JSON 契約，精確到可寫測試）  
3. 演算法／狀態機／不變量  
4. 錯誤碼與失敗模式  
5. 邊界案例  
6. 驗收條件（Given/When/Then 或等效）  
7. 開放問題（供 PPLX 裁決）  
8. 與 `DESIGN.md` 的對齊聲明（無衝突 / 建議修正處）

## 語言

台灣式繁體中文（zh-TW）。程式識別字與 API 名稱可保留英文。

## 路由決策（route()，no-bypass）

全部 design / S1 / medium / required_capabilities=(code, review)：

| task_id | tier_id | binding | reason |
|---------|---------|---------|--------|
| T-RG-D01 … D05 | `opencode-deepseek-pro` | `opencode-go/deepseek-v4-pro` | selected（cost-first 夠格） |

## 驗收（指揮塔 Gate，設計階段）

- [ ] 五份檔案存在且非空、章節齊全  
- [ ] 無實作程式碼變更（`src/**` 除必要註解外應維持 stub）  
- [ ] 不出現 herdr-bridge / herdr-gov 耦合  
- [ ] 開放問題清單可供 PPLX 審  
- [ ] 提交 PPLX 審查並記錄共識／修正  

## HITL

- 設計完成並經 PPLX 共識後，**等待使用者明確同意**才進入實作。
