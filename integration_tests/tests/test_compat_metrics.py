"""UC8 compat views `data_monitoring_metrics` and `schema_columns_snapshot` (macros/compat/elementary_metrics.sql).

Offline, the contract: each view's columns, their order and their types equal Elementary's own definition, parsed from
the package pinned in macros/compat/ELEMENTARY_VERSION (empty_data_monitoring_metrics, empty_schema_columns_snapshot in
its empty_table.sql). Neither relation is one `edr report` reads, so spike S4 has no types for them: the package is the
contract. The views select only from the core views METRICS and SCHEMA_CHANGES. Sandbox: the views are created, the
described types equal Elementary's, row counts match the core views (schema_columns_snapshot leaves out `removed`
changes, which leave no column state), and the values are as documented.
"""
import json
import time
import uuid

import pytest

from helpers.elementary_package import SNOWFLAKE_TYPE, elementary_package, table_columns  # noqa: F401
from helpers.macros import run_macro
from helpers.run_hooks import DATABASE, obs_schema

VIEWS = {"metrics": "data_monitoring_metrics", "snapshot": "schema_columns_snapshot"}


def pairs(columns):
    return [(c["name"], c["type"]) for c in columns]


# --- offline ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["metrics", "snapshot"])
def test_columns_equal_elementarys_definition_in_name_order_and_type(elementary_package, key):
    mine = run_macro("it_compat_metrics", {})[key]["columns"]
    expected = table_columns(VIEWS[key])
    assert pairs(mine) == expected
    assert len(expected) == {"metrics": 15, "snapshot": 8}[key]


@pytest.mark.parametrize("key,core", [("metrics", "METRICS"), ("snapshot", "SCHEMA_CHANGES")])
def test_view_selects_only_from_its_core_view_and_casts_every_column(key, core):
    out = run_macro("it_compat_metrics", {})[key]
    assert f'from "DB"."OBS"."{core}" as t' in out["sql"]
    assert "base_events" not in out["sql"].lower()
    assert out["sql"].count("cast(") == len(out["columns"])
    for column in out["columns"]:
        assert f" as {column['name']}" in out["sql"]


def test_unfillable_columns_are_typed_nulls():
    out = run_macro("it_compat_metrics", {})
    assert sorted(c["name"] for c in out["metrics"]["columns"] if c["expr"] is None) == [
        "bucket_duration_hours", "metric_properties", "source_value"]
    assert [c["name"] for c in out["snapshot"]["columns"] if c["expr"] is None] == []


def test_snapshot_leaves_out_removed_columns():
    assert "t.change_kind in ('added', 'type_changed')" in run_macro("it_compat_metrics", {})["snapshot"]["sql"]


def test_compat_list_holds_all_three_elementary_views():
    names = [v["name"] for v in run_macro("it_compat_views_render", {})]
    assert names[:3] == ["ELEMENTARY_TEST_RESULTS", "DATA_MONITORING_METRICS", "SCHEMA_COLUMNS_SNAPSHOT"]  # run and node views follow


# --- sandbox --------------------------------------------------------------------------------------

def described(sf, schema, view):
    return sf.cursor().execute(
        f"select column_name, data_type from {DATABASE}.information_schema.columns "
        f"where table_schema = '{schema}' and table_name = '{view.upper()}' order by ordinal_position").fetchall()


