#!/usr/bin/env bash
set -euo pipefail
ID="${1:?}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROMPT="$ROOT/.command-tower/tasks/prompts/${ID}.md"
OUTDIR="${COMMAND_TOWER_LOG_DIR:-$ROOT/.command-tower/logs}"
mkdir -p "$OUTDIR"
LOG="$OUTDIR/${ID}.out"
cd "$ROOT"
MSG="$(cat "$PROMPT")"
exec > >(tee "$LOG") 2>&1
exec opencode run -m opencode-go/deepseek-v4-pro --auto --title "RemaGraph-${ID}-fix" -- "$MSG"
