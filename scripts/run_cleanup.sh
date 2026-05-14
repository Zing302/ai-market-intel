#!/bin/bash

PYTHON=/opt/miniconda3/envs/market_env/bin/python
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/cleanup_$(date +%Y-%m-%d).log"

mkdir -p "$PROJECT_DIR/logs"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] cleanup starting" >> "$LOG_FILE"

cd "$PROJECT_DIR"
bash "$PROJECT_DIR/scripts/cleanup_logs.sh" >> "$LOG_FILE" 2>&1
$PYTHON scripts/cleanup_db.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] cleanup finished successfully" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] cleanup FAILED with exit code $EXIT_CODE" >> "$LOG_FILE"
fi

exit $EXIT_CODE
