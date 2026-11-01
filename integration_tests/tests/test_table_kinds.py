"""UC2 readers across every table kind the sandbox can create (story 14bb2e69).

Readers: icecore.get_row_count_metadata / get_row_count_changes / get_last_change_time (macros/monitors/metadata.sql),
icecore.get_columns_signature (schema.sql), icecore.get_change_window / compute_column_metrics (columns.sql).

Kinds built here, in SPIKES, with one run id in every name: standard, transient, dynamic (target_lag = downstream, refreshed
by hand, so it never refreshes on its own and costs no scheduled credits), view (change tracking on), materialized view,
a standard table with a quoted mixed-case name, and a temporary table. External and Iceberg tables cannot be created with
the CI role (see the last tests): each attempt is recorded in spikes/results/uc2_table_kinds.json and skipped.

One module fixture builds everything once; dynamic and materialized objects are dropped as soon as the module ends.
"""
import json
import os
import time
import uuid

import pytest
from snowflake.connector.errors import ProgrammingError

from helpers.findings import record_finding
from helpers.macros import call_macro, run_macro, run_macro_with_logs

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES")
BEFORE, ADDED = 30, 25
COLUMNS = ["ID", "NOTE", "UPDATED_AT"]


def quoted(*parts: str) -> str:
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def rel(identifier: str, quoted_identifier: bool = False) -> dict:
    return {"database": DATABASE, "schema": SCHEMA, "identifier": identifier, "quoted": quoted_identifier}


def utc_now(sf) -> str:
    return sf.cursor().execute("""select to_varchar(sysdate(), 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"')""").fetchone()[0]


def insert(sf, relation: str, n: int, start: int) -> None:
    sf.cursor().execute(
        f"insert into {relation} select {start} + seq4(), 'uc2', sysdate() from table(generator(rowcount => {n}))")


@pytest.fixture(scope="module")
def kinds(sf):
    """{kind: {name, quoted, relation, since}} plus 'since' (taken after the first rows, before the next ones)."""
    rid = uuid.uuid4().hex.upper()
    cur = sf.cursor()
    ddl = "(id number, note varchar, updated_at timestamp_ntz) change_tracking = true"
    base_name = f"UC2_KIND_BASE_{rid}"
    base = quoted(DATABASE, SCHEMA, base_name)
    made = []  # (drop statement)
    out = {}

    def make(kind, name, create_sql, drop_sql, quoted_identifier=False):
        cur.execute(create_sql)
        made.append(drop_sql)
        out[kind] = {"name": name, "quoted": quoted_identifier, "relation": quoted(DATABASE, SCHEMA, name)}

    try:
        cur.execute(f"create table {base} {ddl}")
        made.append(f"drop table if exists {base}")
        for kind, create in (("standard", "table"), ("transient", "transient table")):
            name = f"UC2_KIND_{kind.upper()}_{rid}"
            make(kind, name, f"create {create} {quoted(DATABASE, SCHEMA, name)} {ddl}",
                 f"drop table if exists {quoted(DATABASE, SCHEMA, name)}")
        name = f'Mixed Case "Kind" {rid}'
        make("quoted", name, f"create table {quoted(DATABASE, SCHEMA, name)} {ddl}",
             f"drop table if exists {quoted(DATABASE, SCHEMA, name)}", quoted_identifier=True)
        for kind in ("standard", "transient", "quoted"):
            insert(sf, out[kind]["relation"], BEFORE, 0)
        insert(sf, base, BEFORE, 0)
        proj = f"select id, note, updated_at from {base}"
        name = f"UC2_KIND_DYNAMIC_{rid}"
        make("dynamic", name,
             f"create dynamic table {quoted(DATABASE, SCHEMA, name)} target_lag = downstream "
             f"warehouse = {os.environ['SNOWFLAKE_WAREHOUSE']} as {proj}",
             f"drop dynamic table if exists {quoted(DATABASE, SCHEMA, name)}")
        name = f"UC2_KIND_VIEW_{rid}"
        make("view", name, f"create view {quoted(DATABASE, SCHEMA, name)} change_tracking = true as {proj}",
             f"drop view if exists {quoted(DATABASE, SCHEMA, name)}")
        name = f"UC2_KIND_MVIEW_{rid}"
        make("materialized_view", name, f"create materialized view {quoted(DATABASE, SCHEMA, name)} as {proj}",
             f"drop materialized view if exists {quoted(DATABASE, SCHEMA, name)}")
        time.sleep(3)
        out["since"] = utc_now(sf)
        time.sleep(1)
        for kind in ("standard", "transient", "quoted"):
            insert(sf, out[kind]["relation"], ADDED, 1000)
        insert(sf, base, ADDED, 1000)
        cur.execute(f"alter dynamic table {out['dynamic']['relation']} refresh")
        time.sleep(60)  # metadata settles (S5: correct within seconds; the materialized view maintains itself)
        yield out
    finally:
        failed = []
        for statement in reversed(made):  # every drop is attempted, whatever the others do
            try:
                cur.execute(statement)
            except Exception as exc:
                failed.append(f"{statement}: {str(exc).splitlines()[-1][:120]}")
        if failed:
            raise RuntimeError("fixture objects not dropped: " + "; ".join(failed))


