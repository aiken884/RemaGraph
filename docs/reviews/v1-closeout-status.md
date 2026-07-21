# RemaGraph v1 收斂狀態（Closeout）

| 項目 | 內容 |
|------|------|
| **狀態** | **v1 核心交付完成**（stdio MCP 三 tool + 品質閘門） |
| **Date** | 2026-07-21 |
| **套件版本** | `0.1.0`（`pyproject.toml` / `src/remagraph/__init__.py`） |
| **證據 commit** | `ef666a5`（polish：mutmut / Dependabot / SPDX）及之前 WU 實作鏈 |
| **遠端** | `origin/main` private `aiken884/RemaGraph` |
| **Orchestrated-by** | CommandTower（fleet 實作；本檔為指揮塔收斂紀錄） |

> **活清單**：治理勾選以 [`docs/governance/checklist.md`](../governance/checklist.md) 為準。  
> **對抗審查摘要**：[`v1-adversarial-dispatch-summary.md`](./v1-adversarial-dispatch-summary.md)。  
> **設計 SOT**：[`DESIGN.md`](../../DESIGN.md)（設計凍結；本檔不改設計，只對齊「已實作現況」）。

---

## 1. 白話摘要

RemaGraph v1 已可在本機 editable install 後，以 **stdio MCP** 提供三個 tool：寫入記憶、全文搜尋、查最新 status。資料落在可注入的 state 目錄（預設 `~/.local/state/remagraph/`），audit 寫 `audit.jsonl`。自動 PyPI 發布**不做**；GitHub 分支保護仍需人類在 GitHub 設定。

---

## 2. 交付範圍（已完成）

| 範圍 | 現況 |
|------|------|
| WU-0～WU-10 | 已實作並合入 `main` |
| MCP tools | `remagraph_store` / `remagraph_search` / `remagraph_status` |
| Transport | stdio（`remagraph serve` / FastMCP） |
| 儲存 | SQLite + FTS5 `tokenize=trigram`、WAL |
| 仲裁 | #1 summary 長度 · #2 learnings 非空 · #3 handoff_note（僅 task_handoff）· #4 model2vec 去重 · #5 agent_id 格式 |
| 去重 | `potion-multilingual-128M`；cosine ≥ **0.90**；`EMBEDDING_DIM=256`（實測鎖定） |
| Audit | **`audit.jsonl`**（非 DB table）；store commit 後寫入；合約見 `docs/audit.md` |
| 測試 | unit + smoke；coverage 門檻 ≥80 |
| CI | smoke → lint → test；另 gitleaks / pip-audit / mutmut（mutmut 非 blocking） |
| 依賴掃描 | Dependabot（pip + github-actions）已設定 |

### 本機驗收快照（指揮塔 2026-07-21 重跑）

| Gate | 結果 |
|------|------|
| `pytest -m 'not slow'` | 192 passed |
| `pytest tests/smoke`（temp `REMAGRAPH_STATE_DIR`） | 4 passed |
| `ruff check src tests` | All checks passed |
| `rg NotImplementedError src/remagraph` | 無 |

---

## 3. 刻意非目標 / 未做（v1）

- PyPI 正式 publish（HITL only）
- Unix socket daemon 預設 transport
- sqlite-vec 語意搜尋、audit rotation、多 process 共用 DB
- 跨 `task_id` supersede、invalidate 雙向追溯
- 中文去重門檻正式校準（維持 0.90「待校準」）
- mypy 強制進 CI（dev 依賴有 mypy，**未**當 merge 閘門）
- 完整 mutmut score ≥70% 強制（CI 可跑、非 blocking；見 adversarial summary）

---

## 4. 僅人類／HITL 剩餘項

| 項 | 說明 | checklist |
|----|------|-----------|
| GitHub branch protection | require PR / status checks | P0-1、P0-3 設定面 |
| 廢分支清理慣例 | 可選 | P0-1 |
| Dependabot PR 審合 | 設定已在；PR 出現後人工 | P0-7 / P1-2 |
| 可選：本地完整 `mutmut run` 分數 | 不阻 v1 | P0-3 追蹤 |
| PyPI release | 禁止自動；另開 HITL | P1-6 `[-]` |

---

## 5. 文件對齊備註（本次 closeout）

下列文件在收斂時已改為「實作後現況」表述（不改凍結設計正文契約）：

- `docs/plans/implementation-plan-v1.md` — 狀態改「v1 已實作」；§6／§8 交付勾選更新
- `docs/plans/goal-v1-implementation.md` — Goal task checklist 勾選完成
- `docs/governance/checklist.md` — Dependabot 等已落地項對齊
- `CHANGELOG.md` — 修正仲裁規則與 audit 為 jsonl 的敘述
- `README.md` — 補現況／版本／文件索引
- `docs/design/00-index.md` — 補「設計已落地 v0.1.0」一行

**不進 git**：`.omo/`、`.command-tower/tasks/prompts/*`（派工暫存）。
