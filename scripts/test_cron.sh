#!/bin/bash

PYTHON=/opt/miniconda3/envs/market_env/bin/python
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Load DB credentials from .env
set -a
source "$PROJECT_DIR/config/.env"
set +a

PSQL="psql -U $DB_USER -d $DB_NAME -t -A"

echo "=== AI Market Intel — Cron Test ==="
echo ""

# Count rows before
BEFORE=$($PSQL -c "SELECT COUNT(*) FROM stock_prices;")
echo "stock_prices rows BEFORE: $BEFORE"

# Run collector
echo ""
echo "Running collector..."
cd "$PROJECT_DIR"
$PYTHON data/collector.py
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "FAIL: collector exited with code $EXIT_CODE"
    exit 1
fi

# Count rows after
AFTER=$($PSQL -c "SELECT COUNT(*) FROM stock_prices;")
echo ""
echo "stock_prices rows AFTER:  $AFTER"

NEW_ROWS=$(( AFTER - BEFORE ))
if [ $NEW_ROWS -gt 0 ]; then
    echo ""
    echo "PASS: $NEW_ROWS new row(s) inserted."
    echo ""
    echo "Most recent entries:"
    $PSQL -c "SELECT symbol, price, fetched_at FROM stock_prices ORDER BY fetched_at DESC LIMIT 8;"
else
    echo ""
    echo "FAIL: No new rows were inserted. Check logs for errors."
    exit 1
fi