KINDS = ["standard", "transient", "quoted", "dynamic", "view", "materialized_view"]


def read(kinds, kind, since=True):
    k = kinds[kind]
    args = {"relations": [rel(k["name"] if k["quoted"] else k["name"].lower(), k["quoted"])]}
    if since:
        args["since"] = kinds["since"]
    out, logs = run_macro_with_logs("it_metadata", args)
    return out[k["name"] if k["quoted"] else k["name"].lower()], logs


# --- zero-scan readers per kind ---------------------------------------------------------------

@pytest.mark.sandbox
@pytest.mark.parametrize("kind", KINDS)
def test_row_count_and_last_change_per_kind(dbt, sf, kinds, kind):
    got, logs = read(kinds, kind, since=False)
    assert got["last_change"] is not None, (kind, got, logs)
    if kind == "view":
        # a view has no ROW_COUNT in metadata: none, one clear message
        assert got["row_count"] is None
        assert sum("is a view" in m and "not supported" in m for m in logs) == 1, logs
    elif kind == "materialized_view":
        # Snowflake maintains a materialized view in the background: its metadata total was still the old one 60 s after
        # the insert (30 of 55). So the kind is a default fallback kind: no total, and the monitor falls back to counting.
        assert got["row_count"] is None, (kind, got)
        assert any("is a materialized_view table" in m and "not trusted" in m for m in logs), logs
        stale = sf.cursor().execute(
            "select row_count from information_schema.tables where table_schema = %s and table_name = %s",
            (SCHEMA, kinds[kind]["name"])).fetchone()[0]
        assert stale in (BEFORE, BEFORE + ADDED), stale
        record_finding("uc2_table_kinds", "materialized_view_row_count", {
            "metadata_row_count_60s_after_insert": stale, "true_count": BEFORE + ADDED})
    else:
        assert got["row_count"] == BEFORE + ADDED, (kind, got)


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", ["standard", "transient", "quoted", "dynamic", "view"])
def test_rows_added_over_changes_per_kind(dbt, kinds, kind):
    got, logs = read(kinds, kind)
    assert got["rows_added"] == ADDED, (kind, got, logs)


@pytest.mark.sandbox
def test_materialized_view_has_no_changes_and_says_so(dbt, kinds):
    # CHANGES is unsupported on materialized views: none, with the error carried in a warning, never a failure
    got, logs = read(kinds, "materialized_view")
    assert got["rows_added"] is None and got["row_count"] is None, got  # a default fallback kind: no metadata total
    assert any("get_row_count_changes could not read" in m for m in logs), logs


@pytest.mark.sandbox
def test_view_without_change_tracking_returns_none_then_works_once_enabled(dbt, sf, kinds):
    # kinds is only needed for `since`; the view gets a base table of its own so the shared fixtures keep their counts
    rid = uuid.uuid4().hex.upper()
    name = f"UC2_KIND_VIEWNOCT_{rid}"
    view = quoted(DATABASE, SCHEMA, name)
    base = quoted(DATABASE, SCHEMA, f"UC2_KIND_VIEWNOCT_BASE_{rid}")
    sf.cursor().execute(f"create table {base} (id number, note varchar, updated_at timestamp_ntz) change_tracking = true")
    sf.cursor().execute(f"create view {view} as select id, note, updated_at from {base}")
    try:
        got, logs = run_macro_with_logs("it_metadata", {"relations": [rel(name.lower())], "since": kinds["since"]})
        assert got[name.lower()]["rows_added"] is None
        assert any("Change tracking" in m for m in logs), logs
        sf.cursor().execute(f"alter view {view} set change_tracking = true")
        time.sleep(2)
        since = utc_now(sf)
        insert(sf, base, 5, 5000)
        got, _ = run_macro_with_logs("it_metadata", {"relations": [rel(name.lower())], "since": since})
        assert got[name.lower()]["rows_added"] == 5, got
    finally:
        sf.cursor().execute(f"drop view if exists {view}")
        sf.cursor().execute(f"drop table if exists {base}")


