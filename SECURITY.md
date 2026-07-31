# Security Policy

## English

### Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: (active development, pre-release) |
| < 0.2   | :x:                |

### Reporting a Vulnerability

Please **do not** open a public issue.

Contact a maintainer directly at aiken@megapower.asia (or through an internal channel).

Please include:
- A description of the vulnerability
- Steps to reproduce
- Affected version(s)
- Potential impact

We aim to respond within 48 hours, and will credit reporters publicly after a fix ships (if desired).

### Other Security Practices

- Dependency security is checked with `pip-audit` and GitHub Dependabot.
- No hardcoded secrets (enforced via gitleaks scanning).

## 繁體中文

### 支援版本

| 版本 | 是否支援 |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark:（開發中，發行準備） |
| < 0.2   | :x:                |

### 回報安全漏洞

請**不要**公開 issue。

請直接聯絡維護者（aiken@megapower.asia 或透過內部管道）。

回報內容請包含：
- 漏洞描述
- 重現步驟
- 受影響版本
- 可能造成的影響

我們會在 48 小時內回應，並在修復後公開致謝回報者（若你希望如此）。

### 其他安全措施

- 依賴套件安全性：使用 `pip-audit` 與 GitHub Dependabot 檢查。
- 不得有硬編碼 secret（透過 gitleaks 掃描把關）。
