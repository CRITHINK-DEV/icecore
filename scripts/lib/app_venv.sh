# Sourced by run_app_locally.sh and deploy_app.sh: make (or reuse) the app virtualenv with a supported Python.
# Snowpark supports Python 3.10 to 3.13; a newer python3 cannot install it, so pick a supported one.
#   PYTHON   the interpreter to use (default: the first of python3.13, 3.12, 3.11, 3.10, python3 in 3.10..3.13)
#   VENV     the virtualenv (default: .venv-app at the repo root)
app_python() {
  local candidates="${PYTHON:-python3.13 python3.12 python3.11 python3.10 python3}" py
  for py in $candidates; do
    if command -v "$py" >/dev/null 2>&1 &&
       "$py" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
      echo "$py"; return 0
    fi
  done
  echo "icecore app: needs Python 3.10 to 3.13 (Snowpark does not install on newer versions)." >&2
  echo "Install one (for example python3.13) or set PYTHON=/path/to/python3.13." >&2
  return 1
}

app_venv() {  # $1 repo root; prints the venv path once its requirements are installed
  local root="$1" venv="${VENV:-$1/.venv-app}" py
  if [ ! -x "$venv/bin/python" ] || ! "$venv/bin/python" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)'; then
    py="$(app_python)" || return 1
    rm -rf "$venv"
    "$py" -m venv "$venv" >&2
  fi
  # every run: a no-op when satisfied, and it repairs a venv whose first install failed half-way
  "$venv/bin/pip" install --quiet --disable-pip-version-check -r "$root/streamlit/requirements-local.txt" >&2 || return 1
  echo "$venv"
}
