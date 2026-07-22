# RemaGraph × herdr Bridge 整合實作計劃
## 自動化任務記憶 for Headless Agents

**版本**：v0.3（內部 Alpha 測試版）  
**日期**：2026-07-22  
**目標**：讓指揮塔派工給 Headless Agents 時，能自動讀取與儲存 RemaGraph 記憶，同時支援兩類使用者。重點：安裝、設定、使用過程要極簡，適合非重度技術或開發人員使用者。  
**狀態**：目前僅供內部使用，Alpha 測試階段，尚未對外發布。  
**單一真相來源 (SOT)**：本文件 + `docs/task-memory-convention.md` + RemaGraph CLI

### 實作狀態（2026-07-22）

| 項目 | 狀態 |
|------|------|
| `remagraph init` | ✅ 已實作 |
| `remagraph auto` | ✅ 已實作 |
| `search --task-id`（可無 query） | ✅ 已實作 |
| `examples/simple/remagraph-task.sh` | ✅ 已實作 |
| `examples/herdr-bridge/*` | ✅ 已實作 |
| `docs/task-memory-convention.md` | ✅ 已實作 |
| `scripts/one-key-install.sh` | ✅ 已實作 |
| CLI 測試 `tests/test_cli_init_auto.py` | ✅ 已實作 |
| README 白話快速開始 | ✅ 已更新 |
| herdr-org 指揮塔正式接入 | ⏳ 待使用者專案側接入 |
| curl 遠端一鍵安裝 URL 上線 | ⏳ 待 push / release |

### 1. 背景與問題
- RemaGraph 提供 CLI（store/search/status/init/auto）適合 headless 使用。
- herdr Bridge 派工方式：`send_to_agent(text=...)` 或 `acp.prompt(text=...)`（文字注入）。
- 使用者分兩類：已使用 herdr Bridge、未使用 herdr Bridge。
- 非技術使用者也必須 5 分鐘上手。

### 2. 目標
- 指揮塔派工時可自動 recall 並注入上下文。
- 兩類使用者都能完整使用 store/search/status。
- **極簡體驗（最高優先）**：
  - 安裝：一行（`uv tool install` 或 `scripts/one-key-install.sh`）
  - 設定：`remagraph init` + `source env.sh`
  - 使用：`remagraph auto ...` 或 `./remagraph-task.sh ...`
- 無耦合 herdr；向後相容。

### 3. 設計原則
- 極簡優先、CLI 優先、task_id 核心
- 兩層自動化：指揮塔預 recall；agent / wrapper 自動 store
- 記憶失敗不阻斷主任務
- 文件白話、範例可複製貼上

### 4. 架構（已落地）

#### 共同
```bash
remagraph init --project myproject
source ~/.local/state/remagraph-myproject/env.sh
remagraph auto --task-id T --agent-id A -- <cmd>
```

#### herdr Bridge 使用者
- 用 `examples/herdr-bridge/dispatch_with_memory.py` 組 prompt
- agent 端用 `remagraph auto` 包住真正工作

#### 非 herdr 使用者
- 用 `remagraph auto` 或 `examples/simple/remagraph-task.sh`

### 5. 檔案清單
- `src/remagraph/cli.py` — init / auto / search 強化
- `src/remagraph/search.py` — task_id-only 列表模式
- `src/remagraph/server.py` — 路由 init/auto
- `src/remagraph/models.py` — SearchRequest.query 預設空字串
- `examples/simple/remagraph-task.sh`
- `examples/herdr-bridge/simple-memory-helper.sh`
- `examples/herdr-bridge/dispatch_with_memory.py`
- `docs/task-memory-convention.md`
- `scripts/one-key-install.sh`
- `tests/test_cli_init_auto.py`

### 6. 驗收
- [x] `remagraph init` 建立目錄與 env.sh
- [x] `remagraph auto` 可 recall + store
- [x] `remagraph search --task-id` 可無 query
- [x] 非 herdr wrapper 可用
- [x] herdr 範例可用
- [ ] 全測試 / lint 通過（本輪執行中）
- [ ] 遠端 raw URL 可下載（需 push）

### 7. 下一步
1. 本機跑完 ruff / mypy / pytest
2. 使用者確認後 commit
3. herdr-org 指揮塔側接入 `dispatch_with_memory`
4. release 後啟用 curl 一鍵安裝 URL
