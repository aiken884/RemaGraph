#!/usr/bin/env bash
# 僅在已開好的 pane 內執行 opencode。開窗請用 dispatch-fleet.sh → open-fleet-window.sh
set -euo pipefail
ID="${1:?}"
MODEL="${2:-opencode-go/deepseek-v4-pro}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROMPT="$ROOT/.command-tower/tasks/prompts/${ID}.md"
OUTDIR="${COMMAND_TOWER_LOG_DIR:-$ROOT/.command-tower/logs}"
mkdir -p "$OUTDIR"
LOG="$OUTDIR/${ID}.out"
cd "$ROOT"
MSG="$(cat "$PROMPT")"
exec > >(tee "$LOG") 2>&1
exec opencode run -m "$MODEL" --auto --title "RemaGraph-${ID}" -- "$MSG"
