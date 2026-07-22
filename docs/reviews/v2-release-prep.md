# RemaGraph v0.2.0-alpha 內部測試準備

**日期**：2026-07-22  
**版本**：`0.2.0-alpha`  
**狀態**：內部 Alpha 測試階段；**僅供內部使用，不對外發布 PyPI**

> 目前專案仍在內部開發階段，尚未完成真實使用者情境與完整內部測試。因此暫不對外發布，僅供群內 herdr Bridge 與相關專案進行 Alpha 測試。

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

### 版本與文件

- [x] `pyproject.toml` version = `0.2.0`
- [x] `CHANGELOG.md` 新增 `[0.2.0] — 2026-07-22`
- [x] `README.md` 版本表與快速開始
- [x] `docs/architecture.md` CLI 架構
- [x] `docs/task-memory-convention.md` 白話慣例
- [x] `docs/design/00-index.md` 指向本檔
- [x] `docs/plans/remagraph-herdr-integration-plan.md` 實作狀態

### 不在本版自動做（HITL）

- [ ] `git tag v0.2.0 && git push origin v0.2.0`（觸發 PyPI publish workflow）
- [ ] 確認 GitHub Actions 額度足夠、publish job 綠燈
- [ ] 確認 PyPI trusted publishing 已綁定本 repo
- [ ] GitHub Release 說明人工覆核

---

## 3. 內部測試安裝方式（目前推薦）

```bash
# 從 git 安裝（推薦內部使用）
uv tool install git+https://github.com/aiken884/RemaGraph.git

# 或從本機 clone 安裝（開發測試）
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph
uv pip install -e .
```

使用範例請見 `docs/task-memory-convention.md` 與 README「快速開始（非技術使用者）」章節。

**注意**：暫不執行 `git tag v0.2.0` 與 PyPI 發布流程。待內部測試完成、真實使用者情境驗證後再討論對外發布時機。

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

## 5. 已知限制

| 項目 | 說明 |
|------|------|
| PyPI | 需 HITL tag；workflow 用 trusted publishing |
| CI Actions | 可能額度不足；以本機 gate 為準 |
| ID 格式 | `task_id`/`agent_id` 不可用中文；agent_id 須小寫 3–64 |
| summary | ≥ 30 字；`auto` 會自動補足 |
| herdr 正式接入 | 工具層+治理層已完成（herdr-bridge hooks + RemaGraph wrapper）；組織層（herdr-org）僅設計階段，開發稍後；範例已提供 |

---

## 6. 相關連結

- Changelog：[`CHANGELOG.md`](../../CHANGELOG.md)
- 任務記憶慣例：[`docs/task-memory-convention.md`](../task-memory-convention.md)
- 整合計劃：[`docs/plans/remagraph-herdr-integration-plan.md`](../plans/remagraph-herdr-integration-plan.md)
- Publish workflow：[`.github/workflows/publish.yml`](../../.github/workflows/publish.yml)
