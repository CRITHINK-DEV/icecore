"""scripts/deploy_app.py builds the stage, PUT and CREATE STREAMLIT statements from streamlit/snowflake.yml (offline)."""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_app", REPO / "scripts" / "deploy_app.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def stmts():
    return deploy.plan("my_db", "icecore_obs", "my_wh")


def test_stage_put_and_create_statements():
    s = stmts()
    assert s[0] == ("create or replace stage MY_DB.ICECORE_OBS.ICECORE_APP_STAGE "
                    "directory=(enable=true) encryption=(type='SNOWFLAKE_SSE')")
    assert s[-1] == ("create streamlit if not exists MY_DB.ICECORE_OBS.ICECORE_APP from @MY_DB.ICECORE_OBS.ICECORE_APP_STAGE "
                     "main_file='streamlit_app.py' query_warehouse=MY_WH runtime_name='SYSTEM$WAREHOUSE_RUNTIME'")
    assert all(x.startswith("put 'file://") and x.endswith("auto_compress=false overwrite=true") for x in s[1:-1])


def test_each_file_goes_to_its_relative_folder_and_the_build_assets_ship():
    puts = "\n".join(stmts()[1:-1])
    stage = "@MY_DB.ICECORE_OBS.ICECORE_APP_STAGE"
    assert f"streamlit_app.py' {stage} auto_compress" in puts
    assert f"environment.yml' {stage} auto_compress" in puts
    assert f"config.toml' {stage}/.streamlit auto_compress" in puts
    assert f"lib/queries.py' {stage}/lib auto_compress" in puts
    assert f"build/index.html' {stage}/components/console/build auto_compress" in puts
    assert f"build/assets/console.js' {stage}/components/console/build/assets auto_compress" in puts
    assert f"layout.worker.js' {stage}/components/console/build/assets auto_compress" in puts


def test_every_listed_file_exists_and_the_toml_is_put_only_when_present(tmp_path):
    for f, _ in deploy.files():
        assert f.is_file()
    assert (REPO / "streamlit" / "icecore_app.toml").exists() == any(f.name == "icecore_app.toml" for f, _ in deploy.files())


@pytest.mark.parametrize("db, sch, wh", [("a;drop", "S", "W"), ("D", "a.b", "W"), ("D", "S", "w h"), ("", "S", "W"),
                                         ("D", "S", "W'--"), ("1D", "S", "W")])
def test_bad_identifiers_are_refused(db, sch, wh):
    with pytest.raises(ValueError):
        deploy.plan(db, sch, wh)


def test_grants_are_printed_not_run():
    assert "GRANT USAGE ON STREAMLIT MY_DB.ICECORE_OBS.ICECORE_APP TO ROLE <viewer_role>" in deploy.grants("my_db", "icecore_obs")


def test_an_existing_app_is_updated_in_place_so_its_viewers_keep_their_grants():
    live = "snow://streamlit/MY_DB.ICECORE_OBS.ICECORE_APP/versions/live/"
    assert deploy.update("my_db", "icecore_obs", live) == [
        f"copy files into '{live}' from @MY_DB.ICECORE_OBS.ICECORE_APP_STAGE"]
    assert not any("or replace streamlit" in s for s in deploy.plan("my_db", "icecore_obs", "wh"))


def test_an_existing_app_that_is_not_live_yet_is_made_live_first():
    assert deploy.update("my_db", "icecore_obs", None) == [
        "alter streamlit MY_DB.ICECORE_OBS.ICECORE_APP add live version from last"]


def test_the_app_settings_file_goes_to_the_stage_root(tmp_path):
    import shutil
    copy = tmp_path / "streamlit"
    shutil.copytree(REPO / "streamlit", copy, ignore=shutil.ignore_patterns("node_modules", "frontend"))
    (copy / "icecore_app.toml").write_text('views_schema = "DB.SCHEMA"\n')
    puts = [s for s in deploy.plan("d", "s", "w", copy) if "icecore_app.toml" in s]
    assert puts == [f"put 'file://{copy}/icecore_app.toml' @D.S.ICECORE_APP_STAGE auto_compress=false overwrite=true"]


def test_missing_settings_are_named():
    assert deploy.missing_settings({}) == ["ICECORE_APP_DATABASE", "ICECORE_APP_WAREHOUSE",
                                           "SNOWFLAKE_CONNECTION_NAME (or SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER)"]
    ok = {"ICECORE_APP_DATABASE": "d", "ICECORE_APP_WAREHOUSE": "w", "SNOWFLAKE_CONNECTION_NAME": "c"}
    assert deploy.missing_settings(ok) == []
    assert deploy.missing_settings({**ok, "SNOWFLAKE_CONNECTION_NAME": "", "SNOWFLAKE_ACCOUNT": "a"}) != []
    assert deploy.missing_settings({"ICECORE_APP_DATABASE": "d", "ICECORE_APP_WAREHOUSE": "w"}, dry_run=True) == []


def test_main_exits_2_with_a_message_and_no_traceback(monkeypatch, capsys):
    for n in ("ICECORE_APP_DATABASE", "ICECORE_APP_WAREHOUSE", "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_CONNECTION_NAME"):
        monkeypatch.delenv(n, raising=False)
    assert deploy.main([]) == 2
    err = capsys.readouterr().err
    assert "ICECORE_APP_DATABASE" in err and "SNOWFLAKE_ACCOUNT" in err and "Traceback" not in err


def test_dry_run_prints_the_make_live_step_as_conditional_and_no_done(monkeypatch, capsys):
    monkeypatch.setenv("ICECORE_APP_DATABASE", "d")
    monkeypatch.setenv("ICECORE_APP_WAREHOUSE", "w")
    assert deploy.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "first deploy only" in out and "add live version from last" in out and "Done" not in out
