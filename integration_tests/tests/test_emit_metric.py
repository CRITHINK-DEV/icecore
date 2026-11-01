"""UC2 METRIC emitter: icecore.emit_metric (macros/monitors/emit_metric.sql).

emit_metric(relation, metric, value, column, dimension, anomaly) writes through icecore.emit_events: one span event
`icecore.metric` with dbt.metric.* keys, the value as a number, `dbt.node.relation` as the quoted DB.SCHEMA.NAME and,
only when an anomaly result is given, the six dbt.anomaly.* keys. When that result says is_anomaly, a second span
event `icecore.anomaly` at level warn goes in the same emit_events call, so in the same CALL. Gate 1 (spikes/GATE1.md)
lets ICECORE_EMIT write span events only; LOG records come from ICECORE_EMIT_RUN at on-run-end, so the story's
"anomaly LOG" is this `icecore.anomaly` event.

Offline tests render the events through integration_tests/macros/test_support/it_emit_metric.sql. Sandbox tests
create both procedures with icecore.create_emit_procedures in a schema of their own, ICECORE_OBS_<run_id> (passed as
the obs_schema var, so that schema is the run tag), emit three metrics, then read them back through
icecore.base_events() to prove they pass the base filter, and drop the schema at the end.
"""
import json
import os
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import call_macro, run_macro, run_macro_with_logs

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
REL = {"database": "icecore_sandbox", "schema": "spikes", "identifier": "orders"}
ANOMALY = {"engine": "native", "expected": 1000.0, "lower": 900.0, "upper": 1100.0, "score": -3.2,
           "is_anomaly": True, "fallback_reason": None}
ANOMALY_KEYS = {"dbt.anomaly.engine": "native", "dbt.anomaly.expected": 1000.0, "dbt.anomaly.lower": 900.0,
                "dbt.anomaly.upper": 1100.0, "dbt.anomaly.score": -3.2, "dbt.anomaly.is_anomaly": True}


def render(metric="row_count", value=100, rel=None, **kwargs) -> dict:
    return run_macro("it_emit_metric_render", {"rel": rel or REL, "metric": metric, "value": value, **kwargs})


def anomaly_keys(attrs: dict) -> set:
    return {k for k in attrs if k.startswith("dbt.anomaly.")}


# --- offline: rendering only, no connection ------------------------------------------------------

def test_row_count_100_is_one_metric_event_with_a_numeric_value():
    out = render()
    assert out["chunk_sizes"] == [1]
    (record,) = out["records"]
    a = record["attrs"]
    assert record["name"] == "icecore.metric"
    assert a["dbt.metric.name"] == "row_count"
    assert a["dbt.metric.value"] == 100 and type(a["dbt.metric.value"]) is int
    assert a["dbt.node.relation"] == '"ICECORE_SANDBOX"."SPIKES"."ORDERS"'
    assert a["icecore.level"] == "info"
    assert a["dbt.invocation_id"]  # base_attributes() through icecore.emit_events
    assert "dbt.metric.column" not in a and "dbt.metric.dimension" not in a


def test_no_anomaly_means_no_anomaly_keys_and_no_anomaly_event():
    out = render(column="amount", dimension="country=FR")
    (record,) = out["records"]
    assert anomaly_keys(record["attrs"]) == set()
    assert record["attrs"]["dbt.metric.column"] == "amount"
    assert record["attrs"]["dbt.metric.dimension"] == "country=FR"


def test_anomaly_keys_go_on_the_metric_event_typed():
    out = render(anomaly={**ANOMALY, "is_anomaly": False})
    (record,) = out["records"]
    got = {k: v for k, v in record["attrs"].items() if k.startswith("dbt.anomaly.")}
    assert got == {**ANOMALY_KEYS, "dbt.anomaly.is_anomaly": False}
    assert type(got["dbt.anomaly.is_anomaly"]) is bool


