#!/usr/bin/env bash
# Deploy the icecore app to Streamlit in Snowflake (see docs/app.md). Settings: see scripts/deploy_app.py.
#   ICECORE_APP_DATABASE, ICECORE_APP_SCHEMA (default ICECORE_OBS), ICECORE_APP_WAREHOUSE, SNOWFLAKE_* login variables.
#   Pass --dry-run to print the statements only.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${VENV:-$root/.venv-app}"
if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv "$venv"
  "$venv/bin/pip" install --quiet -r "$root/streamlit/requirements-local.txt"
fi
"$venv/bin/pip" install --quiet pyyaml cryptography
exec "$venv/bin/python" "$root/scripts/deploy_app.py" "$@"
