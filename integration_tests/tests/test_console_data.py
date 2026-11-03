"""console_data.build(): the views as prototype D's ICECORE_DATA object. Offline: a fake session with canned rows.

Pure Python (lib.queries and lib.incidents import no Streamlit), so it runs on every venv:

    cd integration_tests && python -m pytest tests/test_console_data.py
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


# --- the contract (docs/ui-directions/icecore-mock-data.json): field -> accepted types -----------------------------
S, I, F, B, L = str, int, float, bool, list
NUM = (int, float)
CONTRACT = {
    "nodes": {"unique_id": S, "type": S, "name": S, "layer": S, "depends_on": L, "materialization": S,
              "test_name": S, "column": S},
    "runs": {"invocation_id": S, "command": S, "started_at": S, "duration_s": NUM, "status": S, "credits": NUM,
             "nodes_total": I, "nodes_failed": I, "target": S},
    "node_executions": {"invocation_id": S, "unique_id": S, "status": S, "started_at": S, "execution_time_s": NUM,
                        "credits": NUM},
    "test_results": {"invocation_id": S, "test_unique_id": S, "status": S, "failures": I,
                     "root_cause_unique_id": S, "root_cause_kind": S},
    "metrics": {"relation": S, "unique_id": S, "metric": S, "observed_at": S, "value": NUM, "expected": NUM,
                "lower": NUM, "upper": NUM, "anomaly": B, "engine": S, "score": NUM},
    "source_freshness": {"unique_id": S, "name": S, "freshness_lag_min": I, "max_lag_min": I},
    "schema_changes": {"relation": S, "column": S, "change": S, "type": S, "detected_at": S, "from": S, "to": S},
    "slas": {"unique_id": S, "name": S, "expected_every": S, "grace": S, "last_success_at": S},
    "incidents": {"incident_key": S, "kind": S, "node": S, "title": S, "severity": S, "first_detected_at": S,
                  "occurrences": I, "acknowledged_by": S, "root_cause": S, "channel": S, "root_cause_kind": S,
                  "node_unique_id": S, "state": S},
}
TOP = {"project", "generated_at", "capabilities", "errors", *CONTRACT}
CAPS = {"edition": S, "event_table": S, "cortex": B, "icecore_version": S, "dbt_versions": L}


def validate(data):
    assert set(data) == TOP
    for key, fields in CONTRACT.items():
        assert isinstance(data[key], list), key
        for rec in data[key]:
            assert set(rec) == set(fields), (key, set(rec) ^ set(fields))
            for f, t in fields.items():
                assert rec[f] is None or (isinstance(rec[f], t) and not (t is I and isinstance(rec[f], bool))), \
                    (key, f, rec[f])
    assert set(data["capabilities"]) == set(CAPS)
    for f, t in CAPS.items():
        v = data["capabilities"][f]
        assert v is None or isinstance(v, t), f
    assert data["project"] is None or isinstance(data["project"], str)
    assert data["generated_at"] is None or isinstance(data["generated_at"], str)
    for e in data["errors"]:
        assert set(e) == {"view", "error"} and isinstance(e["error"], str)
    json.dumps(data)  # serialisable: no datetime, no Decimal left


# --- a fake Snowpark session: rows per view, picked from the view named in the SQL ---------------------------------

class FakeRow(dict):
    def as_dict(self):
        return dict(self)


class FakeSession:
    def __init__(self, rows, fail=()):
        self.rows, self.fail, self.queries = rows, set(fail), []

    def sql(self, sql, params=None):
        self.queries.append((sql, params))
        self._sql, self._params = sql, params
        return self

    def collect(self):
        view = re.search(r"from \S+\.(\w+)(?: where| order)", self._sql).group(1)
        if view in self.fail:
            raise RuntimeError(f"SQL compilation error: view {view} does not exist")
        rows = list(self.rows.get(view, []))
        if view == "NODE_EXECUTIONS":  # the real order, so that a limit drops what the order puts last
            rows.sort(key=lambda r: r["started_at"], reverse="started_at desc" in self._sql)
        limit = self._params[-1] if self._params else None
        return [FakeRow({k.upper(): v for k, v in r.items()}) for r in rows[:limit]]


def layer_for(rows, fail=()):
    from lib import queries
    return queries.QueryLayer(FakeSession(rows, fail), "DB.OBS", cache={})


T0 = datetime(2026, 11, 3, 8, 0, 0)


def small_rows():
    p = "model.shop."
    return {
        "INVOCATIONS": [{"invocation_id": "inv2", "runtime": "core", "command": "build", "target": "prod",
                         "project": "shop", "dbt_version": "1.8.10", "started_at": T0, "ended_at": T0 + timedelta(seconds=80),
                         "duration_s": 80.5, "status": "error", "nodes_total": 3, "nodes_failed": 1, "credits": 0.25},
                        {"invocation_id": "inv1", "command": "build", "target": "prod", "project": "shop",
                         "dbt_version": "1.9.4", "started_at": T0 - timedelta(days=1), "status": "success",
                         "duration_s": 70, "nodes_total": 3, "nodes_failed": 0, "credits": None}],
        "NODE_EXECUTIONS": [
            {"invocation_id": "inv1", "unique_id": p + "orders", "type": "model", "name": "orders",
             "materialization": "table", "relation": "DB.S.ORDERS", "status": "success",
             "started_at": T0 - timedelta(days=1), "execution_time_s": 3, "credits": 0.1, "layer": "gold"},
            {"invocation_id": "inv2", "unique_id": p + "orders", "type": "model", "name": "orders",
             "materialization": "table", "relation": "DB.S.ORDERS", "status": "error", "started_at": T0,
             "execution_time_s": 4.5, "credits": None, "layer": "gold"}],
        "TEST_RESULTS": [
            {"invocation_id": "inv2", "project": "shop", "test_unique_id": "test.shop.not_null_orders_id.abc",
             "test_name": "not_null", "column": "id", "status": "fail", "failures": 12, "executed_at": T0,
             "root_cause_unique_id": p + "stg", "root_cause_kind": "schema_change"},
            {"invocation_id": "inv1", "project": "shop", "test_unique_id": "test.shop.not_null_orders_id.abc",
             "test_name": "not_null", "column": "id", "status": "pass", "failures": 0,
             "executed_at": T0 - timedelta(days=1)}],
        "METRICS": [
            {"relation": "orders", "node_unique_id": p + "orders", "metric": "row_count", "observed_at": T0,
             "value": 10, "anomaly_engine": "native", "anomaly_expected": 100, "anomaly_lower": 90,
             "anomaly_upper": 110, "anomaly_score": 8.1, "anomaly_is_anomaly": True},
            {"relation": "raw.cust", "node_unique_id": "source.shop.raw.cust", "metric": "freshness_lag_seconds",
             "observed_at": T0 - timedelta(hours=1), "value": 1800, "threshold": 7200},
            {"relation": "raw.cust", "node_unique_id": "source.shop.raw.cust", "metric": "freshness_lag_seconds",
             "observed_at": T0, "value": 3600, "threshold": 7200}],
        "SCHEMA_CHANGES": [
            {"relation": "orders", "change_kind": "type_changed", "column": "D", "from_type": "NUMBER", "to_type": "FLOAT",
             "detected_at": T0, "node_unique_id": p + "orders"},
            {"relation": "orders", "change_kind": "added", "column": "N", "to_type": "VARCHAR", "detected_at": T0}],
        "LINEAGE_LATEST": [
            {"project": "shop", "parent_unique_id": p + "stg", "child_unique_id": p + "orders", "parent_layer": "silver",
             "child_layer": "gold"},
            {"project": "shop", "parent_unique_id": p + "orders", "child_unique_id": "test.shop.not_null_orders_id.abc"}],
        "SLA_DECLARED": [{"unique_id": p + "orders", "expected_every_minutes": 90, "grace_minutes": 10,
                          "channel": "slack #x", "severity": "warn"}],
        "ALERTS_FEED": [
            {"incident_key": "k1", "kind": "test_failure", "node_key": "test.shop.not_null_orders_id.abc",
             "severity": "error", "title": "<b>t</b>", "first_detected_at": T0, "last_detected_at": T0,
             "occurrences": 2},
            {"incident_key": "k2", "kind": "anomaly", "node_key": "orders|row_count||", "severity": "warn",
             "title": "a", "first_detected_at": T0, "last_detected_at": T0, "occurrences": 1,
             "acknowledged_at": T0, "acknowledged_by": "jane"},
            {"incident_key": "k3", "kind": "silence", "node_key": p + "orders", "severity": "error", "title": "s",
             "first_detected_at": T0 - timedelta(days=1), "last_detected_at": T0 - timedelta(days=1), "occurrences": 1}],
        "CAPABILITIES": [{"edition": "Enterprise", "event_table": "DB.T.EVENTS", "cortex_available": True,
                          "icecore_version": "0.9.0"}],
    }


def test_output_validates_against_the_contract():
    data = __import__("lib.console_data", fromlist=["build"]).build(layer=layer_for(small_rows()))
    validate(data)
    assert data["errors"] == []
    assert data["project"] == "shop"
    assert data["generated_at"] == "2026-11-03T08:01:20"  # newest record time (the run's end), not the app clock


def test_mapping_of_each_key():
    from lib import console_data
    d = console_data.build(layer=layer_for(small_rows()))
    nodes = {n["unique_id"]: n for n in d["nodes"]}
    t = nodes["test.shop.not_null_orders_id.abc"]
    assert (t["type"], t["test_name"], t["column"], t["depends_on"]) == ("test", "not_null", "id", ["model.shop.orders"])
    assert nodes["model.shop.orders"]["depends_on"] == ["model.shop.stg"]
    assert nodes["model.shop.orders"]["materialization"] == "table" and nodes["model.shop.orders"]["layer"] == "gold"
    assert nodes["model.shop.stg"]["layer"] == "silver" and nodes["model.shop.stg"]["type"] == "model"
    assert nodes["model.shop.stg"]["materialization"] is None
    assert [r["invocation_id"] for r in d["runs"]] == ["inv1", "inv2"]
    assert d["runs"][1]["status"] == "error" and d["runs"][0]["credits"] is None
    fail = [r for r in d["test_results"] if r["status"] == "fail"][0]
    assert (fail["failures"], fail["root_cause_unique_id"], fail["root_cause_kind"]) == (12, "model.shop.stg", "schema_change")
    m = [x for x in d["metrics"] if x["metric"] == "row_count"][0]
    assert (m["expected"], m["lower"], m["upper"], m["anomaly"], m["engine"], m["score"]) == (100, 90, 110, True, "native", 8.1)
    assert d["source_freshness"] == [{"unique_id": "source.shop.raw.cust", "name": "raw.cust",
                                      "freshness_lag_min": 60, "max_lag_min": 120}]
    ch = {c["change"]: c for c in d["schema_changes"]}
    assert (ch["type_changed"]["from"], ch["type_changed"]["to"], ch["type_changed"]["type"]) == ("NUMBER", "FLOAT", None)
    assert (ch["added"]["type"], ch["added"]["from"]) == ("VARCHAR", None)
    assert d["slas"] == [{"unique_id": "model.shop.orders", "name": "orders", "expected_every": "90m", "grace": "10m",
                          "last_success_at": "2026-11-02T08:00:00"}]
    assert d["capabilities"] == {"edition": "Enterprise", "event_table": "DB.T.EVENTS", "cortex": True,
                                 "icecore_version": "0.9.0", "dbt_versions": ["1.8.10", "1.9.4"]}


def test_incidents_state_node_and_root_cause():
    from lib import console_data
    inc = {i["incident_key"]: i for i in console_data.build(layer=layer_for(small_rows()))["incidents"]}
    assert inc["k1"]["state"] == "open" and inc["k1"]["node_unique_id"] == inc["k1"]["node"]
    assert (inc["k1"]["root_cause"], inc["k1"]["root_cause_kind"]) == ("model.shop.stg", "schema_change")
    assert inc["k2"]["state"] == "acknowledged" and inc["k2"]["acknowledged_by"] == "jane"
    assert inc["k2"]["node_unique_id"] == "model.shop.orders"
    assert inc["k3"]["state"] == "open" and inc["k3"]["channel"] == "slack #x"  # never resolved: no success after it


def test_root_cause_from_alerts_feed_first_then_test_results():
    from lib import console_data
    rows = small_rows()
    rows["ALERTS_FEED"][0].update(root_cause_unique_id="model.shop.feed_cause", root_cause_kind="late_source")
    inc = {i["incident_key"]: i for i in console_data.build(layer=layer_for(rows))["incidents"]}
    assert (inc["k1"]["root_cause"], inc["k1"]["root_cause_kind"]) == ("model.shop.feed_cause", "late_source")
    rows["ALERTS_FEED"][0].update(root_cause_unique_id=None, root_cause_kind=None)  # null: TEST_RESULTS fills in
    inc = {i["incident_key"]: i for i in console_data.build(layer=layer_for(rows))["incidents"]}
    assert (inc["k1"]["root_cause"], inc["k1"]["root_cause_kind"]) == ("model.shop.stg", "schema_change")


def test_resolved_incident():
    from lib import console_data
    rows = small_rows()
    rows["ALERTS_FEED"][0]["first_detected_at"] = rows["ALERTS_FEED"][0]["last_detected_at"] = T0 - timedelta(days=2)
    rows["TEST_RESULTS"][1]["executed_at"] = T0 - timedelta(hours=1)  # a pass after the failure was last seen
    inc = {i["incident_key"]: i for i in console_data.build(layer=layer_for(rows))["incidents"]}
    assert inc["k1"]["state"] == "resolved"


@pytest.mark.parametrize("view", ["INVOCATIONS", "NODE_EXECUTIONS", "TEST_RESULTS", "METRICS", "SCHEMA_CHANGES",
                                  "LINEAGE_LATEST", "SLA_DECLARED", "ALERTS_FEED", "CAPABILITIES"])
def test_a_failing_read_gives_errors_not_an_exception(view):
    from lib import console_data
    d = console_data.build(layer=layer_for(small_rows(), fail=[view]))
    validate(d)
    assert [e["view"] for e in d["errors"]] == [view]
    assert "does not exist" in d["errors"][0]["error"]


def test_everything_failing_and_empty():
    from lib import console_data
    d = console_data.build(layer=layer_for({}))
    validate(d)
    assert d["errors"] == [] and d["nodes"] == [] and d["generated_at"] is None and d["project"] is None
    assert d["capabilities"] == {k: None for k in CAPS}
    d = console_data.build(layer=layer_for({}, fail=["INVOCATIONS", "METRICS", "ALERTS_FEED"]))
    validate(d)
    assert len(d["errors"]) == 3


def test_values_are_data_not_html():
    from lib import console_data
    d = console_data.build(layer=layer_for(small_rows()))
    assert {i["title"] for i in d["incidents"]} >= {"<b>t</b>"}  # untouched: the UI escapes, nothing here wraps markup


def test_reads_are_bound_and_windowed():
    from lib import console_data, queries
    layer = layer_for(small_rows())
    console_data.build(days=7, layer=layer, now=T0)
    for sql, params in layer.session.queries:
        assert "?" in sql and "limit ?" in sql
        assert params[-1] <= queries.MAX_LIMIT
        assert "2026" not in sql  # values are bound, never in the text
    windowed = [p for sql, p in layer.session.queries if "NODE_EXECUTIONS" in sql][0]
    assert windowed[0] == (T0 - timedelta(days=7)).isoformat(sep=" ")


def test_scale_2000_nodes_50000_rows():
    from lib import console_data
    p = "model.big."
    nodes = [f"{p}n{i}" for i in range(2000)]
    rows = {
        "LINEAGE_LATEST": [{"project": "big", "parent_unique_id": nodes[i - 1], "child_unique_id": nodes[i]}
                           for i in range(1, 2000)],
        "NODE_EXECUTIONS": [{"invocation_id": f"i{k % 25}", "unique_id": nodes[k % 2000], "type": "model",
                             "name": f"n{k % 2000}", "status": "success", "started_at": T0 - timedelta(seconds=k),
                             "execution_time_s": 1.5, "credits": 0.01} for k in range(16_000)],
        "TEST_RESULTS": [{"invocation_id": f"i{k % 25}", "test_unique_id": f"test.big.t{k % 2000}", "status": "pass",
                          "failures": 0, "executed_at": T0 - timedelta(seconds=k)} for k in range(16_000)],
        "METRICS": [{"relation": f"n{k % 2000}", "node_unique_id": nodes[k % 2000], "metric": "row_count",
                     "observed_at": T0 - timedelta(seconds=k), "value": k} for k in range(16_000)],
        "ALERTS_FEED": [{"incident_key": f"k{i}", "kind": ("test_failure", "anomaly", "silence")[i % 3],
                         "node_key": (f"test.big.t{i}", f"n{i}|row_count||", nodes[i])[i % 3], "severity": "warn",
                         "title": "x", "first_detected_at": T0, "last_detected_at": T0, "occurrences": 1}
                        for i in range(300)],
    }
    layer = layer_for(rows)
    start = time.perf_counter()
    d = console_data.build(layer=layer)
    out = json.dumps(d, separators=(",", ":"))
    elapsed = time.perf_counter() - start
    validate(d)
    assert len(d["nodes"]) >= 2000
    assert len(out) < 10 * 1024 * 1024, len(out)
    assert elapsed < 2.0, elapsed


def test_node_executions_cap_drops_the_oldest_and_says_so(monkeypatch):
    from lib import console_data, queries
    monkeypatch.setattr(queries, "MAX_LIMIT", 5)
    rows = small_rows()
    rows["NODE_EXECUTIONS"] = [{"invocation_id": f"i{k}", "unique_id": "model.shop.orders", "type": "model",
                                "status": "success", "started_at": T0 - timedelta(hours=k)} for k in range(8)]
    d = console_data.build(layer=layer_for(rows))
    assert [r["invocation_id"] for r in d["node_executions"]] == ["i4", "i3", "i2", "i1", "i0"]  # newest 5, ascending
    errs = [e for e in d["errors"] if e["view"] == "NODE_EXECUTIONS"]
    assert errs and "truncated at 5 rows" in errs[0]["error"] and "oldest" in errs[0]["error"]
    validate(d)


def test_relation_metrics_resolve_to_their_own_node():
    from lib import console_data
    rows = small_rows()
    q = lambda t: f'"DB"."S"."{t}"'
    rows["NODE_EXECUTIONS"] += [
        {"invocation_id": "inv2", "unique_id": "model.shop.cust", "type": "model", "name": "cust",
         "relation": q("CUST"), "status": "success", "started_at": T0}]
    rows["NODE_EXECUTIONS"][0]["relation"] = rows["NODE_EXECUTIONS"][1]["relation"] = q("ORDERS")
    rows["METRICS"] = [
        {"relation": q("ORDERS"), "node_unique_id": None, "metric": "row_count", "observed_at": T0, "value": 1},
        {"relation": q("CUST"), "node_unique_id": None, "metric": "row_count", "observed_at": T0, "value": 2},
        {"relation": q("GHOST"), "node_unique_id": None, "metric": "row_count", "observed_at": T0, "value": 3}]
    got = {m["relation"]: m["unique_id"] for m in console_data.build(layer=layer_for(rows))["metrics"]}
    assert got == {q("ORDERS"): "model.shop.orders", q("CUST"): "model.shop.cust", q("GHOST"): None}


def test_source_freshness_is_keyed_on_the_node_the_ui_looks_up():
    from lib import console_data
    rows = small_rows()
    q = '"DB"."S"."ORDERS"'
    rows["NODE_EXECUTIONS"][0]["relation"] = rows["NODE_EXECUTIONS"][1]["relation"] = q
    t = "test.shop.not_null_orders_id.abc"  # the freshness test's own metric is keyed on the test: it maps to the node
    rows["METRICS"] = [
        {"relation": q, "node_unique_id": None, "metric": "freshness_lag_seconds", "observed_at": T0 - timedelta(hours=2),
         "value": 600},
        {"relation": q, "node_unique_id": t, "metric": "freshness_lag_seconds", "observed_at": T0, "value": 3600,
         "threshold": 7200},
        {"relation": "unknown", "node_unique_id": None, "metric": "freshness_lag_seconds", "observed_at": T0,
         "value": 60}]
    fr = console_data.build(layer=layer_for(rows))["source_freshness"]
    assert fr == [{"unique_id": "model.shop.orders", "name": "orders", "freshness_lag_min": 60, "max_lag_min": 120}]


@pytest.mark.parametrize("minutes,expected", [(90, "90m"), (120, "2h"), (1440, "1d"), (10, "10m"), (0.5, "30s"),
                                              (None, None)])
def test_sla_durations_are_what_the_prototype_parses(minutes, expected):
    from lib import console_data
    assert console_data._minutes(minutes) == expected
    rows = small_rows()
    rows["SLA_DECLARED"][0].update(expected_every_minutes=minutes, grace_minutes=minutes)
    sla = console_data.build(layer=layer_for(rows))["slas"][0]
    assert sla["expected_every"] == expected == sla["grace"]
    if expected:
        assert re.fullmatch(r"\d+(?:\.\d+)?[smhd]", expected)  # console.html durStr
