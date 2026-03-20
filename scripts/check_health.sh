#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SUCCESS_FILE="$PROJECT_DIR/logs/collector_last_success.txt"
HEALTH_LOG="$PROJECT_DIR/logs/health_$(date +%Y-%m-%d).log"
THRESHOLD_MINUTES=15

# Load email credentials from .env
set -a
source "$PROJECT_DIR/config/.env"
set +a

mkdir -p "$PROJECT_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$HEALTH_LOG"
}

send_alert_email() {
    local subject="$1"
    local body="$2"

    curl --silent --url "smtps://smtp.gmail.com:465" \
        --ssl-reqd \
        --user "$EMAIL_FROM:$EMAIL_APP_PASSWORD" \
        --mail-from "$EMAIL_FROM" \
        --mail-rcpt "$EMAIL_TO" \
        --upload-file - <<EOF
From: $EMAIL_FROM
To: $EMAIL_TO
Subject: $subject

$body
EOF
}

# Check if success file exists and was modified within threshold
if [ ! -f "$SUCCESS_FILE" ]; then
    log "ALERT: collector_last_success.txt not found — collector may never have run."
    send_alert_email \
        "[ALERT] AI Market Intel: Collector Never Run" \
        "The collector has no recorded successful run. Check the system immediately."
    log "Alert email sent."
    exit 1
fi

LAST_MODIFIED=$(date -r "$SUCCESS_FILE" +%s)
NOW=$(date +%s)
DIFF_MINUTES=$(( (NOW - LAST_MODIFIED) / 60 ))

if [ $DIFF_MINUTES -gt $THRESHOLD_MINUTES ]; then
    LAST_RUN=$(cat "$SUCCESS_FILE")
    log "ALERT: Last successful collection was ${DIFF_MINUTES}m ago (at $LAST_RUN). Threshold is ${THRESHOLD_MINUTES}m."
    send_alert_email \
        "[ALERT] AI Market Intel: Collector Stale (${DIFF_MINUTES}m ago)" \
        "The collector last succeeded at $LAST_RUN (${DIFF_MINUTES} minutes ago). Threshold is ${THRESHOLD_MINUTES} minutes. Please investigate."
    log "Alert email sent."
    exit 1
else
    log "OK: Collector last succeeded ${DIFF_MINUTES}m ago — within threshold."
    exit 0
fi