def test_is_anomaly_adds_a_warn_anomaly_event_in_the_same_call():
    out = render(column="amount", anomaly=ANOMALY)
    assert out["chunk_sizes"] == [2]  # one emit_events call, one CALL of ICECORE_EMIT
    metric, anomaly = out["records"]
    assert metric["name"] == "icecore.metric" and anomaly["name"] == "icecore.anomaly"
    assert metric["attrs"]["icecore.level"] == "info"
    assert anomaly["attrs"]["icecore.level"] == "warn"
    for key, value in {**ANOMALY_KEYS, "dbt.metric.name": "row_count", "dbt.metric.value": 100,
                       "dbt.metric.column": "amount",
                       "dbt.node.relation": '"ICECORE_SANDBOX"."SPIKES"."ORDERS"'}.items():
        assert anomaly["attrs"][key] == value, key
    message = anomaly["attrs"]["icecore.message"]
    assert "row_count" in message and "ORDERS" in message and "900" in message and "1100" in message


def test_null_anomaly_fields_are_left_out():
    """At parse time icecore.score returns the contract with nulls: no keys, and no anomaly event."""
    nulls = {k: None for k in ANOMALY}
    (record,) = render(anomaly=nulls)["records"]
    assert anomaly_keys(record["attrs"]) == set()


def test_extra_attributes_are_written_but_never_replace_metric_keys():
    (record,) = render(attributes={"dbt.window.until": "2026-11-01T10:00:00.000000Z", "dbt.metric.name": "evil"})["records"]
    assert record["attrs"]["dbt.window.until"] == "2026-11-01T10:00:00.000000Z"
    assert record["attrs"]["dbt.metric.name"] == "row_count"


def test_quoted_relation_keeps_its_case_and_doubles_quotes():
    rel = {"database": "icecore_sandbox", "schema": "spikes", "identifier": 'My "Odd" Table', "quoted": True}
    (record,) = render(rel=rel)["records"]
    assert record["attrs"]["dbt.node.relation"] == '"ICECORE_SANDBOX"."SPIKES"."My ""Odd"" Table"'


@pytest.mark.parametrize("value,expected", [("12.5", 12.5), ("1200", 1200), (0, 0), (-0.25, -0.25), ("1e3", 1000.0)])
def test_value_is_always_a_number(value, expected):
    (record,) = render(value=value)["records"]
    got = record["attrs"]["dbt.metric.value"]
    assert got == expected and type(got) is type(expected)


@pytest.mark.parametrize("value", [None, "abc", True, ""])
def test_a_value_that_is_not_a_number_is_not_written(value):
    assert render(value=value)["events"] == []
    out, logs = run_macro_with_logs("it_emit_metric", {"rel": REL, "metric": "row_count", "value": value})
    assert out == {"events": 0, "calls": 0, "statuses": []}
    assert any(line.startswith("icecore: warning: emit_metric") for line in logs), logs


# --- sandbox --------------------------------------------------------------------------------------

def _rel(run_id: str, name: str) -> dict:
    return {"database": DATABASE, "schema": "SPIKES", "identifier": f"it_metric_{name}_{run_id}", "quoted": True}


@pytest.fixture(scope="module")
def emitted(dbt, sf):
    """Procedures in ICECORE_OBS_<run_id>, three emit_metric calls, the records read back raw and through base_events."""
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, obs_vars)
        since = cur.execute("select dateadd(minute, -2, convert_timezone('UTC', current_timestamp())::timestamp_ntz)"
                            ).fetchone()[0]
        calls = {
            "plain": {"rel": _rel(run_id, "plain"), "metric": "row_count", "value": 100},
            "scored": {"rel": _rel(run_id, "scored"), "metric": "null_rate", "value": 0.02, "column": "amount",
                       "dimension": "country=FR", "anomaly": {**ANOMALY, "is_anomaly": False}},
            "anomalous": {"rel": _rel(run_id, "anomalous"), "metric": "row_count", "value": 100, "anomaly": ANOMALY},
        }
        statuses = {case: run_macro("it_emit_metric", args, obs_vars) for case, args in calls.items()}
        raw = wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'",
            {"db": DATABASE, "schema": schema}, min_rows=4, since=since, settle_s=10)
        # the records have landed; now read them through the base filter
        view = f'"{DATABASE}"."{schema}"."BASE_EVENTS"'
        cur.execute(f"create view {view} as {call_macro('base_events', obs_schema=schema)}")
        rows = sf.cursor(DictCursor).execute(
            f"select * from {view} where event_at >= %(since)s order by record_name desc", {"since": since}).fetchall()
        yield {"run_id": run_id, "schema": schema, "statuses": statuses, "raw": raw,
               "rows": [{k.lower(): v for k, v in r.items()} for r in rows]}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