@pytest.mark.sandbox
def test_default_fallback_kinds_are_iceberg_and_materialized_view():
    assert call_macro("_icecore_metadata_fallback_kinds") == ["iceberg", "materialized_view"]


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", ["standard", "dynamic", "materialized_view", "quoted"])
def test_flagged_kind_skips_the_metadata_total_and_keeps_the_delta(dbt, kinds, kind):
    # `metadata_fallback_kinds` is how a kind S5 flags (Iceberg by default) is routed: no total, a recorded message
    # pointing at get_row_count_changes. Iceberg cannot be built here, so the mechanism is proved on the kinds that can,
    # by naming each as a fallback kind. The kind label the reader derives for each is part of the message.
    k = kinds[kind]
    label = {"standard": "standard", "dynamic": "dynamic", "materialized_view": "materialized_view", "quoted": "standard"}[kind]
    ident = k["name"] if k["quoted"] else k["name"].lower()
    switch = ["--vars", json.dumps({"icecore": {"metadata_fallback_kinds": [label]}})]
    got, logs = run_macro_with_logs("it_metadata", {"relations": [rel(ident, k["quoted"])], "since": kinds["since"]}, switch)
    assert got[ident]["row_count"] is None, got
    assert any(f"is a {label} table" in m and "use get_row_count_changes" in m for m in logs), logs
    if kind != "materialized_view":
        assert got[ident]["rows_added"] == ADDED, got


# --- schema signature per kind ----------------------------------------------------------------

@pytest.mark.sandbox
@pytest.mark.parametrize("kind", KINDS)
def test_columns_signature_per_kind(dbt, kinds, kind):
    k = kinds[kind]
    out = run_macro("it_schema", {"database": DATABASE, "schema": SCHEMA,
                                  "identifier": k["name"] if k["quoted"] else k["name"].lower(), "quoted": k["quoted"]})
    assert [c["name"] for c in out["signature"]] == COLUMNS, (kind, out)
    assert [c["data_type"] for c in out["signature"]] == ["NUMBER(38,0)", "VARCHAR(16777216)", "TIMESTAMP_NTZ(9)"], out


# --- column monitors per kind -----------------------------------------------------------------

@pytest.mark.sandbox
@pytest.mark.parametrize("kind", ["standard", "transient", "quoted", "dynamic", "view", "materialized_view"])
def test_column_window_and_metrics_per_kind(dbt, kinds, kind):
    k = kinds[kind]
    ident = k["name"] if k["quoted"] else k["name"].lower()
    got, logs = run_macro_with_logs("it_column_metrics", {
        "rel": rel(ident, k["quoted"]), "column_name": "note", "since": kinds["since"], "timestamp_column": "updated_at"})
    assert got["metrics"] == {"window_rows": ADDED, "null_rate": 0.0}, (kind, got, logs)
    # CHANGES where it works; the materialized view cannot, so the timestamp column takes over and says why
    assert got["window"]["mode"] == ("timestamp_column" if kind == "materialized_view" else "changes"), got
    if kind == "materialized_view":
        assert any("CHANGES is unavailable" in m and "using timestamp column updated_at" in m for m in logs), logs


# --- paths where a reader cannot read ---------------------------------------------------------

@pytest.mark.sandbox
def test_temporary_table_is_invisible_to_the_reader_session(dbt, sf):
    # dbt's run-operation uses its own session: a TEMPORARY table of another session is not in INFORMATION_SCHEMA, so the
    # readers return none with the "missing or not visible" warning instead of failing
    name = f"UC2_KIND_TEMP_{uuid.uuid4().hex.upper()}"
    sf.cursor().execute(f"create temporary table {quoted(DATABASE, SCHEMA, name)} (id number)")
    try:
        got, logs = run_macro_with_logs("it_metadata", {"relations": [rel(name.lower())]})
        assert got[name.lower()]["row_count"] is None
        assert any("not in INFORMATION_SCHEMA.TABLES" in m for m in logs), logs
    finally:
        sf.cursor().execute(f"drop table if exists {quoted(DATABASE, SCHEMA, name)}")


