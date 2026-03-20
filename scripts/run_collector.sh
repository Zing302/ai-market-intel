#!/bin/bash

PYTHON=/opt/miniconda3/envs/market_env/bin/python
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/collector_$(date +%Y-%m-%d).log"
SUCCESS_FILE="$PROJECT_DIR/logs/collector_last_success.txt"

mkdir -p "$PROJECT_DIR/logs"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] collector starting" >> "$LOG_FILE"

cd "$PROJECT_DIR"
$PYTHON data/collector.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S')" > "$SUCCESS_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] collector finished successfully" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] collector FAILED with exit code $EXIT_CODE" >> "$LOG_FILE"
fi

exit $EXIT_CODE
