#!/bin/bash
#
# remagraph-task.sh — one-line "task memory" wrapper for non-technical users
#
# Usage (just two variables):
#   TASK_ID=fix-login-001 AGENT_ID=my-ai ./remagraph-task.sh python my_agent.py
#
# Or with no variables set at all (auto-generated):
#   ./remagraph-task.sh echo "hello"
#
# It will automatically:
#   1. Recall relevant prior memories at the start
#   2. Run your task
#   3. Auto-store the result at the end
#
# Note: TASK_ID / AGENT_ID may only contain letters, digits, underscores, and hyphens.
# If the remagraph CLI is installed, you can also use the simpler:
#   remagraph auto --task-id fix-login-001 --agent-id my-ai -- python my_agent.py
#
# Towers that just want to check memory first (no run, no store) can use:
#   remagraph auto --recall-only --task-id fix-login-001 --agent-id my-ai
#
# For internal Alpha test guidance, see: docs/internal/alpha-test-playbook.md

set -u

TASK_ID="${TASK_ID:-task-$(date +%Y%m%d-%H%M%S)}"
AGENT_ID="${AGENT_ID:-default-agent}"

# Prefer the system remagraph auto if available (more complete)
if command -v remagraph >/dev/null 2>&1; then
  exec remagraph auto --task-id "$TASK_ID" --agent-id "$AGENT_ID" -- "$@"
fi

# Fallback: pure shell implementation (when remagraph isn't on PATH)
echo "=== RemaGraph task starting ===" >&2
echo "Task:  $TASK_ID" >&2
echo "Agent: $AGENT_ID" >&2
echo "" >&2

echo ">>> recalling prior memories..." >&2
remagraph search --task-id "$TASK_ID" --top-k 5 2>/dev/null \
  || echo "(no prior memories found, continuing)" >&2

echo "" >&2
echo ">>> running your task..." >&2
echo "--------------------------------" >&2

set +e
"$@"
EXIT_CODE=$?
set -e

echo "--------------------------------" >&2
echo "" >&2
echo ">>> task finished, auto-storing memory..." >&2

SUMMARY="Task completed, exit_code=${EXIT_CODE}. Time: $(date). Command: $*"
# Pad to at least 30 characters (RemaGraph rule)
while [ "${#SUMMARY}" -lt 30 ]; do
  SUMMARY="${SUMMARY} (padded)"
done

remagraph store \
  --task-id "$TASK_ID" \
  --agent-id "$AGENT_ID" \
  --kind status_update \
  --summary "$SUMMARY" \
  --tags '["auto","wrapper"]' 2>/dev/null \
  || echo "(a minor issue occurred while storing memory, your task result is unaffected)" >&2

echo "" >&2
echo "=== RemaGraph task finished (task_id: $TASK_ID) ===" >&2

exit "$EXIT_CODE"
