# RemaGraph 內部 Alpha 測試 Playbook

**目的**：讓內部團隊快速上手，並收集真實使用回饋，持續讓它變得「極簡」。

**適用對象**：自動派工系統開發者、headless agent 維護者、非技術使用者測試者。

**現況**：工具層 + 治理層已就緒。測試重點在 RemaGraph wrapper 與上游派工流程的整合。

---

## 快速開始（5 分鐘）

1. 安裝（任何一台有 uv 的機器）：
   ```bash
   uv tool install git+https://github.com/aiken884/RemaGraph.git
   remagraph init --project alpha-test
   source ~/.local/state/remagraph-alpha-test/env.sh
   ```

2. 下載包裝腳本（推薦非技術者）：
   ```bash
   curl -O https://raw.githubusercontent.com/aiken884/RemaGraph/main/examples/simple/remagraph-task.sh
   chmod +x remagraph-task.sh
   ```

3. 第一次測試（用簡單指令）：
   ```bash
   TASK_ID=alpha-test-001 AGENT_ID=test-agent ./remagraph-task.sh echo "這是第一次測試"
   ```

4. 看記憶：
   ```bash
   remagraph search --task-id alpha-test-001
   remagraph status
   ```

---

## 建議的測試場景（請至少試 2-3 個）

### A. 單一任務連續執行
- 同一個 TASK_ID 下執行 2-3 次不同指令。
- 觀察第二次能不能看到第一次的記憶。

### B. 任務交接（handoff）
- 用 `--kind task_handoff` 寫入。
- 另一個 agent 用同 task_id 讀取，確認能接手。

### C. 自動派工情境（最重要）
- 模擬派工系統：
  - 先用 `--recall-only` 查記憶
  - 再用 `remagraph auto` 或 `remagraph-task.sh` 包裝實際 agent 指令
- 確認 task_id / agent_id 能正確帶入。

### D. 非技術使用者體驗
- 請一位「不寫程式」的人，只用環境變數 + 包裝腳本跑一次。
- 記錄他卡在哪一步。

### E. 錯誤與邊界
- 故意用很短的 summary（看有沒有自動補足）
- 用同 task_id 不同 agent
- 查不存在的 task_id

---

## 任務命名建議（強制養成習慣）

- 格式：`專案-日期-簡短描述-短碼`
- 範例：
  - `fix-login-20260722-a3f2`
  - `daily-brief-20260722`
  - `demo-alpha-test-001`

**不要用中文、空白、特殊符號。**

---

## 回饋收集方式（每次測試後請填）

請用簡單文字回覆（可直接貼 Slack / 內部頻道 / 回覆這份文件）：

```
測試日期：2026-07-22
測試者：Aiken
測試場景：單一任務連續執行 + 派工前 recall-only
task_id：alpha-test-001

【好用之處】
- ...

【卡住 / 不清楚的地方】
- ...

【希望改進】
- 更簡單？還是加功能？
- ...

【其他建議】
```

---

## 內部共識（目前階段）

- 這是 **內部 Alpha**，不對外公開、不上 PyPI。
- 目標是「極簡到連非技術者都不用想太多」。
- 目前最推薦用法：
  - 非技術：`remagraph-task.sh` + 兩個環境變數
  - 派工系統：`remagraph auto --recall-only` 先查 + 自動包裝執行
- 所有測試資料都留在本地 `~/.local/state/remagraph-*/`

---

## 下一步（測試後）

1. 收集 3–5 筆真實回饋
2. 討論是否需要調整 CLI 或文件
3. 再決定是否要繼續強化「派工前自動注入記憶」功能

---

**記住：我們現在的優先級是「簡單」，不是「功能完整」。**

有任何問題直接問 Aiken 或在內部討論。
