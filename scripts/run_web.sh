#!/usr/bin/env bash
# Launch the AI Market Intelligence web dashboard.
set -euo pipefail
cd "$(dirname "$0")/.."
exec conda run -n market_env python -m web.app
