# T-RG-DF1：回寫 DESIGN.md（PPLX 共識）

讀取並嚴格依循：
- `docs/design/reviews/pplx-consensus-actions-2026-07-21.md`
- `docs/design/reviews/pplx-design-review-2026-07-21.md`

## 任務
只修改 **`DESIGN.md`**（必要時同步 `docs/audit.md` 若契約文字需一致；通常不必）。

套用 B1、B2、B3 與共識表中所有「應回寫 DESIGN.md」項：
- potion-multilingual-128M、中文支援、fail-fast
- FTS5 tokenize=trigram + 中文說明
- 部署：v1 主 stdio
- handoff_note 僅 task_handoff
- timestamp 欄、memories_au trigger
- limit/top_k 預設 20 最大 100
- agent_id 3–64
- StoreResponse 擴充欄位（若適用）
- has_more 取代 total_matches（若 search response 有寫）
- pyproject 片段：model2vec 模型名 + mcp 依賴

禁止改 src/tests 實作。文末加 `## PPLX-CONSENSUS-APPLIED` checklist。
