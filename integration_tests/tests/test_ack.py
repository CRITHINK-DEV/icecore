"""The app's only write, streamlit/lib/ack.py (the UI control waits for the new design).

Offline: a fake session records (sql, params) and returns what ICECORE_ACK returns or raises what Snowflake raises, so
the tests check that the key is a bound parameter, the messages for success, already acknowledged, refusal and missing
privilege, and that the cache is cleared.

Sandbox: creates ICECORE_OBS_<run_id> with the emitter procedures and the views, seeds one schema change (one incident
in ALERTS_FEED), acknowledges it through lib/ack.py over a qmark-bound connection, then reads ALERTS_FEED back, and
drops the schema.
"""
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
sys.path.insert(0, str(APP_DIR))
from lib import ack, queries  # noqa: E402  (no Streamlit import at module level)

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
KEY = "0123456789abcdef0123456789abcdef"
EVIL = "x'); drop table t; --"


class FakeRow:
    def __init__(self, data):
        self.data = data

    def as_dict(self):
        return dict(self.data)


class FakeSession:
    """Answers the CALL with `status` (or raises `error`) and the ALERTS_FEED lookup with `feed_rows`."""

    def __init__(self, status="ok 1", error=None, feed_rows=()):
        self.status, self.error, self.feed_rows = status, error, list(feed_rows)
        self.calls = []
        self._rows = []

    def sql(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("call"):
            if self.error:
                raise self.error
            self._rows = [FakeRow({"ICECORE_ACK": self.status})]
        else:
            self._rows = [FakeRow(r) for r in self.feed_rows]
        return self

    def collect(self):
        return self._rows


def layer(session, schema="DB.ICECORE_OBS"):
    return queries.QueryLayer(session, schema)


def test_the_key_is_a_bound_parameter_and_the_call_names_the_procedure_in_the_views_schema():
    s = FakeSession()
    res = ack.acknowledge(KEY, layer=layer(s))
    assert res.kind == ack.OK and res.ok
    assert s.calls == [('call DB.ICECORE_OBS."ICECORE_ACK"(?)', [KEY])]
    assert KEY not in s.calls[0][0]


@pytest.mark.parametrize("bad", ["", None, "nope", EVIL, KEY.upper(), KEY + "0", 123])
def test_a_malformed_key_is_refused_before_any_call(bad):
    s = FakeSession()
    res = ack.acknowledge(bad, layer=layer(s))
    assert res.kind == ack.INVALID and not s.calls


@pytest.mark.parametrize("schema", [EVIL, "a b", "", "a.b.c", "x;y"])
def test_the_procedure_name_accepts_plain_identifiers_only(schema):
    with pytest.raises(ValueError):
        ack.procedure_name(schema)


def test_success_clears_the_query_cache():
    s = FakeSession()
    lay = layer(s)
    lay.cache["k"] = (0, [])
    assert ack.acknowledge(KEY, layer=lay).ok
    assert lay.cache == {}


def test_already_acknowledged_is_told_apart_from_an_unknown_incident():
    refusal = f"icecore-error not_an_open_incident: {KEY} is not a current unacknowledged incident of ALERTS_FEED"
    s = FakeSession(refusal, feed_rows=[{"ACKNOWLEDGED_AT": "2026-11-02 10:00:00"}])
    res = ack.acknowledge(KEY, layer=layer(s))
    assert res.kind == ack.ALREADY and "already acknowledged" in res.message
    assert s.calls[1][1] == [KEY] and "?" in s.calls[1][0] and KEY not in s.calls[1][0]

    unknown = FakeSession(refusal, feed_rows=[])
    res = ack.acknowledge(KEY, layer=layer(unknown))
    assert res.kind == ack.REFUSED and "not_an_open_incident" in res.message and not res.ok


def test_a_refusal_is_shown_even_when_the_feed_cannot_be_read():
    class Broken(FakeSession):
        def sql(self, sql, params=None):
            if not sql.startswith("call"):
                raise RuntimeError("feed unreadable")
            return super().sql(sql, params)

    res = ack.acknowledge(KEY, layer=layer(Broken("icecore-error not_an_open_incident: x")))
    assert res.kind == ack.REFUSED


@pytest.mark.parametrize("message", [
    "SQL access control error: Insufficient privileges to operate on procedure 'ICECORE_ACK'",
    "Procedure ICECORE_OBS.ICECORE_ACK does not exist or not authorized.",
    "Schema 'DB.ICECORE_OBS' does not exist or not authorized.",
    "002141 (42601): SQL compilation error:\nUnknown user-defined function DB.ICECORE_OBS.ICECORE_ACK."])
def test_a_missing_privilege_is_a_clear_message_naming_the_procedure(message):
    res = ack.acknowledge(KEY, layer=layer(FakeSession(error=RuntimeError(message))))
    assert res.kind == ack.NO_PRIVILEGE and "USAGE" in res.message and "ICECORE_ACK" in res.message


def test_a_privilege_error_code_is_enough():
    class Err(Exception):
        errno = 2003

    assert ack.acknowledge(KEY, layer=layer(FakeSession(error=Err("boom")))).kind == ack.NO_PRIVILEGE


def test_any_other_failure_is_an_error_with_its_text_and_nothing_raised():
    res = ack.acknowledge(KEY, layer=layer(FakeSession(error=RuntimeError("warehouse suspended"))))
    assert res.kind == ack.ERROR and "warehouse suspended" in res.message


def test_an_unexpected_procedure_answer_is_an_error():
    res = ack.acknowledge(KEY, layer=layer(FakeSession("icecore-error something_else: boom")))
    assert res.kind == ack.ERROR and "something_else" in res.message


def test_ack_py_only_calls_the_procedure_and_reads_the_feed():
    code = (APP_DIR / "lib" / "ack.py").read_text().split('"""', 2)[-1].lower()
    for forbidden in ["insert ", "merge ", "icecore_emit", "execute immediate"]:
        assert forbidden not in code, forbidden


# --- the control, rendered with AppTest (needs streamlit) --------------------------------------------------

# --- sandbox ------------------------------------------------------------------------------------------------

class QmarkSession:
    """The slice of Snowpark that ack.py uses, over a connector connection bound with qmark."""

    def __init__(self, conn):
        from snowflake.connector import DictCursor
        self.cur = conn.cursor(DictCursor)

    def sql(self, sql, params=None):
        self.cur.execute(sql, params or [])
        self._rows = self.cur.fetchall()
        return self

    def collect(self):
        return self._rows


@pytest.fixture(scope="module")
def seeded(dbt, sf):
    import snowflake.connector
    from helpers.events import wait_for_events
    from helpers.macros import run_macro
    from helpers.run_hooks import snowflake_since

    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    conn = None
    try:
        run_macro("it_create_emit_procedures", {}, obs_vars)
        since = snowflake_since(sf, 2)
        relation = f'"{DATABASE}"."SPIKES"."it_ack_app_{run_id}"'
        run_macro("it_emit_events", {"events": [{"kind": "schema_change", "attributes": {
            "dbt.node.relation": relation, "dbt.schema.change_kind": "added", "dbt.schema.column": "NEW_COL",
            "dbt.schema.to_type": "NUMBER(38,0)"}}]}, obs_vars)
        wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                " and record:name::string = 'icecore.schema_change'",
            {"db": DATABASE, "schema": schema}, min_rows=1, since=since, settle_s=10)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        conn = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
            private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], role=os.environ["SNOWFLAKE_ROLE"],
            warehouse=os.environ["SNOWFLAKE_WAREHOUSE"], database=DATABASE, paramstyle="qmark")
        session = QmarkSession(conn)
        yield {"layer": queries.QueryLayer(session, f"{DATABASE}.{schema}"), "session": session, "schema": schema,
               "relation": relation, "since": since}
    finally:
        if conn is not None:
            conn.close()
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


