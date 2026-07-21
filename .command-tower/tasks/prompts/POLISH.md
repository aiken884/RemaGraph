# 加速收尾：mutmut 修復 + 可自動化 P0 尾巴

repo: /Users/aikenlin/Projects/RemaGraph
禁止 herdr 耦合、禁止 PyPI publish、禁止改業務邏輯語意。

## 1. 修 mutmut config（P0）
目前 `uv run mutmut` 因 pyproject `[tool.mutmut]` 的 `tests_dir` 字串與新版 mutmut 不相容而 TypeError。
請改成新版 mutmut 可用設定（例如 `source_paths`、`pytest_add_cli_args_test_selection` 為 list），並讓：
```
uv run mutmut run
```
至少能啟動（若太慢可只 mutate arbitration.py 且 runner 限 test_arbitration）。
同步修正 `.github/workflows/mutmut.yml` 指令。

## 2. Dependabot（P1 快速項）
新增 `.github/dependabot.yml`：pip 週更、github-actions 週更；`open-pull-requests-limit` 合理。

## 3. SPDX（可選但快）
在 `src/remagraph/*.py` 檔頭加一行 `# SPDX-License-Identifier: Apache-2.0`（不改邏輯）。

## 4. checklist
更新 `docs/governance/checklist.md`：
- Dependabot → [x]
- SPDX → [x]
- P0-4 四項若尚未 [x] 則勾上並指向 `docs/reviews/v1-adversarial-dispatch-summary.md`
- 廢分支清理、branch protection 維持 [ ] 並註明「需人類 GitHub 設定」

## 5. 驗證
- `uv run ruff check .` 全過
- `uv run pytest tests/ -m "not slow" -q` 全綠
- `uv run mutmut --help` 或一次短 run 不 TypeError

結尾 ## POLISH DONE
