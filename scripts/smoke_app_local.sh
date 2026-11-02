#!/usr/bin/env bash
# Smoke test of a running local app: health, and every page opened by its own URL (a bookmark or a refresh).
# Streamlit's page itself tries <page>/_stcore/health first and falls back to /_stcore/health: those 404s in the browser
# console are its base-path probing, not a fault. Use PORT to test another port.
set -euo pipefail
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
base="http://localhost:${PORT:-8501}"
[ "$(curl -fsS "$base/_stcore/health")" = "ok" ] || { echo "health check failed" >&2; exit 1; }
for page in "" tests_and_anomalies lineage cost slas settings; do
  code="$(curl -s -o "$tmp" -w '%{http_code}' "$base/$page")"
  grep -q 'id="root"' "$tmp" && [ "$code" = 200 ] || { echo "page /$page: HTTP $code, no app shell" >&2; exit 1; }
  echo "ok /$page"
done
