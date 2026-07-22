# RemaGraph v0.2.0 發行準備

**日期**：2026-07-22  
**版本**：`0.2.0`  
**狀態**：發行前準備完成；**打 tag / PyPI 需 HITL 確認**

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

## 3. 建議發行指令（確認後再跑）

```bash
# 1. 確認 main 乾淨且已 push
git status
git log -1 --oneline

# 2. 打 tag（觸發 .github/workflows/publish.yml）
git tag -a v0.2.0 -m "RemaGraph v0.2.0 — CLI init/auto + v2 hardening"
git push origin v0.2.0

# 3. 觀察 Actions：Publish to PyPI + GitHub Release
gh run list --workflow=publish.yml --limit 3
```

若 CI 仍因 Actions 額度暫停，可先只做 GitHub Release（不含 PyPI）：

```bash
gh release create v0.2.0 --title "v0.2.0" --notes-file CHANGELOG.md
```

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
| herdr 正式接入 | 範例已提供；指揮塔專案側另開 |

---

## 6. 相關連結

- Changelog：[`CHANGELOG.md`](../../CHANGELOG.md)
- 任務記憶慣例：[`docs/task-memory-convention.md`](../task-memory-convention.md)
- 整合計劃：[`docs/plans/remagraph-herdr-integration-plan.md`](../plans/remagraph-herdr-integration-plan.md)
- Publish workflow：[`.github/workflows/publish.yml`](../../.github/workflows/publish.yml)
