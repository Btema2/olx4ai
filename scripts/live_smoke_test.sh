#!/usr/bin/env bash
# Opt-in live smoke test -- hits the real olx.pl site. Not run in CI.
set -euo pipefail

echo "== agent-help =="
olx4ai agent-help

echo "== stats =="
olx4ai stats "test query" --max 10

echo "== search =="
olx4ai search "test query" --max 5

echo "== url =="
olx4ai url "https://www.olx.pl/oferty/q-test/" --max 5

echo "== offer (HTML fallback via a listing URL) =="
FIRST_URL=$(olx4ai search "test query" --max 1 --urls --json --fields url \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['url'])")
olx4ai offer "$FIRST_URL"

echo "All live smoke checks completed."
