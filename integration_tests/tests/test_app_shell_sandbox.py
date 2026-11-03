"""The app shell inside Snowflake: environment.yml's packages resolve from the Snowflake
Anaconda channel and every page renders there with AppTest.

Zips streamlit/ (the files a deployment uploads), puts the zip in the CI user's stage under
the run_id, runs AppTest in an anonymous Python procedure with the packages pinned in
environment.yml, and removes the zip. Creates no object.
"""
import json
import zipfile
from pathlib import Path

import pytest
import yaml

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
PAGES = ["pages/overview.py", "pages/incidents.py", "pages/tests_and_anomalies.py", "pages/runs.py",
         "pages/schedules.py", "pages/schema_changes.py", "pages/cost.py", "pages/lineage.py", "pages/node.py",
         "pages/settings.py"]

PROC = """
WITH icecore_apptest AS PROCEDURE()
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ({packages})
  IMPORTS = ('{zip_path}')
  HANDLER = 'run'
AS $$
import os, sys, zipfile

def run(session):
    root = "/tmp/icecore_app"
    with zipfile.ZipFile(os.path.join(sys._xoptions["snowflake_import_directory"], "icecore_app.zip")) as z:
        z.extractall(root)
    sys.path.insert(0, root)
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    seen = []
    real = st.navigation
    def spy(pages, *a, **k):
        seen.extend(p for g in pages.values() for p in g) if isinstance(pages, dict) else seen.extend(pages)
        return real(pages, *a, **k)
    st.navigation = spy
    try:
        at = AppTest.from_file(os.path.join(root, "streamlit_app.py"), default_timeout=60)
        at.run()
        out = {{"streamlit": st.__version__, "nav_titles": [p.title for p in seen],
               "default_schema": at.session_state["icecore_views_schema"], "pages": {{}}}}
        for path in {pages}:
            at.switch_page(path).run()
            out["pages"][path] = {{"exception": [e.value for e in at.exception],
                                  "title": at.title[0].value if at.title else None,
                                  "empty_state": bool(at.info)}}
        return out
    finally:
        st.navigation = real
$$
CALL icecore_apptest()
"""


def _packages() -> list[str]:
    deps = yaml.safe_load(open(APP_DIR / "environment.yml"))["dependencies"]
    return [d.replace("=", "==", 1) if "=" in d else d for d in deps]


@pytest.mark.sandbox
def test_app_renders_in_snowflake_with_the_channel_packages(sf, run_id, tmp_path):
    zip_file = tmp_path / "icecore_app.zip"
    with zipfile.ZipFile(zip_file, "w") as z:
        for f in APP_DIR.rglob("*"):
            rel = f.relative_to(APP_DIR)
            if f.is_file() and f.suffix in {".py", ".yml", ".toml"} and "__pycache__" not in rel.parts and f.name != "snowflake.yml":
                z.write(f, rel.as_posix())

    prefix = f"@~/icecore_apptest_{run_id}/"
    cur = sf.cursor()
    try:
        cur.execute(f"PUT 'file://{zip_file}' {prefix} AUTO_COMPRESS=FALSE OVERWRITE=TRUE")
        sql = PROC.format(packages=", ".join(f"'{p}'" for p in _packages()),
                          zip_path=f"{prefix}icecore_app.zip", pages=PAGES)
        out = json.loads(cur.execute(sql).fetchone()[0])
    finally:
        cur.execute(f"REMOVE {prefix}")

    assert out["streamlit"] == "1.50.0"
    assert out["nav_titles"] == [
        "Overview", "Incidents", "Tests & anomalies", "Runs", "Schedules & freshness", "Schema changes", "Cost",
        "Lineage", "Models & sources", "Settings & about"]
    assert out["default_schema"].endswith(".ICECORE_OBS")
    for path in PAGES:
        page = out["pages"][path]
        assert page["exception"] == [] and page["empty_state"], (path, page)
