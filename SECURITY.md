# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: (開發中，發行準備) |
| < 0.2   | :x:                |

## 回報安全漏洞

請**不要**公開 issue。

請直接聯絡維護者（aiken@megapower.asia 或透過內部管道）。

包含：
- 描述漏洞
- 重現步驟
- 受影響版本
- 可能的影響

我們會在 48 小時內回應，並在修復後公開致謝（若適用）。

## 其他

- 依賴安全：使用 `pip-audit` 與 GitHub Dependabot。
- 無硬編碼 secret（gitleaks 檢查）。
