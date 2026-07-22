#!/bin/bash
#
# 給使用 herdr Bridge 的指揮塔使用
# 極簡記憶幫手（非技術者可直接複製使用）
#
# 用法：在你的指揮塔腳本中：
#   MEMORY=$(./simple-memory-helper.sh get "task-001")
#   然後把 $MEMORY 塞進送給 agent 的文字裡
#
# 或者自動包裝：
#   ./simple-memory-helper.sh wrap "task-001" "agent-name" "你的原始指令"
#
# 指揮塔想只先 recall（不執行）可直接用：
#   remagraph auto --recall-only --task-id "task-001" --agent-id "..." 

set -e

CMD=$1
TASK_ID=$2
AGENT_ID=${3:-default-agent}
INSTRUCTION=${4:-""}

case $CMD in
  get)
    echo ">>> 自動讀取記憶..."
    remagraph search --task-id "$TASK_ID" --top-k 5 2>/dev/null || echo "（無之前記憶）"
    ;;
  wrap)
    echo "任務編號：$TASK_ID"
    echo "執行者：$AGENT_ID"
    echo ""
    echo ">>> 之前記憶："
    remagraph search --task-id "$TASK_ID" --top-k 5 2>/dev/null || echo "（目前沒有之前記憶）"
    echo ""
    echo ">>> 請遵守：開始時可再查記憶，關鍵進度用 remagraph store 記錄，結束用 task_handoff"
    echo ""
    echo "原始指令："
    echo "$INSTRUCTION"
    ;;
  *)
    echo "用法: $0 get TASK_ID"
    echo "   或: $0 wrap TASK_ID AGENT_ID '指令'"
    exit 1
    ;;
esac
