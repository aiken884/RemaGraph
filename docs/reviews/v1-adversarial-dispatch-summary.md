# RemaGraph v1 對抗式審查派工摘要

| WU | 實作 | 審查 route | 異質 | 狀態 |
|----|------|------------|------|------|
| WU-0 | opencode-deepseek-pro (deepseek) | audit → claude-sonnet (anthropic) | family 不同 | 已派；gate 由指揮塔 ruff/trigram/import 驗收 |
| WU-1–5 | opencode-deepseek-pro | audit → claude-sonnet | family 不同 | 已派；pytest 117→全套綠 |
| WU-6/7 | opencode-deepseek-pro | audit → claude-sonnet | family 不同 | 已派；pytest 綠 |
| WU-8 | opencode-deepseek-pro（deepseek 首 stall 後 retry 成功） | audit → claude-sonnet | family 不同 | 已派；smoke 4 + server tests 綠 |
| WU-9/10 | opencode-deepseek-pro | （文件/CI 為主，以指揮塔 gate 驗收） | — | CI/README/CHANGELOG 驗收 |

**規則**：審查 `(model_family, tier)` 至少一維異於實作；本 v1 實作以 deepseek 為主、審查 anthropic。

**mutmut**：CI `mutmut.yml` 非 blocking（`continue-on-error`）；本地追蹤 arbitration/dedup。完整 mutation score 以 CI/本地 `uv run mutmut run` 為準，v1 不因 mutmut 超時阻斷交付。