@pytest.mark.sandbox
@pytest.mark.parametrize("where", ["database", "schema"])
def test_relation_in_a_missing_database_or_schema_returns_none(dbt, where):
    ident = f"uc2_kind_nowhere_{uuid.uuid4().hex}"
    r = rel(ident)
    r[where] = f"UC2_NO_SUCH_{where.upper()}_{uuid.uuid4().hex[:8].upper()}"
    out, logs = run_macro_with_logs("it_metadata", {"relations": [r], "since": "2026-10-31T08:00:00.000000Z"})
    assert out[ident] == {"row_count": None, "rows_added": None, "last_change": None}, out
    assert any(m.startswith("icecore: warning:") for m in logs), logs
    sig = run_macro("it_schema", {"database": r["database"], "schema": r["schema"], "identifier": ident})
    assert sig["signature"] is None, sig


# --- kinds the CI role cannot create: record what happens -------------------------------------

@pytest.mark.sandbox
def test_external_table_cannot_be_created_and_the_readers_say_so(dbt, sf):
    rid = uuid.uuid4().hex.upper()
    stage = quoted(DATABASE, SCHEMA, f"UC2_KIND_STG_{rid}")
    table = quoted(DATABASE, SCHEMA, f"UC2_KIND_EXT_{rid}")
    cur = sf.cursor()
    cur.execute(f"create stage {stage}")
    try:
        try:
            cur.execute(f"create external table {table} with location = @{stage} file_format = (type = parquet)")
        except ProgrammingError as exc:  # an external table needs an external stage (cloud storage), which the sandbox lacks
            if exc.errno != 91080:
                raise
            error = str(exc).splitlines()[-1][:200]
        else:
            cur.execute(f"drop external table if exists {table}")
            pytest.fail("an external table was created over an internal stage: revisit the external-table coverage")
        record_finding("uc2_table_kinds", "external", {
            "created": False, "error": error,
            "readers": "a table that does not exist reads as none with a warning (test_relation_in_a_missing_database_or_schema)",
            "unmeasured": "ROW_COUNT / last change of a real external table; the reader maps table_type EXTERNAL TABLE to "
                          "'external' and returns none with the views-and-external-tables message when ROW_COUNT is null"})
        pytest.skip(f"external tables cannot be created in the sandbox: {error}")
    finally:
        cur.execute(f"drop stage if exists {stage}")


@pytest.mark.sandbox
def test_iceberg_table_cannot_be_created_without_an_external_volume(dbt, sf):
    volume = os.environ.get("ICECORE_EXTERNAL_VOLUME")
    cur = sf.cursor()
    cur.execute("show external volumes")
    volumes = [r[0] for r in cur.fetchall()]
    record_finding("uc2_table_kinds", "iceberg", {
        "ICECORE_EXTERNAL_VOLUME_set": bool(volume), "external_volumes_visible_to_role": volumes,
        "unmeasured": "S5 left Iceberg unmeasured and still is; the default fallback kind (metadata_fallback_kinds: [iceberg]) "
                      "is proved by naming other kinds as fallback kinds (test_flagged_kind_skips_the_metadata_total_and_keeps_the_delta)"})
    if not volume:
        pytest.skip("no external volume: Iceberg tables cannot be created, so the Iceberg path stays unmeasured")
    name = f"UC2_KIND_ICE_{uuid.uuid4().hex.upper()}"
    table = quoted(DATABASE, SCHEMA, name)
    cur.execute(f"create iceberg table {table} (id int, note string, updated_at timestamp_ntz) catalog = 'SNOWFLAKE' "
                f"external_volume = '{volume}' base_location = '{name}' change_tracking = true")
    try:
        insert(sf, table, 10, 0)
        time.sleep(30)
        got, logs = run_macro_with_logs("it_metadata", {"relations": [rel(name.lower())]})
        # the default fallback kind: no trusted total, the message names get_row_count_changes
        assert got[name.lower()]["row_count"] is None, got
        assert any("is a iceberg table" in m for m in logs), logs
    finally:
        cur.execute(f"drop iceberg table if exists {table}")
