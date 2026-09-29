#!/usr/bin/env bash
# One-click client demo: simulated scene, dashboard opens in the browser.
cd "$(dirname "$0")"
exec python3 run.py --source sim --profile demo --open "$@"
