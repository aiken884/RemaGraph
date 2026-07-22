# RemaGraph v0.2.0 發行前準備

**日期**：2026-07-22  
**版本**：`0.2.0`  
**狀態**：已透過 Herdr Bridge 進入真實運作階段；目前進行發行前準備（暫不對外公開發布 PyPI）

> Herdr Bridge 已在真實生產環境中使用 RemaGraph。目前無計畫立即對外公開發布，重點在文件、治理、營運可靠性與發行前準備工作。

---

## 1. 本版範圍（白話）

v0.2.0 在 v1 MCP 三工具之上，補上：

1. **Headless CLI**：`store` / `search` / `status`
2. **極簡入口**：`init`（設定）+ `auto`（一鍵讀→做→寫）
3. **安全與治理**：路徑防禦、rate limit、輸入驗證、audit 分檔、migration
4. **文件與範例**：白話任務記憶慣例、herdr Bridge 範例、一鍵安裝 script

---

## 2. 發行前檢查清單

### 品質閘門（本機）

- [x] `uv run ruff check src tests`
- [x] `uv run mypy src --strict`
- [x] `uv run pytest`（224+ passed）
- [x] 手動 e2e：`init` → `auto` → `search --task-id` → `status`
- [x] Migration 框架驗證（O5）：tests/test_db_migrations.py 涵蓋新鮮 DB 與新版拒絕；v1→v4 / v3→v4 升級路徑已在開發中手動模擬並驗證資料保留（含 herdr-bridge 專案 DB）

### 版本與文件

- [x] `pyproject.toml` version = `0.2.0`
- [x] `CHANGELOG.md` 新增 `[0.2.0] — 2026-07-22`
- [x] `README.md` 版本表與快速開始
- [x] `docs/architecture.md` CLI 架構
- [x] `docs/task-memory-convention.md` 白話慣例
- [x] `docs/design/00-index.md` 指向本檔
- [x] `docs/plans/remagraph-herdr-integration-plan.md` 實作狀態
- [x] 新增 SECURITY.md、CODE_OF_CONDUCT.md（開源準備）

### 不在本版自動做（HITL）

- [ ] `git tag v0.2.0 && git push origin v0.2.0`（觸發 PyPI publish workflow）
- [ ] 確認 GitHub Actions 額度足夠、publish job 綠燈
- [ ] 確認 PyPI trusted publishing 已綁定本 repo
- [ ] GitHub Release 說明人工覆核

---

## 3. 目前安裝方式（推薦 via git）

```bash
# 從 git 安裝（推薦內部使用）
uv tool install git+https://github.com/aiken884/RemaGraph.git

# 或從本機 clone 安裝（開發測試）
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph
uv pip install -e .
```

使用範例請見 `docs/task-memory-convention.md` 與 README「快速開始（非技術使用者）」章節。

**注意**：真實運作已透過 Herdr Bridge 進行。目前聚焦發行前準備（文件、治理、營運可靠性），但**暫不執行 tag 與對外發布**。

---

## 4. 安裝驗證（發行後）

```bash
# 從 git（現在即可）
uv tool install git+https://github.com/aiken884/RemaGraph.git

# 從 PyPI（tag 成功後）
# uv tool install remagraph
# 或：pip install remagraph

remagraph init --project release-check
source ~/.local/state/remagraph-release-check/env.sh
remagraph auto --task-id release-check-001 --agent-id release-bot -- echo ok
remagraph search --task-id release-check-001
```

---

## 5. 已知限制與發行前注意事項

| 項目 | 說明 |
|------|------|
| PyPI | 需 HITL tag；workflow 用 trusted publishing；目前暫不發布 |
| CI Actions | 可能額度不足；以本機 gate 為準 |
| ID 格式 | `task_id`/`agent_id` 不可用中文；agent_id 須小寫 3–64 |
| summary | ≥ 30 字；`auto` 會自動補足 |
| herdr 接入 | Herdr Bridge 已真實運作中使用（工具層 + governance + CLI）；組織層（herdr-org）仍在設計/後續開發 |
| DB 與維護 | project_id 隔離 + 自動維護機制已實作；歷史 DB 需經 migration 路徑升級 |

---

## 6. 真實運作觀察（Herdr Bridge）

- project_id 隔離與 dedicated DB（herdr-bridge）已穩定運作。
- DB 自動維護（maintenance）已在多個專案目錄中執行，WAL / prune / FTS 行為正常。
- Safety valve 正確阻擋不合規的 state_dir 使用。
- 主要使用模式：task_handoff + status_update 為主，fleet_member 用於 tower 層級追蹤。
- 尚未發現重大穩定性問題；持續收集邊界案例（例如長 summary、大量 superseded）。

## 7. 相關連結

- Changelog：[`CHANGELOG.md`](../../CHANGELOG.md)
- 任務記憶慣例：[`docs/task-memory-convention.md`](../task-memory-convention.md)
- 整合計劃：[`docs/plans/remagraph-herdr-integration-plan.md`](../plans/remagraph-herdr-integration-plan.md)
- Publish workflow：[`.github/workflows/publish.yml`](../../.github/workflows/publish.yml)