def _incident(seeded):
    lay = seeded["layer"]
    sql = f"select incident_key, acknowledged_at, acknowledged_by from {lay.views_schema}.ALERTS_FEED where node_key like ?"
    (row,) = seeded["session"].sql(sql, params=["%it_ack_app_%"]).collect()
    return {k.lower(): v for k, v in row.items()}


@pytest.mark.sandbox
def test_acknowledging_through_ack_py_records_the_user_and_the_feed_shows_it(seeded, sf):
    from helpers.events import wait_for_events
    before = _incident(seeded)
    assert before["acknowledged_at"] is None
    caller = sf.cursor().execute("select current_user()").fetchone()[0]
    res = ack.acknowledge(before["incident_key"], layer=seeded["layer"])
    assert res.kind == ack.OK, res
    rows = wait_for_events(
        sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.ack'"
            " and startswith(resource_attributes:\"snow.executable.name\"::string, 'ICECORE_ACK(')",
        {"schema": seeded["schema"]}, since=seeded["since"], settle_s=5)
    assert len(rows) == 1
    attrs = rows[0]["record_attributes"]
    assert attrs["icecore.ack.incident_key"] == before["incident_key"] and attrs["icecore.ack.by"] == caller
    after = _incident(seeded)
    assert after["acknowledged_at"] is not None and after["acknowledged_by"] == caller


@pytest.mark.sandbox
def test_acknowledging_twice_says_already_acknowledged_and_an_unknown_key_is_refused(seeded):
    key = _incident(seeded)["incident_key"]
    assert ack.acknowledge(key, layer=seeded["layer"]).kind == ack.ALREADY
    res = ack.acknowledge("0" * 32, layer=seeded["layer"])
    assert res.kind == ack.REFUSED and "not_an_open_incident" in res.message


@pytest.mark.sandbox
def test_a_schema_the_role_cannot_use_is_a_privilege_message(seeded):
    res = ack.acknowledge(KEY, layer=queries.QueryLayer(seeded["session"], f"{DATABASE}.ICECORE_NO_SUCH_{uuid.uuid4().hex[:8]}"))
    assert res.kind == ack.NO_PRIVILEGE, res
