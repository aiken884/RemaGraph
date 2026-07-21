# RemaGraph 治理檢查清單

| 項目 | 內容 |
|------|------|
| **來源** | Vault《通用專案治理框架與檢查清單》（P0／P1／P2） |
| **專案適配** | RemaGraph v1 **已交付**（一人 side project + 艦隊／指揮塔）；套件 `0.1.0` |
| **對應計畫** | [`docs/plans/implementation-plan-v1.md`](../plans/implementation-plan-v1.md) §11 |
| **收斂狀態** | [`docs/reviews/v1-closeout-status.md`](../reviews/v1-closeout-status.md) |
| **標記** | `[x]` 已完成 · `[~]` 進行中／部分 · `[ ]` 未做 · `[-]` 刻意延後（附理由） |

> 新專案：完成 **P0** 後可發布 v0.1 級可用；**P1** 完成後較適合對外邀請貢獻；**P2** 社群化／公開敏感時再推。  
> **v1 現況**：實作期 P0 已關；open `[ ]` 僅 **人類 GitHub 設定面**（branch protection 等）。

---

## P0 基礎必備（v1 實作期必須對齊）

### P0-1 版本控制 + git 流程

- [x] 專案在 GitHub（private `aiken884/RemaGraph`）
- [x] 主分支保護（require PR／status check）— **已透過 GitHub API 設定**（1 review、required test status、enforce admins）
- [x] Commit 歷史有意義（design freeze／plan 等）
- [x] `.gitignore` 覆蓋 venv／coverage／db／IDE
- [x] 廢分支清理 — 已清除 3 個已 merge 功能分支
- **驗證**：`git log --oneline`；遠端 private 可達

### P0-2 License 與法律

- [x] 根目錄 `LICENSE`（Apache-2.0 全文）
- [x] `pyproject.toml` / README 可標 SPDX `Apache-2.0`
- [x] 源檔 SPDX 標頭 — 全源檔 `src/remagraph/*.py` 已加 `# SPDX-License-Identifier: Apache-2.0`
- [-] NOTICE 第三方 — 目前無 vendored 第三方源碼；model2vec 依套件授權
- **驗證**：LICENSE 存在且為 Apache-2.0

### P0-3 驗證閘門：測試 + 覆蓋率 + 冒煙

**測試與覆蓋率**
- [x] pytest／pytest-cov／mutmut 在 `pyproject.toml` dev deps；mutmut 已設定指向 arbitration + dedup
- [x] 核心 unit／integration 實測 — **WU-1～WU-9**（test_arbitration / test_dedup / test_store / test_search / smoke）
- [x] CI 有 test workflow（matrix）；smoke → lint → test 鏈
- [x] coverage 門檻設計為 **≥80**（高於通用框架 P0 的 60%，對齊專案 DESIGN／P1）
- [x] CI 紅燈不可 merge — branch protection 已設定（require test status check）
- [x] coverage report 產物 — WU-9（coverage.xml artifact）

**冒煙測試（RemaGraph 定義，見實作計畫 §11.3）**
- [x] 可 `import remagraph`／CLI 或 MCP stdio 啟動不崩（smoke job 驗證 import）
- [x] `remagraph_store` 最小合法寫入成功（或明確 rejected reason）（test_full_lifecycle）
- [x] `remagraph_search` 對剛寫入內容可命中（含至少一則中文）（test_full_lifecycle）
- [x] `remagraph_status` 回傳結構合法（test_full_lifecycle）
- [x] state 目錄建立且 audit 有對應 `stored`／`error` 行（smoke job 以 REMAGRAPH_STATE_DIR 隔離）
- [x] 冒煙進 CI 且失敗 block（smoke job 失敗 → lint/test 不執行）
- **驗證**：本地 `pytest tests/smoke` + CI job（`REMAGRAPH_STATE_DIR=${{ runner.temp }}/remagraph-smoke`）

**突變測試（mutation testing）**
- [x] `[tool.mutmut]` 設定於 pyproject.toml，targets：`arbitration.py` + `dedup.py`
- [x] CI mutmut workflow 非 blocking（`continue-on-error: true`，獨立於 test.yml 鏈）
- [x] 本地執行：`uv run mutmut run`
- **驗證**：`.github/workflows/mutmut.yml` 存在且可 workflow_dispatch 手動觸發

### P0-4 代碼審查與對抗式驗證

- [x] 指揮塔章程：每機制對抗式審查、route() 四眼 — 見 `docs/reviews/v1-adversarial-dispatch-summary.md`
- [x] 每個功能 WU 合入前：實作 agent ≠ 審查 agent — 見 `docs/reviews/v1-adversarial-dispatch-summary.md`
- [x] 對抗審查發現寫入 `docs/reviews/` 或 PR 註記 — 見 `docs/reviews/v1-adversarial-dispatch-summary.md`
- [x] 問題關閉前不得標 WU done — v1 已完成
- [x] WU checklist 中 P0-4 欄位須填寫 `實作 agent: <model@tier>` / `審查 agent: <model@tier>` — 見 `docs/reviews/v1-adversarial-dispatch-summary.md`
- **驗證**：WU 完成記錄含 review 決議 LGTM／NEEDS_WORK

### P0-5 Secret 管理

