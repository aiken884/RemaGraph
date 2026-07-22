#!/bin/bash
#
# remagraph-task.sh — 非技術使用者專用「一鍵任務記憶」
#
# 使用方式（超簡單，兩個變數就好）：
#   TASK_ID=fix-login-001 AGENT_ID=my-ai ./remagraph-task.sh python my_agent.py
#
# 或完全不設變數（會自動產生）：
#   ./remagraph-task.sh echo "hello"
#
# 它會自動：
#   1. 開始時讀取之前相關記憶
#   2. 執行你的任務
#   3. 結束時自動儲存結果
#
# 注意：TASK_ID / AGENT_ID 只能用英文、數字、底線、連字號。
# 若已安裝 remagraph CLI，也可用更簡單的：
#   remagraph auto --task-id fix-login-001 --agent-id my-ai -- python my_agent.py

set -u

TASK_ID="${TASK_ID:-task-$(date +%Y%m%d-%H%M%S)}"
AGENT_ID="${AGENT_ID:-default-agent}"

# 若系統已有 remagraph auto，優先用它（更完整）
if command -v remagraph >/dev/null 2>&1; then
  exec remagraph auto --task-id "$TASK_ID" --agent-id "$AGENT_ID" -- "$@"
fi

# 後備：純 shell 實作（remagraph 不在 PATH 時）
echo "=== RemaGraph 任務開始 ===" >&2
echo "任務編號: $TASK_ID" >&2
echo "執行者: $AGENT_ID" >&2
echo "" >&2

echo ">>> 自動讀取之前記憶..." >&2
remagraph search --task-id "$TASK_ID" --top-k 5 2>/dev/null \
  || echo "(目前沒有之前記憶，繼續執行)" >&2

echo "" >&2
echo ">>> 開始執行你的任務..." >&2
echo "--------------------------------" >&2

set +e
"$@"
EXIT_CODE=$?
set -e

echo "--------------------------------" >&2
echo "" >&2
echo ">>> 任務結束，自動儲存記憶..." >&2

SUMMARY="任務完成，退出碼=${EXIT_CODE}。執行時間: $(date)。指令: $*"
# 補足至少 30 字元（RemaGraph 規則）
while [ "${#SUMMARY}" -lt 30 ]; do
  SUMMARY="${SUMMARY}（自動補足）"
done

remagraph store \
  --task-id "$TASK_ID" \
  --agent-id "$AGENT_ID" \
  --kind status_update \
  --summary "$SUMMARY" \
  --tags '["auto","wrapper"]' 2>/dev/null \
  || echo "(記憶儲存時發生小問題，但不影響你的任務結果)" >&2

echo "" >&2
echo "=== RemaGraph 任務結束 (task_id: $TASK_ID) ===" >&2

exit "$EXIT_CODE"
