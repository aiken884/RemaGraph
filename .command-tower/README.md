# RemaGraph 指揮塔派工（開窗）

## 硬規則

開新艦隊 pane **只能**走：

```bash
source ~/.command-tower/RemaGraph/session.env
bash ~/.command-tower/bin/open-fleet-window.sh <base_pane_id> <cwd> <label>
```

或本專案封裝（推薦）：

```bash
source ~/.command-tower/RemaGraph/session.env
bash .command-tower/tasks/dispatch-fleet.sh <label> <prompt_id> [model]
# 例：
bash .command-tower/tasks/dispatch-fleet.sh rg-wu9 WU9 opencode-go/deepseek-v4-pro
```

`open-fleet-window.sh` 會：

- 依 `display-mode` 選 mobile（獨立 tab）／desktop（分割）
- **Fix3**：`base_pane` 必須屬於 `CT_WORKSPACE`（RemaGraph = `wQ`），否則 **fail-closed**

**禁止**：`herdr pane split --pane <id>` 手填 id、或 `herdr agent start` 不帶 workspace／不經 open-fleet-window 另開窗。

## 環境

| 變數 | RemaGraph 值 |
|------|----------------|
| `CT_PROJECT` | `RemaGraph` |
| `CT_WORKSPACE` | `wQ` |
| `CT_TOWER_PANE` | 指揮塔 pane（如 `wQ:p1`） |
| `CT_WORKDIR` | repo 路徑 |

見 `~/.command-tower/RemaGraph/session.env`。

## 顯示模式

```bash
export CT_PROJECT=RemaGraph CT_WORKSPACE=wQ
bash ~/.command-tower/bin/display-mode.sh set desktop --project
# 搬動既有 pane：python3 ~/.command-tower/bin/switch-display-mode.py desktop wQ "$CT_TOWER_PANE"
```

指引：Vault `herdr 艦隊顯示模式指引（Mobile／Desktop）.md`
