#!/usr/bin/env bash
# Run the icecore Streamlit app on your laptop against your Snowflake account (see docs/app.md).
#
#   ICECORE_VIEWS_SCHEMA   where the icecore views are, DATABASE.SCHEMA (default: <your database>.ICECORE_OBS)
#   SNOWFLAKE_CONNECTION_NAME                      an entry of your connections.toml, or
#   SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE
#     + SNOWFLAKE_PRIVATE_KEY_PATH (key pair) or nothing (browser login)
#   PORT                   default 8501
#   VENV                   a virtualenv with streamlit/requirements-local.txt installed (default: .venv-app, made on first run)
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${VENV:-$root/.venv-app}"
if [ ! -x "$venv/bin/streamlit" ]; then
  python3 -m venv "$venv"
  "$venv/bin/pip" install --quiet -r "$root/streamlit/requirements-local.txt"
fi
exec "$venv/bin/streamlit" run "$root/streamlit/streamlit_app.py" \
  --server.port "${PORT:-8501}" --server.address "${HOST:-localhost}" --server.headless true \
  --browser.gatherUsageStats false
