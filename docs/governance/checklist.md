# RemaGraph 治理檢查清單

| 項目 | 內容 |
|------|------|
| **來源** | Vault《通用專案治理框架與檢查清單》（P0／P1／P2） |
| **專案適配** | RemaGraph v1 實作階段（一人 side project + 艦隊／指揮塔） |
| **對應計畫** | [`docs/plans/implementation-plan-v1.md`](../plans/implementation-plan-v1.md) §11 |
| **標記** | `[x]` 已完成 · `[~]` 進行中／部分 · `[ ]` 未做 · `[-]` 刻意延後（附理由） |

> 新專案：完成 **P0** 後可發布 v0.1 級可用；**P1** 完成後較適合對外邀請貢獻；**P2** 社群化／公開敏感時再推。

---

## P0 基礎必備（v1 實作期必須對齊）

### P0-1 版本控制 + git 流程

- [x] 專案在 GitHub（private `aiken884/RemaGraph`）
- [~] 主分支保護（require PR／status check）— **待開公開或多人前設定**
- [x] Commit 歷史有意義（design freeze／plan 等）
- [x] `.gitignore` 覆蓋 venv／coverage／db／IDE
- [ ] 廢分支清理慣例 — 實作期開始後執行
- **驗證**：`git log --oneline`；遠端 private 可達

### P0-2 License 與法律

- [x] 根目錄 `LICENSE`（Apache-2.0 全文）
- [x] `pyproject.toml` / README 可標 SPDX `Apache-2.0`
- [ ] 源檔 SPDX 標頭（可選，WU-0／WU-10 補）
- [-] NOTICE 第三方 — 目前無 vendored 第三方源碼；model2vec 依套件授權
- **驗證**：LICENSE 存在且為 Apache-2.0

### P0-3 驗證閘門：測試 + 覆蓋率 + 冒煙

**測試與覆蓋率**
- [~] pytest／pytest-cov／mutmut 在 `pyproject.toml` dev deps；測試檔仍為佔位
- [ ] 核心 unit／integration 實測 — **WU-1～WU-9**
- [x] CI 有 test workflow（matrix）
- [x] coverage 門檻設計為 **≥80**（高於通用框架 P0 的 60%，對齊專案 DESIGN／P1）
- [ ] CI 紅燈不可 merge — 需 branch protection
- [ ] coverage report 產物 — WU-9

**冒煙測試（RemaGraph 定義，見實作計畫 §11.3）**
- [ ] 可 `import remagraph`／CLI 或 MCP stdio 啟動不崩
- [ ] `remagraph_store` 最小合法寫入成功（或明確 rejected reason）
- [ ] `remagraph_search` 對剛寫入內容可命中（含至少一則中文）
- [ ] `remagraph_status` 回傳結構合法
- [ ] state 目錄建立且 audit 有對應 `stored`／`error` 行
- [ ] 冒煙進 CI 且失敗 block
- **驗證**：本地 `pytest tests/smoke`（路徑以實作為準）+ CI job

### P0-4 代碼審查與對抗式驗證

- [~] 指揮塔章程：每機制對抗式審查、route() 四眼 — **制度已有，實作 PR 須執行**
- [ ] 每個功能 WU 合入前：實作 agent ≠ 審查 agent（異質 tier）
- [ ] 對抗審查發現寫入 `docs/reviews/` 或 PR 註記
- [ ] 問題關閉前不得標 WU done
- **驗證**：WU 完成記錄含 review 決議 LGTM／NEEDS_WORK

### P0-5 Secret 管理

- [x] 無 production secret 進 repo 設計；state 在 `~/.local/state/`
- [~] `.env.example` — v1 可能僅需可選 env（state path／log）；**WU-0 補**
- [x] CI gitleaks workflow 已存在
- [ ] pre-commit gitleaks — **WU-0 建議補**
- [x] 金鑰不進程式：model 走 HF cache／env，不寫死 token
- **驗證**：`gitleaks`；無硬編碼 token

### P0-6 決策與文件

- [x] `DESIGN.md` + `docs/design/*` 為架構／規格
- [x] `docs/plans/implementation-plan-v1.md` 實作藍圖
- [x] PPLX 審查紀錄在 `docs/design/reviews`、`docs/plans/reviews`
- [~] README 快速開始完整度 — **WU-10**
- [ ] `docs/architecture.md` 精簡索引（可指向 DESIGN）— **WU-10 可選**
- **驗證**：外人 30 分鐘內能懂「agent 殘跡記憶 MCP」

### P0-7 依賴管理與掃描

- [x] `pyproject.toml` 列依賴（model2vec／mcp／pydantic）
- [ ] lockfile（`uv.lock` 或同等）— **WU-0 建議**
- [x] CI `pip-audit` workflow 已存在
- [~] HIGH/CRITICAL fail — 確認 workflow 設定與實作期對齊
- [ ] Dependabot — **P1**
- **驗證**：本地／CI pip-audit

---

## P1 規模化前（v1 實作中後半～首次對外）

| ID | 項 | RemaGraph 處置 | 狀態 |
|----|-----|----------------|------|
| P1-1 | 嚴格 lint／format／（可選 mypy） | WU-0／WU-9：ruff；coverage 已 80 | [ ] |
| P1-2 | Dependabot／Renovate | 首次 public 或有使用者前 | [ ] |
| P1-3 | ADR `docs/decisions/` | 實作期重大偏離設計時必寫；可補「stdio 優先」等 | [ ] |
| P1-4 | CONTRIBUTING + PR template | 開源前；私人期可簡版 | [ ] |
| P1-5 | CHANGELOG | WU-10 建 `[Unreleased]` | [ ] |
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
