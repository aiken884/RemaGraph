# RemaGraph 任務記憶慣例（白話版）

> 目標：不管你是不是工程師，5 分鐘內就能讓 AI 任務「自動記得之前做過什麼」。

## 你只需要記住三件事

1. **任務編號（task_id）**：同一個工作從頭到尾用同一個編號  
2. **執行者（agent_id）**：是誰在做這件事（例如 `my-ai`、`headless-01`；只能英文數字）  
3. **一鍵指令**：`remagraph auto ...` 或 `./remagraph-task.sh ...`

---

## 5 分鐘上手（所有人）

### 步驟 1：安裝（一行）

```bash
uv tool install git+https://github.com/aiken884/RemaGraph.git
```

### 步驟 2：初始化（一行）

```bash
remagraph init --project myproject
```

照著畫面上提示，執行：

```bash
source ~/.local/state/remagraph-myproject/env.sh
```

### 步驟 3：跑任務（一行）

```bash
remagraph auto --task-id fix-login-001 --agent-id my-ai -- echo "這裡換成你的真正指令"
```

或下載包裝腳本：

```bash
curl -O https://raw.githubusercontent.com/aiken884/RemaGraph/main/examples/simple/remagraph-task.sh
chmod +x remagraph-task.sh

TASK_ID=fix-login-001 AGENT_ID=my-ai ./remagraph-task.sh echo "hello"
```

完成。系統會：
- 開始時自動讀取這個任務之前的記憶
- 結束時自動寫入結果

---

## 兩類使用者怎麼用

### A. 獨立使用（獨立 script / CI / 手動）

直接用上面的 `remagraph auto` 或 `remagraph-task.sh` 即可。

查現況：

```bash
remagraph status
remagraph search --task-id fix-login-001
```

### B. 已串接上游自動派工系統

**目前狀態**：RemaGraph 側的 MemoryDispatcher 已就緒，可供任何上游派工系統（例如企業內部的自動化派工器、CI 排程器）在派工前呼叫，取得「已注入記憶」的文字。

上游系統派工時，把「已注入記憶」的文字送給 agent 即可。

最簡單做法：參考 `build_prompt_with_memory()` 這類 helper 函式的寫法（在派工前組出含記憶摘要的 prompt），在送出指令前呼叫。

```python
from dispatch_with_memory import build_prompt_with_memory

text = build_prompt_with_memory(
    task_id="fix-login-001",
    agent_label="headless-worker-03",
    instruction="請修復登入失敗",
)
actions.send_to_agent("rule:dispatcher", agent_id, text)
```

agent 端建議在啟動時用：

```bash
remagraph auto --task-id "$TASK_ID" --agent-id "$AGENT_ID" -- <真正指令>
```

---

## 常用指令速查

| 你想做的事 | 指令 |
|---|---|
| 初始化 | `remagraph init --project 名稱` |
| 一鍵自動 | `remagraph auto --task-id T --agent-id A -- 指令` |
| 手動寫入 | `remagraph store --task-id T --agent-id A --kind status_update --summary "..."` |
| fleet 管理（協調者角色） | `remagraph store --task-id fleet --agent-id coordinator --kind fleet_member --summary "..." --tags '["member:xx"]'` |
| 查某個任務 | `remagraph search --task-id T` |
| 全文搜尋 | `remagraph search --query "關鍵字"` |
| 看最新現況 | `remagraph status` |

> 注意：`summary` 至少要 30 個字。`auto` 會自動幫你補足長度。

---

## 環境變數（可選）

| 變數 | 用途 | 預設 |
|---|---|---|
| `REMAGRAPH_STATE_DIR` | 記憶存放目錄 | `~/.local/state/remagraph/` |
| `TASK_ID` | 給 auto / wrapper 用的任務編號 | 自動產生 |
| `AGENT_ID` | 給 auto / wrapper 用的執行者 | `default-agent` |

不同專案請用不同 `REMAGRAPH_STATE_DIR`，避免記憶混在一起。

---

## 建議的記憶節奏（給 agent）

1. **開始**：`remagraph search --task-id ...`（或靠 auto 自動做）  
2. **關鍵進度**：`status_update`  
3. **結束 / 交接**：`task_handoff`  
4. **發現限制**：`discovered_constraint`  
5. **艦隊成員（僅協調者角色使用）**：`fleet_member` record/recycle（PPLX B 強制整合）

---

## 出錯怎麼辦（白話）

- 「找不到 remagraph」→ 先跑安裝指令，或確認 `uv tool update remagraph`
- 「summary 太短」→ 多寫幾個字，或改用 `remagraph auto`（會自動補）
- 「讀不到舊記憶」→ 確認 `TASK_ID` 是否同一個，以及 `REMAGRAPH_STATE_DIR` 是否相同
- 「不影響主任務」→ 記憶失敗不會讓你的主程式失敗；這是刻意設計

---

## 相關檔案

- 包裝腳本：`examples/simple/remagraph-task.sh`
- 上游派工整合範例：詳見 `examples/` 目錄下的進階範例
- 一鍵安裝：`scripts/one-key-install.sh`
- 完整整合規劃：詳見 `docs/plans/` 目錄下的整合規劃文件