def rows_of(sf, sql, params=()):
    cur = sf.cursor().execute(sql, params)
    names = [d[0].lower() for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


@pytest.mark.sandbox
def test_views_have_elementarys_types_the_core_row_counts_and_documented_values(sf, elementary_package):
    with obs_schema(sf) as obs:
        compat = f"{obs}_ELEMENTARY"
        obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": obs}})]
        run_id = uuid.uuid4().hex[:8]
        relation = f'"{DATABASE}"."SPIKES"."ORDERS_{run_id.upper()}"'
        attrs = {"dbt.node.relation": relation}
        events = [
            {"kind": "metric", "attributes": {**attrs, "dbt.metric.name": "row_count", "dbt.metric.value": 120}},
            {"kind": "metric", "attributes": {**attrs, "dbt.metric.name": "null_rate", "dbt.metric.value": 0.25,
                                              "dbt.metric.column": "amount", "dbt.metric.dimension": "country=FR"}},
            {"kind": "schema_change", "attributes": {**attrs, "dbt.schema.change_kind": "added",
                                                     "dbt.schema.column": "email", "dbt.schema.to_type": "TEXT"}},
            {"kind": "schema_change", "attributes": {**attrs, "dbt.schema.change_kind": "type_changed",
                                                     "dbt.schema.column": "amount", "dbt.schema.from_type": "NUMBER",
                                                     "dbt.schema.to_type": "FLOAT"}},
            {"kind": "schema_change", "attributes": {**attrs, "dbt.schema.change_kind": "removed",
                                                     "dbt.schema.column": "legacy", "dbt.schema.from_type": "TEXT"}},
        ]
        try:
            run_macro("it_compat_emit_events", {"events": events}, obs_vars)
            run_macro("it_create_views", {"schema": obs}, obs_vars)
            deadline = time.monotonic() + 300
            while True:
                core_metrics = sf.cursor().execute(
                    f'select count(*) from "{DATABASE}"."{obs}"."METRICS" where relation = %s', (relation,)).fetchone()[0]
                core_changes = sf.cursor().execute(
                    f'select count(*) from "{DATABASE}"."{obs}"."SCHEMA_CHANGES" where relation = %s', (relation,)).fetchone()[0]
                if (core_metrics, core_changes) == (2, 3) or time.monotonic() > deadline:
                    break
                time.sleep(5)
            assert (core_metrics, core_changes) == (2, 3)

            created = run_macro("it_create_compat_views", {"schema": obs}, obs_vars)
            assert sorted(created) == sorted(f'"{DATABASE}"."{compat}"."{n}"' for n in [
                "ELEMENTARY_TEST_RESULTS", "DATA_MONITORING_METRICS", "SCHEMA_COLUMNS_SNAPSHOT"])

            for view in VIEWS.values():
                assert described(sf, compat, view) == [(n.upper(), SNOWFLAKE_TYPE[t]) for n, t in table_columns(view)]

            full_name = f"{DATABASE}.SPIKES.ORDERS_{run_id.upper()}"
            metrics = {r["metric_name"]: r for r in rows_of(
                sf, f'select * from "{DATABASE}"."{compat}"."DATA_MONITORING_METRICS" where full_table_name = %s', (full_name,))}
            assert len(metrics) == core_metrics == 2
            nulls = metrics["null_rate"]
            assert nulls["metric_type"] == "null_rate" and nulls["metric_value"] == 0.25 and nulls["column_name"] == "amount"
            assert nulls["dimension"] == "country" and nulls["dimension_value"] == "FR"
            assert nulls["bucket_start"] == nulls["bucket_end"] == nulls["updated_at"] == nulls["created_at"] is not None
            assert nulls["source_value"] is None and nulls["bucket_duration_hours"] is None and nulls["id"]
            assert metrics["row_count"]["metric_value"] == 120 and metrics["row_count"]["dimension"] is None
            assert metrics["row_count"]["dimension_value"] is None

            snapshot = {r["column_name"]: r for r in rows_of(
                sf, f'select * from "{DATABASE}"."{compat}"."SCHEMA_COLUMNS_SNAPSHOT" where full_table_name = %s', (full_name,))}
            assert sorted(snapshot) == ["amount", "email"]  # `legacy` was removed: no column state
            assert snapshot["email"]["is_new"] is True and snapshot["email"]["data_type"] == "TEXT"
            assert snapshot["amount"]["is_new"] is False and snapshot["amount"]["data_type"] == "FLOAT"
            assert snapshot["email"]["full_column_name"] == f"{full_name}.EMAIL".upper() and snapshot["email"]["column_state_id"]
            assert snapshot["email"]["detected_at"] == snapshot["email"]["created_at"]

            counts = sf.cursor().execute(
                f'select (select count(*) from "{DATABASE}"."{compat}"."DATA_MONITORING_METRICS"), '
                f'(select count(*) from "{DATABASE}"."{obs}"."METRICS"), '
                f'(select count(*) from "{DATABASE}"."{compat}"."SCHEMA_COLUMNS_SNAPSHOT"), '
                f'(select count(*) from "{DATABASE}"."{obs}"."SCHEMA_CHANGES" where change_kind <> \'removed\')').fetchone()
            assert counts[0] == counts[1] and counts[2] == counts[3]
        finally:
            sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{compat}" cascade')
