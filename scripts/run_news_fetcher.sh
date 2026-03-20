#!/bin/bash

PYTHON=/opt/miniconda3/envs/market_env/bin/python
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/news_fetcher_$(date +%Y-%m-%d).log"
SUCCESS_FILE="$PROJECT_DIR/logs/news_fetcher_last_success.txt"

mkdir -p "$PROJECT_DIR/logs"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] news_fetcher starting" >> "$LOG_FILE"

cd "$PROJECT_DIR"
$PYTHON data/news_fetcher.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S')" > "$SUCCESS_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] news_fetcher finished successfully" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] news_fetcher FAILED with exit code $EXIT_CODE" >> "$LOG_FILE"
fi

exit $EXIT_CODE
