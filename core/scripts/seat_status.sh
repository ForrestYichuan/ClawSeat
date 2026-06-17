#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Generate seat status JSON and open dashboard.
/opt/homebrew/bin/python3.12 "$SCRIPT_DIR/seat_status.py" > /tmp/clawseat_seat_status.json

# Inject the JSON into a temporary self-contained dashboard so file:// browsing
# does not need network requests, a local server, or cross-file fetch support.
export CLAWSEAT_SEAT_DASHBOARD_HTML="$SCRIPT_DIR/seat_dashboard.html"
/opt/homebrew/bin/python3.12 - <<'PY'
from pathlib import Path

import os

src = Path(os.environ["CLAWSEAT_SEAT_DASHBOARD_HTML"])
json_path = Path("/tmp/clawseat_seat_status.json")
out = Path("/tmp/clawseat_seat_dashboard.html")
html = src.read_text(encoding="utf-8")
status_json = json_path.read_text(encoding="utf-8")
out.write_text(html.replace("__SEAT_STATUS_JSON__", status_json), encoding="utf-8")
PY

open /tmp/clawseat_seat_dashboard.html
