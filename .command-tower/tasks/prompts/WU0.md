# T-RG-WU0：工程基線（僅此範圍）

你是艦隊實作者。repo: /Users/aikenlin/Projects/RemaGraph

## 禁止
- 不要實作 store/search/server/arbitration/dedup/audit/db 的業務邏輯（可維持 stub）
- 不要耦合 herdr
- 不要 PyPI publish
- 語言：台灣繁體中文 commit message 可用英文 subject

## 必須完成（implementation-plan WU-0）
1. pyproject.toml：依賴 pin 上界 model2vec、mcp、pydantic；dev deps 完整；entry point 若 DESIGN 需要（如 remagraph CLI 或 python -m remagraph）
2. 若尚無 `.env.example`：可選 env（REMAGRAPH_STATE_DIR、log 等）說明、無真實 secret
3. `.pre-commit-config.yaml`：ruff + gitleaks
4. 確認 `uv.lock` 存在且與 pyproject 一致（`uv lock` 更新如需）
5. CI：
   - test.yml：順序建議 smoke→lint→test；`uv sync --frozen`；Python matrix 維持
   - 加入 SQLite≥3.38 + FTS5 trigram gate（見計畫中 test_fts5_trigram_available / rejects_bigram 邏輯，可放 tests/ 或 ci/）
6. ruff 可跑通（對現有檔不無理 fail 可最小設定）
7. 更新 docs/governance/checklist.md 中 WU-0 相關項（P0-5 pre-commit、P0-7 lockfile 若已提交勾 x）

## 完成標記
在 PR/回覆末尾寫 `## WU-0 DONE` 並列改動檔案清單。
