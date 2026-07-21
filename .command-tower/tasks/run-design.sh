#!/usr/bin/env bash
# Command Tower 派工腳本：執行單一設計軌（opencode + route 選定模型）
set -euo pipefail
ID="${1:?usage: run-design.sh D0N}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROMPT="$ROOT/.command-tower/tasks/prompts/${ID}.md"
OUTDIR="${COMMAND_TOWER_LOG_DIR:-$ROOT/.command-tower/logs}"
mkdir -p "$OUTDIR"
LOG="$OUTDIR/${ID}.out"
cd "$ROOT"
if [[ ! -f "$PROMPT" ]]; then
  echo "missing prompt: $PROMPT" >&2
  exit 2
fi
MSG="$(cat "$PROMPT")"
# model 來自 route()：opencode-deepseek-pro → opencode-go/deepseek-v4-pro
exec > >(tee "$LOG") 2>&1
exec opencode run \
  -m opencode-go/deepseek-v4-pro \
  --auto \
  --title "RemaGraph-${ID}-design" \
  -- "$MSG"
