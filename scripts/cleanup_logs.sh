#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"

deleted=$(find "$LOG_DIR" -name "*.log" -mtime +14 -print -delete | wc -l | tr -d ' ')
echo "[$(date '+%Y-%m-%d %H:%M:%S')] cleanup_logs: removed $deleted log file(s) older than 14 days."
