#!/bin/bash
#
# For towers using the herdr Bridge
# Minimal memory helper (usable as-is by non-technical users)
#
# Usage: in your tower script:
#   MEMORY=$(./simple-memory-helper.sh get "task-001")
#   then insert $MEMORY into the text sent to the agent
#
# Or auto-wrap:
#   ./simple-memory-helper.sh wrap "task-001" "agent-name" "your original instruction"
#
# A tower that just wants to recall (without running) can use directly:
#   remagraph auto --recall-only --task-id "task-001" --agent-id "..."
# cross project test (Option B): bridge uses the herdr project, shareable on the RemaGraph side.

set -e

CMD=$1
TASK_ID=$2
AGENT_ID=${3:-default-agent}
INSTRUCTION=${4:-""}

case $CMD in
  get)
    echo ">>> recalling memories..."
    remagraph search --task-id "$TASK_ID" --top-k 5 2>/dev/null || echo "(no prior memories)"
    ;;
  wrap)
    echo "Task:  $TASK_ID"
    echo "Agent: $AGENT_ID"
    echo ""
    echo ">>> prior memories:"
    remagraph search --task-id "$TASK_ID" --top-k 5 2>/dev/null || echo "(no prior memories found)"
    echo ""
    echo ">>> please follow: you may recall memory again at the start, log key progress with remagraph store, and end with task_handoff"
    echo ""
    echo "original instruction:"
    echo "$INSTRUCTION"
    ;;
  *)
    echo "Usage: $0 get TASK_ID"
    echo "   or: $0 wrap TASK_ID AGENT_ID 'instruction'"
    exit 1
    ;;
esac