def _by_relation(rows: list, run_id: str, name: str) -> list:
    rel = f'"{DATABASE}"."SPIKES"."it_metric_{name}_{run_id}"'
    return [r for r in rows if r["node_relation"] == rel]


@pytest.mark.sandbox
def test_each_emit_metric_is_one_call(emitted):
    assert emitted["statuses"] == {
        "plain": {"events": 1, "calls": 1, "statuses": ["ok 1"]},
        "scored": {"events": 1, "calls": 1, "statuses": ["ok 1"]},
        "anomalous": {"events": 2, "calls": 1, "statuses": ["ok 2"]},
    }


@pytest.mark.sandbox
def test_row_count_100_lands_and_passes_the_base_filter(emitted):
    # the four span events of the three CALLs, and nothing lost to the base filter
    assert len(emitted["raw"]) == 4 and len(emitted["rows"]) == 4
    (row,) = _by_relation(emitted["rows"], emitted["run_id"], "plain")
    assert row["record_type"] == "SPAN_EVENT" and row["record_name"] == "icecore.metric"
    assert row["metric_name"] == "row_count" and row["metric_value"] == 100.0
    assert json.loads(row["attributes"])["dbt.metric.value"] == 100  # a JSON number, not "100"
    assert row["invocation_id"]


@pytest.mark.sandbox
def test_no_anomaly_means_no_anomaly_keys_in_the_event_table(emitted):
    (row,) = _by_relation(emitted["rows"], emitted["run_id"], "plain")
    assert anomaly_keys(json.loads(row["attributes"])) == set()
    assert row["anomaly_is_anomaly"] is None and row["anomaly_engine"] is None


@pytest.mark.sandbox
def test_scored_metric_carries_typed_anomaly_columns_and_no_anomaly_event(emitted):
    (row,) = _by_relation(emitted["rows"], emitted["run_id"], "scored")
    assert row["record_name"] == "icecore.metric"
    assert (row["metric_name"], row["metric_column"], row["metric_dimension"]) == ("null_rate", "amount", "country=FR")
    assert row["metric_value"] == 0.02
    assert (row["anomaly_engine"], row["anomaly_expected"], row["anomaly_lower"], row["anomaly_upper"],
            row["anomaly_score"], row["anomaly_is_anomaly"]) == ("native", 1000.0, 900.0, 1100.0, -3.2, False)


@pytest.mark.sandbox
def test_is_anomaly_lands_an_extra_warn_anomaly_record_from_the_same_call(emitted):
    rows = _by_relation(emitted["rows"], emitted["run_id"], "anomalous")
    assert sorted(r["record_name"] for r in rows) == ["icecore.anomaly", "icecore.metric"]
    metric, anomaly = sorted(rows, key=lambda r: r["record_name"], reverse=True)
    assert metric["emit_query_id"] == anomaly["emit_query_id"]  # one CALL
    assert json.loads(anomaly["attributes"])["icecore.level"] == "warn"
    assert json.loads(metric["attributes"])["icecore.level"] == "info"
    for row in (metric, anomaly):
        assert row["anomaly_is_anomaly"] is True and row["anomaly_lower"] == 900.0 and row["metric_value"] == 100.0
