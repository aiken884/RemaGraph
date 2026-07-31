# Contributing to RemaGraph

This project maintains its contribution guide in both English and Traditional Chinese (繁體中文). Please read the version you're most comfortable with — both describe the same rules.

本專案的貢獻指南同時提供英文與台灣繁體中文版本，內容一致，請選擇您慣用的語言閱讀。

---

## English

Thanks for your interest in contributing to RemaGraph! The guidelines below help us keep the codebase healthy and the review process smooth.

### Table of Contents

- [Development Setup](#development-setup)
- [Testing](#testing)
- [Code Style](#code-style)
- [Lint / Type-Check](#lint--type-check)
- [Commit Format](#commit-format)
- [Pull Request Process](#pull-request-process)
- [Security Considerations](#security-considerations)
- [CI Pipeline](#ci-pipeline)

### Development Setup

RemaGraph uses **uv** (Python 3.11+) for dependency management.

```bash
# Clone the repo
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph

# Create a virtualenv and install all dependencies (including dev)
uv sync --extra dev

# Enable the pre-commit hook (recommended)
uv run pre-commit install
```

`uv sync --extra dev` installs the dev toolchain: pytest, ruff, mypy, mutmut, pre-commit, and friends. If you need SQLite-vec vector support, add `--extra vector`, or use `--all-extras` for everything.

### Testing

#### General Tests

```bash
# Fast run (skips tests marked "slow")
uv run pytest -m "not slow"

# Full run with coverage (minimum threshold: 80%)
uv run pytest --cov=src/remagraph --cov-fail-under=80 --cov-report=term
```

#### Smoke Tests

Smoke tests use an isolated state directory and **must never write to a production** `REMAGRAPH_STATE_DIR`:

```bash
REMAGRAPH_STATE_DIR=$(mktemp -d) uv run pytest tests/smoke/ -v
```

#### Mutation Testing (mutmut)

Mutation testing verifies the *quality* of the test suite itself, and is run only against core logic modules:

```bash
uv run mutmut run
uv run mutmut results
```

A surviving mutant means the tests aren't catching a real behavior change — **add a test to kill it; don't lower the bar.**

### Code Style

We use **ruff** as both formatter and linter, configured in `pyproject.toml`:

| Setting | Rule |
|---------|------|
| Line length | 100 characters (`line-length = 100`) |
| Quote style | Double quotes (`quote-style = "double"`) |
| Lint rules | E, F, I, N, W |

```bash
# Check
uv run ruff check .

# Auto-fix + format
uv run ruff check --fix .
uv run ruff format .
```

### Lint / Type-Check

Before submitting, make sure the following commands pass:

```bash
uv run ruff check .
uv run mypy src/
```

- `ruff check .` — static analysis and import sorting
- `mypy src/` — type checking (source only for now; test files are exempt for the time being)

### Commit Format

We follow **Conventional Commits**:

```
<type>: <short description>

<optional longer description>
```

Allowed `<type>` values:

| Type | When to use it |
|------|-----------------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation changes |
| `chore` | Miscellaneous (dependency bumps, CI, config, etc.) |
| `refactor` | Refactoring with no behavior change |
| `test` | Test-related changes |
| `style` | Code formatting (no logic change) |
| `perf` | Performance improvement |

For breaking changes, append `!` after the type, e.g. `feat!: remove legacy API`.

### Pull Request Process

1. **Branch off `main`**, using the naming convention `<type>/<short-description>` (e.g. `feat/add-vector-search`, `fix/state-race-condition`).
2. **At least one reviewer approval** is required before merging.
3. **Use squash merge** into `main` to keep commit history clean.
4. Before merging, check the PR against the template checklist:
   - `ruff check .` passes
   - `pytest -m "not slow"` passes
   - `mypy src/` passes
   - No new secrets or credentials introduced
   - CHANGELOG updated (if applicable)

If you find an issue or are unsure about a design decision, please open an **issue** to discuss it before submitting a PR.

### Security Considerations

RemaGraph stores memory data produced by agent execution, so security is a shared responsibility across all contributors:

- The **gitleaks pre-commit hook** scans for leaked credentials automatically on every commit once installed.
- **API keys, tokens, and passwords must never enter the repo**; use environment variables or isolate them via `REMAGRAPH_STATE_DIR`.
- Every PR triggers **gitleaks** and **pip-audit** (dependency vulnerability scanning) in CI.
- State directories used in tests must be isolated (`tmp_path` or `mktemp -d`) — never write to a real environment.

If you discover a security issue, please open an issue directly (public disclosure is discouraged) or email the maintainers.

### CI Pipeline

CI runs the following steps on every PR:

1. **smoke** → smoke tests across platforms (ubuntu + macos) and Python versions (3.11 / 3.12 / 3.13)
2. **adversarial** → fuzz testing and state-tampering tests
3. **lint** → `ruff check .`
4. **test + coverage** → `pytest --cov --cov-fail-under=80`
5. **gitleaks** → secret scanning (non-blocking)
6. **pip-audit** → dependency vulnerability scanning (non-blocking)
7. **mutmut** → mutation testing (non-blocking)

Blocking steps (1-4) failing means the PR **cannot be merged**. Non-blocking steps (5-7) failing should be fixed in a follow-up PR — **they must not be bypassed.**

---

## 繁體中文

歡迎貢獻 RemaGraph。以下規範幫助我們維持程式碼品質與專案健康。

### 目錄

- [開發環境設定](#開發環境設定)
- [測試](#測試)
- [程式碼風格](#程式碼風格)
- [Lint / Type-Check](#lint--type-check-1)
- [Commit 格式](#commit-格式)
- [PR 流程](#pr-流程)
- [安全考量](#安全考量)
- [CI Pipeline](#ci-pipeline-1)

### 開發環境設定

RemaGraph 使用 **uv**（Python 3.11+）管理依賴。

```bash
# 複製專案
git clone https://github.com/aiken884/RemaGraph.git
cd RemaGraph

# 建立虛擬環境並安裝所有依賴（含 dev）
uv sync --extra dev

# 啟用 pre-commit hook（建議）
uv run pre-commit install
```

`uv sync --extra dev` 會安裝 pytest、ruff、mypy、mutmut、pre-commit 等開發工具。若需 SQLite-vec 向量支援，加上 `--extra vector` 或改用 `--all-extras`。

### 測試

#### 一般測試

```bash
# 快速測試（跳過耗時標記）
uv run pytest -m "not slow"

# 全部測試（含 coverage，低標 80%）
uv run pytest --cov=src/remagraph --cov-fail-under=80 --cov-report=term
```

#### 冒煙測試

冒煙測試使用隔離的狀態目錄，**不應寫入生產環境**的 `REMAGRAPH_STATE_DIR`：

```bash
REMAGRAPH_STATE_DIR=$(mktemp -d) uv run pytest tests/smoke/ -v
```

#### 突變測試（mutmut）

驗證測試本身的品質，只針對核心邏輯模組：

```bash
uv run mutmut run
uv run mutmut results
```

存活（surviving）的 mutant 表示測試覆蓋不足，**請補測試而非調低門檻**。

### 程式碼風格

採用 **ruff** 作為 formatter 與 linter，設定寫在 `pyproject.toml`：

| 項目 | 規範 |
|------|------|
| 行長限制 | 100 字元（`line-length = 100`） |
| 引號風格 | 雙引號（`quote-style = "double"`） |
| lint rules | E, F, I, N, W |

```bash
# 檢查
uv run ruff check .

# 自動修正 + format
uv run ruff check --fix .
uv run ruff format .
```

### Lint / Type-Check

提交前請確保以下指令通過：

```bash
uv run ruff check .
uv run mypy src/
```

- `ruff check .` — 靜態分析與 import sorting
- `mypy src/` — 型別檢查（僅限 source，測試暫時放寬）

### Commit 格式

採用 **Conventional Commits**：

```
<type>: <簡短描述>

<選擇性詳細說明>
```

允許的 `<type>`：

| type | 使用時機 |
|------|---------|
| `feat` | 新功能 |
| `fix` | 錯誤修正 |
| `docs` | 文件異動 |
| `chore` | 雜項（依賴更新、CI、設定等） |
| `refactor` | 重構（無行為變動） |
| `test` | 測試相關 |
| `style` | 程式碼格式（不影響邏輯） |
| `perf` | 效能改善 |

Breaking change 在 type 後加 `!`，例如 `feat!: 移除舊版 API`。

### PR 流程

1. **Branch 從 `main` 開出**，命名慣例：`<type>/<簡短描述>`（如 `feat/add-vector-search`、`fix/state-race-condition`）
2. **至少 1 位 reviewer 核准**後方可合併
3. **使用 squash merge** 進 `main`，保持 commit history 乾淨
4. 合併前請對照 PR template 檢查 checklist：
   - `ruff check .` 通過
   - `pytest -m "not slow"` 通過
   - `mypy src/` 通過
   - 無新的 secret / credential 引入
   - CHANGELOG 已更新（若適用）

若發現問題或有不確定的設計，先開 **issue** 討論再發 PR。

### 安全考量

RemaGraph 儲存 agent 執行的記憶資料，安全由所有貢獻者共同維護：

- **gitleaks pre-commit hook** 自動掃描 credential 洩漏，安裝後每次 commit 都會執行
- **API key、token、密碼一律不進 repo**；使用環境變數或 `REMAGRAPH_STATE_DIR` 隔離
- PR 會觸發 CI 中的 **gitleaks** 與 **pip-audit**（相依性安全掃描）
- 測試中使用的 state 目錄必須隔離（`tmp_path` 或 `mktemp -d`），禁止寫入真實環境

若發現安全問題，請直接開 issue（不建議公開揭露），或寄信給維護者。

### CI Pipeline

PR 觸發的 CI 順序：

1. **smoke** → 多平台（ubuntu + macos）、多 Python 版本（3.11 / 3.12 / 3.13）冒煙測試
2. **adversarial** → 模糊測試與狀態竄改測試
3. **lint** → `ruff check .`
4. **test + coverage** → `pytest --cov --cov-fail-under=80`
5. **gitleaks** → secret 掃描（non-blocking）
6. **pip-audit** → 依賴漏洞掃描（non-blocking）
7. **mutmut** → 突變測試（non-blocking）

Blocking 步驟（1-4）紅燈 = PR 不可合併。Non-blocking 步驟（5-7）紅燈應在後續 PR 修正，**不應繞過**。
