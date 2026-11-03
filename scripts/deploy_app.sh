#!/usr/bin/env bash
# Deploy the icecore app to Streamlit in Snowflake (see docs/app.md). Settings: see scripts/deploy_app.py.
#   ICECORE_APP_DATABASE, ICECORE_APP_SCHEMA (default ICECORE_OBS), ICECORE_APP_WAREHOUSE, SNOWFLAKE_* login variables.
#   Pass --dry-run to print the statements only.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/scripts/lib/app_venv.sh"
venv="$(app_venv "$root")"
"$venv/bin/pip" install --quiet --disable-pip-version-check pyyaml cryptography
exec "$venv/bin/python" "$root/scripts/deploy_app.py" "$@"