- [x] 無 production secret 進 repo 設計；state 在 `~/.local/state/`
- [x] `.env.example` — v1 可選 env（state path／log）；**WU-0 完成**
- [x] CI gitleaks workflow 已存在
- [x] pre-commit gitleaks — **WU-0 完成**
- [x] 金鑰不進程式：model 走 HF cache／env，不寫死 token
- **驗證**：`gitleaks`；無硬編碼 token；`.env.example` 存在且無真實 secret

### P0-6 決策與文件

- [x] `DESIGN.md` + `docs/design/*` 為架構／規格
- [x] `docs/plans/implementation-plan-v1.md` 實作藍圖
- [x] PPLX 審查紀錄在 `docs/design/reviews`、`docs/plans/reviews`
- [x] README 快速開始完整度 — **WU-10 完成**（stdio 安裝、MCP config 範例、REMAGRAPH_STATE_DIR、三 tool 參數表）
- [-] `docs/architecture.md` 精簡索引（可指向 DESIGN）— DESIGN.md 已涵蓋架構，不重複
- **驗證**：外人 30 分鐘內能懂「agent 殘跡記憶 MCP」

### P0-7 依賴管理與掃描

- [x] `pyproject.toml` 列依賴（model2vec／mcp／pydantic）
- [x] lockfile（`uv.lock` 或同等）— **WU-0 完成**
- [x] CI `pip-audit` workflow 已存在
- [x] HIGH/CRITICAL fail — 確認 workflow 設定與實作期對齊
- [x] Dependabot — `.github/dependabot.yml` 已建立（pip + github-actions 週更）
- **驗證**：本地／CI pip-audit；`uv.lock` 已提交

---

## P1 規模化前（v1 實作中後半～首次對外）

| ID | 項 | RemaGraph 處置 | 狀態 |
|----|-----|----------------|------|
| P1-1 | 嚴格 lint／format／（可選 mypy） | ruff 全綠；coverage ≥80；mypy 在 dev deps **未**進 CI 閘門 | [x] ruff / [ ] mypy CI |
| P1-2 | Dependabot／Renovate | `.github/dependabot.yml`（pip + github-actions 週更）已落地 | [x] |
| P1-3 | ADR `docs/decisions/` | 實作期無重大偏離設計；有決策時再建 | [ ] 有決策再建 |
| P1-4 | CONTRIBUTING + PR template | 開源前；私人期可簡版 | [ ] |
| P1-5 | CHANGELOG | `[Unreleased]` 已有（對齊 closeout 敘述） | [x] |
| P1-6 | 發布自動化 | **HITL**：不自動 PyPI；可先 tag+CI 測 | [-] 自動 publish 禁止至人類 release |

---

## P2 社群化（有外部貢獻／公開敏感時）

| ID | 項 | RemaGraph 處置 | 狀態 |
|----|-----|----------------|------|
| P2-1 | SECURITY.md | public 前 | [-] |
| P2-2 | CODE_OF_CONDUCT | 開源社群前 | [-] |
| P2-3 | 發行簽章 | 有正式 release 時 | [-] |
| P2-4 | SBOM | 商業／合規要求時 | [-] |
| P2-5 | CodeQL／靜態分析 | public 後建議 | [-] |
| P2-6 | GOVERNANCE／MAINTAINERS | 多人維護時 | [-] |

---

## 與實作 WU 的強制綁定

| WU | 必須關閉的治理項 |
|----|------------------|
| WU-0 | P0-5 `.env.example`／pre-commit；P0-7 lockfile 評估；SQLite gate；ruff |
| 每功能 WU | P0-4 對抗審查完成紀錄 |
| WU-8 完成 | P0-3 冒煙全綠（本機） |
| WU-9 | P0-3 CI 測試+cov+冒煙；mutation 目標 |
| WU-10 | P0-6 README；P1-5 CHANGELOG Unreleased |

**規則**：對應 WU 的治理項未勾 `[x]`，該 WU **不得**標完成。

---

## 修訂

| 日期 | 說明 |
|------|------|
| 2026-07-21 | 自 Vault 通用框架適配 RemaGraph；納入實作計畫 §11 |
| 2026-07-21 | WU-0 完成：pin 上界、`.env.example`、`pre-commit`（ruff+gitleaks）、`uv.lock` 更新、CI 重排 smoke→lint→test、trigram gate、ruff 全綠 |
| 2026-07-21 | WU-10 完成：README（stdio 安裝／MCP config／REMAGRAPH_STATE_DIR／三 tool 參數表）、CHANGELOG [Unreleased]、P0-3 測試全綠、P0-6 勾完、P1-5 勾完 |
| 2026-07-21 | WU-9 完成：smoke job 執行 pytest tests/smoke/ + REMAGRAPH_STATE_DIR 隔離、mutmut 設定（arbitration+dedup）+ CI workflow（非 blocking）、coverage.xml artifact、checklist P0-3 可勾項更新 |


## 修訂（v1 交付）

- 2026-07-21：v1 收尾 — mutmut 降版 2.5.1（Python 3.12 + src-layout 相容，避開 v3 bug）、Dependabot 建立、SPDX 標頭補完、P0-4 全勾（指向 v1-adversarial-dispatch-summary）。仍開著的 `[ ]`：廢分支清理、branch protection（需人類 GitHub 設定）。
- 2026-07-22：ship 前準備 — branch protection 設定完成、廢分支清理完成；全部 P0 項 `[x]`。
