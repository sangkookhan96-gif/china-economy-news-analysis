#!/bin/bash
# run_daily_selection.sh - Wrapper for edition-based daily_news_selector.py
# Usage: run_daily_selection.sh <morning|afternoon|evening> [cron|manual]
# Prevents duplicate runs via PID-based lock file per edition.

set -euo pipefail

PROJECT_DIR="/home/jeozeohan/vibe_temp/China Economy News Analysis"
LOG_DIR="/home/jeozeohan/logs"
PYTHON="/usr/bin/python3"
SCRIPT="src/agents/daily_news_selector.py"

# Ensure user site-packages are available in cron environment
export PYTHONPATH="/home/jeozeohan/.local/lib/python3.10/site-packages${PYTHONPATH:+:$PYTHONPATH}"
export HOME="/home/jeozeohan"

# Parse arguments
EDITION="${1:-}"
CALLER="${2:-cron}"

if [ -z "$EDITION" ] || [[ ! "$EDITION" =~ ^(morning|afternoon|evening)$ ]]; then
    echo "Usage: $0 <morning|afternoon|evening> [cron|manual]"
    exit 1
fi

# Edition-specific lock file and log file
LOCK_FILE="/home/jeozeohan/.china_news_selector_${EDITION}.lock"
LOG_FILE="${LOG_DIR}/daily_news_${EDITION}_${CALLER}.log"

mkdir -p "$LOG_DIR"

# PID-based stale lock cleanup and duplicate prevention
if [ -f "$LOCK_FILE" ]; then
    OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$CALLER] Another $EDITION instance (PID $OLD_PID) is running. Exiting." >> "$LOG_FILE"
        exit 0
    fi
    # Stale lock — process is gone, clean up
    rm -f "$LOCK_FILE"
fi

# Write our PID as lock
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

{
    echo "=========================================="
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$CALLER] Starting $EDITION edition selection (PID $$)"
    echo "=========================================="

    cd "$PROJECT_DIR"
    "$PYTHON" "$SCRIPT" --edition "$EDITION"

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$CALLER] $EDITION edition completed successfully"
    echo ""
} >> "$LOG_FILE" 2>&1
