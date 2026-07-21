#!/usr/bin/env bash
# RemaGraph 指揮塔艦隊派工入口（唯一允許的開窗路徑）
#
# 一律經 ~/.command-tower/bin/open-fleet-window.sh 開 pane/tab：
#   - desktop：對指揮塔 pane 分割（Fix3：base 不屬於 CT_WORKSPACE 會 fail-closed）
#   - mobile：獨立 tab --no-focus
# 禁止：手動 herdr pane split / 手填錯誤 workspace 的 agent start 開窗。
#
# 用法：
#   source ~/.command-tower/RemaGraph/session.env   # 建議
#   bash .command-tower/tasks/dispatch-fleet.sh <label> <prompt_id> [model]
#   例：dispatch-fleet.sh rg-wu9 WU9 opencode-go/deepseek-v4-pro
#
set -euo pipefail

CT_ROOT="${HOME}/.command-tower"
SESSION_ENV="${CT_ROOT}/RemaGraph/session.env"
if [[ -f "$SESSION_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$SESSION_ENV"
fi

: "${CT_PROJECT:=RemaGraph}"
: "${CT_WORKSPACE:=wQ}"
export CT_PROJECT CT_WORKSPACE

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${CT_WORKDIR:=$ROOT}"
CWD="$CT_WORKDIR"

# 指揮塔 base pane
if [[ -z "${CT_TOWER_PANE:-}" ]]; then
  CT_TOWER_PANE="$(herdr pane list --workspace "$CT_WORKSPACE" | python3 -c '
import json,sys
d=json.load(sys.stdin)
panes=d["result"]["panes"]
for p in panes:
    if (p.get("agent") or "") == "grok":
        print(p["pane_id"]); break
else:
    for p in panes:
        if p.get("focused"):
            print(p["pane_id"]); break
    else:
        print(panes[0]["pane_id"] if panes else "")
')"
fi
: "${CT_TOWER_PANE:?FATAL: 設 CT_TOWER_PANE 或確保 workspace 有 grok 指揮塔 pane}"

LABEL="${1:?用法: dispatch-fleet.sh <label> <prompt_id> [model]}"
PROMPT_ID="${2:?需要 prompt_id（tasks/prompts/<id>.md）}"
MODEL="${3:-opencode-go/deepseek-v4-pro}"

PROMPT_FILE="${ROOT}/.command-tower/tasks/prompts/${PROMPT_ID}.md"
[[ -f "$PROMPT_FILE" ]] || { echo "FATAL: missing $PROMPT_FILE" >&2; exit 2; }

OUTDIR="${COMMAND_TOWER_LOG_DIR:-$ROOT/.command-tower/logs}"
mkdir -p "$OUTDIR"
LOG="${OUTDIR}/${PROMPT_ID}.out"
WIN_JSON_FILE="${OUTDIR}/${PROMPT_ID}.window.json"

echo "dispatch: project=$CT_PROJECT workspace=$CT_WORKSPACE base=$CT_TOWER_PANE label=$LABEL model=$MODEL cwd=$CWD"

# --- 開窗（唯一路徑；Fix3 workspace 驗證）---
WIN_JSON="$(bash "${CT_ROOT}/bin/open-fleet-window.sh" "$CT_TOWER_PANE" "$CWD" "$LABEL")"
printf '%s\n' "$WIN_JSON" > "$WIN_JSON_FILE"

# 解析 pane_id / tab_id
eval "$(python3 -c '
import json,sys
d=json.loads(sys.stdin.read())
r=d.get("result", d)

def pick(o):
    if not isinstance(o, dict):
        return None
    if "pane_id" in o and "tab_id" in o:
        return o["pane_id"], o["tab_id"]
    for k in ("pane", "tab"):
        if k in o and isinstance(o[k], dict) and "pane_id" in o[k]:
            p=o[k]
            return p.get("pane_id"), p.get("tab_id") or ""
    return None

x = pick(r) or pick(d)
if x is None:
    def walk(o):
        if isinstance(o, dict):
            if "pane_id" in o:
                return o.get("pane_id"), o.get("tab_id") or ""
            for v in o.values():
                y=walk(v)
                if y: return y
        if isinstance(o, list):
            for v in o:
                y=walk(v)
                if y: return y
        return None
    x = walk(d)

if not x or not x[0]:
    print("echo FATAL_PARSE; exit 3")
else:
    pane, tab = x[0], x[1] or ""
    print(f"NEW_PANE={pane!r}")
    print(f"NEW_TAB={tab!r}")
' <<<"$WIN_JSON")"

if [[ -z "${NEW_PANE:-}" ]]; then
  echo "FATAL: 無法解析 open-fleet-window 輸出中的 pane_id" >&2
  cat "$WIN_JSON_FILE" >&2
  exit 3
fi

echo "opened: pane=$NEW_PANE tab=${NEW_TAB:-?} workspace=$CT_WORKSPACE"

# --- 在新 pane 啟動 opencode（不另開窗）---
# 使用 pane run 綁定到 open-fleet-window 剛建立的 pane，避免 agent start 再開一個窗
CMD=$(printf 'export COMMAND_TOWER_LOG_DIR=%q; bash %q %q %q' \
  "$OUTDIR" \
  "${ROOT}/.command-tower/tasks/run-fleet.sh" \
  "$PROMPT_ID" \
  "$MODEL")

herdr pane run "$NEW_PANE" "$CMD" >/dev/null
herdr pane rename "$NEW_PANE" "$LABEL" 2>/dev/null || true

echo "dispatched label=$LABEL pane=$NEW_PANE model=$MODEL log=$LOG"
echo "open-fleet-window: OK (workspace-guarded)"
