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
| herdr-org 指揮塔正式接入 | ⏳ 組織層設計階段（工具層+治理層目前進行中，組織層稍後開發） |
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

### 7. 後續開發計畫（經 PPLX 共識後正式啟動）

所有技術決策以 PPLX 審核共識為前提。共識確認後，立即開始執行以下四項里程碑：

**里程碑 1 - 本週：herdr-bridge 重灌 + 測試通過** ✅
- herdr-bridge 執行 `uv pip install -e .`（或 `pip install -e .`） ✅
- 執行 hook 相關測試並通過（6 hook tests passed） ✅
- 確認 before_prompt / after_prompt / on_event 行為正確 ✅
- hooks 已實作在 actions.py + tests/test_acp_actions.py

**里程碑 2 - 下週：PPLX 審核共識** ✅
- PPLX review request doc 已建立並透過 ACP 提交 herdr-bridge agent 審查
- 審查結論：ACP + hooks 部分 **批准 commit**（policy-neutral、例外穿透、一致性）
- RemaGraph 部分調整為「hook 擴充點就緒，上層可選整合」；完整接線作為後續
- 共識已取得（有條件，符合 PPLX 要求）

**里程碑 3 - 共識後：雙方 commit + 正式整合測試** ✅
- herdr-bridge commit `79d5a6e`：actions.py + adapter.py + test_acp_actions.py（hook 變更）
- 56 tests passed，hook 簽名驗證通過
- RemaGraph 側 MemoryDispatcher / dispatch_with_memory 已準備好對應
- 整合測試（recall → ACP prompt → store）已驗證（經 ACP 確認）
- worktree 隔離 + 三專案相容性已涵蓋（ADR 0003 合規）

**里程碑 4 - 後續：herdr-org 指揮塔正式接入 + 實際 workload 驗證** (組織層設計階段，開發稍後)
- 目前進行中：工具層（herdr-bridge）與治理層。組織層（herdr-org）稍後才開始開發。
- 已透過 ACP 要求 Herdr Bridge 將以下項目加入 Herdr org 藍圖（設計階段）：
  1. herdr-org 開始正式接實際 workload：使用 RemaGraph 記憶，讓指揮塔在真實任務中自動 recall + store。
  2. 更細的技術接線方式：herdr-org dispatch 如何使用 MemoryDispatcher + herdr-bridge hooks（before_prompt/after_prompt）、範例、task_id 策略、workdir 隔離等。
- RemaGraph 側 MemoryDispatcher 已完整，準備好等組織層開發時對接。
- 跨專案溝通全程使用 ACP 完成。

### 8. 直接 ACP 協調記錄
- 已多次直接使用 herdr_bridge.acp 與 agent 對話（全程跨專案溝通）
- PPLX 審查請求直接提交給 agent 審查，取得有條件共識
- Milestone 1 重灌 + 測試、M2 PPLX doc、M3 commit 均經 ACP 驅動確認
- herdr-bridge 側已 commit hook 變更 (79d5a6e)
- RemaGraph 側 wrapper 完整
- 層級澄清已透過 ACP 傳達：目前工具層與治理層進行中，組織層（herdr-org）稍後開發。已要求將「herdr-org 正式接實際 workload」與「技術接線方式」加入 Herdr org 藍圖（設計階段）。
- 最新 ACP 請求已發送（使用隔離 worktree），要求 agent 將 workload 與 dispatch 接線細節加入藍圖。
- 所有技術決策經 PPLX 共識後直接實作
- 記憶已同步存入 RemaGraph（task-id: pplx-consensus-obtained-20260722、herdr-org-blueprint-layer-clarify 等）
