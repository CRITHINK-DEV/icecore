"""The data object of the console (`window.ICECORE_DATA`), built from the icecore views. Pure Python, no Streamlit.

`build()` reads only through lib.queries (bound parameters, MAX_LIMIT, a `since` window) and returns exactly the
shape prototype D reads (docs/design/app-prototype/console.html). The shape is the contract: every top-level key and
every record field is always present; a value the views cannot give is None (the UI shows "not recorded yet").
Nothing here produces HTML: every value is data, passed through untouched.

Every read is guarded: a view that fails gives its key an empty list and appends {"view", "error"} to the extra
top-level `errors` list, which the UI shows. build() never raises on a failed read.

Mapping gaps (None because no view carries the value): nodes.materialization/layer of nodes that never ran,
incidents.channel except for silence incidents (SLA_DECLARED.channel), incidents.node_unique_id when the series'
relation cannot be matched to a node, schema_changes.type of a type change (it has from and to instead),
capabilities.dbt_versions without invocations in the window. Stale incidents (open for over 7 days) read as open.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from lib import incidents as _incidents
from lib import queries

FRESHNESS_METRIC = "freshness_lag_seconds"
FRESHNESS_OUT = "freshness_lag_minutes"
_NODE_TYPES = ("model", "source", "seed", "snapshot", "test", "analysis", "exposure", "metric", "operation")
_AS_OF_KEYS = (*_incidents._AS_OF_KEYS, "ended_at", "completed_at")
EMPTY_CAPABILITIES = {"edition": None, "event_table": None, "cortex": None, "icecore_version": None,
                      "dbt_versions": None}


# --- value helpers ----------------------------------------------------------------------------------------------

def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _incidents.naive(value)
    if isinstance(value, str) and value:
        try:
            return _incidents.naive(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    d = _dt(value)
    if d is not None:
        return d.isoformat(timespec="seconds")
    return value if isinstance(value, str) and value else None


def _num(value: Any, ndigits: int | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return round(out, ndigits) if ndigits is not None else out


def _int(value: Any) -> int | None:
    n = _num(value)
    return None if n is None else int(n)


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _minutes(m: Any) -> str | None:
    """A duration in minutes as console.html's durStr reads it: one number and one unit, /^\\d+(\\.\\d+)?[smhd]$/.
    The largest unit that divides it evenly: 1440 -> '1d', 120 -> '2h', 90 -> '90m', 0.5 -> '30s'."""
    n = _num(m)
    if n is None or n < 0:
        return None
    secs = int(round(n * 60))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs and secs % size == 0:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def _type_of(unique_id: str) -> str | None:
    head = unique_id.split(".", 1)[0]
    return head if head in _NODE_TYPES else None


def _name_of(unique_id: str) -> str:
    parts = unique_id.split(".")
    return ".".join(parts[2:]) if len(parts) > 2 else unique_id


# --- guarded reads ----------------------------------------------------------------------------------------------

class _Reader:
    def __init__(self, layer: Any) -> None:
        self.layer = layer
        self.errors: list[dict] = []

    def read(self, view: str, fn: Callable[..., list], **kwargs) -> list[dict]:
        try:
            rows = list(fn(layer=self.layer, **kwargs))
            if kwargs.get("limit") == queries.MAX_LIMIT and len(rows) >= queries.MAX_LIMIT:
                tail = "; oldest rows dropped" if view != "LINEAGE_LATEST" else ""
                self.errors.append({"view": view, "error": f"truncated at {queries.MAX_LIMIT} rows{tail}"})
            return rows
        except Exception as exc:  # one failing view must not take the console down
            self.errors.append({"view": view, "error": f"{type(exc).__name__}: {exc}"[:500]})
            return []


# --- builders ---------------------------------------------------------------------------------------------------

def _by_time(rows: list[dict], key: str) -> list[dict]:
    return sorted(rows, key=lambda r: _dt(r.get(key)) or datetime.min)


def _nodes(lineage, execs, tests, test_edges) -> list[dict]:
    parents: dict[str, set[str]] = {}
    layers: dict[str, str] = {}
    ids: set[str] = set()
    for e in lineage:
        p, c = e.get("parent_unique_id"), e.get("child_unique_id")
        if p:
            ids.add(p)
            if e.get("parent_layer"):
                layers.setdefault(p, e["parent_layer"])
        if c:
            ids.add(c)
            parents.setdefault(c, set())
            if e.get("child_layer"):
                layers.setdefault(c, e["child_layer"])
            if p:
                parents[c].add(p)
    latest_exec: dict[str, dict] = {}
    for r in _by_time(execs, "started_at"):
        if r.get("unique_id"):
            latest_exec[r["unique_id"]] = r
    latest_test: dict[str, dict] = {}
    for r in _by_time(tests, "executed_at"):
        if r.get("test_unique_id"):
            latest_test[r["test_unique_id"]] = r
    ids |= set(latest_exec) | set(latest_test)
    for tid, tested in test_edges.items():
        parents.setdefault(tid, set()).add(tested)
        ids.add(tested)
    out = []
    for uid in sorted(ids):
        ex, tr = latest_exec.get(uid, {}), latest_test.get(uid)
        out.append({
            "unique_id": uid,
            "type": ex.get("type") or ("test" if tr else _type_of(uid)),
            "name": ex.get("name") or _name_of(uid),
            "layer": ex.get("layer") or layers.get(uid),
            "depends_on": sorted(parents.get(uid, ())),
            "materialization": ex.get("materialization"),
            "test_name": tr.get("test_name") if tr else None,
            "column": tr.get("column") if tr else None,
        })
    return out


def _norm_relation(relation: str) -> str:
    return ".".join(part.strip('"').upper() for part in relation.split("."))


def _make_resolver(nodes, execs, schema, mets):
    """(node_of, resolve): node_of(unique_id) maps a test to the node it tests; resolve(relation) finds the node a
    relation belongs to, by the relation of its executions and metrics, else by its name. None unless exactly one
    node matches, so two models never share an id."""
    tested = {n["unique_id"]: n["depends_on"][0] for n in nodes if n["type"] == "test" and len(n["depends_on"]) == 1}

    def node_of(uid):
        return tested.get(uid, uid) if uid else None

    rel: dict[str, set] = {}
    for rows in (execs, schema, mets):
        for r in rows:
            uid = node_of(r.get("unique_id") or r.get("node_unique_id"))
            if r.get("relation") and uid and not uid.startswith("test."):
                full = _norm_relation(r["relation"])
                rel.setdefault(full, set()).add(uid)
                rel.setdefault(full.rsplit(".", 1)[-1], set()).add(uid)
    by_name: dict[str, set] = {}
    for n in nodes:
        if n["type"] != "test":
            by_name.setdefault(str(n["name"]).lower(), set()).add(n["unique_id"])

    def resolve(relation):
        if not relation:
            return None
        full = _norm_relation(relation)
        for ids in (rel.get(full), rel.get(full.rsplit(".", 1)[-1]), by_name.get(full.rsplit(".", 1)[-1].lower())):
            if ids and len(ids) == 1:
                return next(iter(ids))
        return None

    def target(r):
        """The node a metric row is about: its node (a test maps to what it tests), a test with no known target
        falls back to the row's relation, a row without a node to its relation."""
        uid = node_of(r.get("node_unique_id"))
        if uid and not uid.startswith("test."):
            return uid
        return resolve(r.get("relation")) or uid

    return node_of, resolve, target


def _incident_node(inc: dict, resolve) -> str | None:
    kind, key = inc.get("kind"), inc.get("node_key") or ""
    if kind in ("test_failure", "silence"):
        return key or None
    relation = key.split("|")[0]
    return resolve(relation)


def _incidents_out(feed, tests, execs, mets, declared, resolve, now) -> list[dict]:
    tests_by_id: dict[str, list[dict]] = {}
    for r in tests:
        tests_by_id.setdefault(r.get("test_unique_id"), []).append(r)
    execs_by_id: dict[str, list[dict]] = {}
    for r in execs:
        execs_by_id.setdefault(r.get("unique_id"), []).append(r)
    mets_by_rel: dict[str, list[dict]] = {}
    for r in mets:
        mets_by_rel.setdefault(r.get("relation"), []).append(r)
    channels = {r.get("unique_id"): r.get("channel") for r in declared}
    out = []
    for inc in feed:
        key = inc.get("node_key") or ""
        kind = inc.get("kind")
        # only the rows state() can look at for this incident: it scans what it is given
        st = _incidents.state(
            inc,
            test_results=tests_by_id.get(key, []) if kind == "test_failure" else [],
            node_executions=execs_by_id.get(key, []) if kind == "silence" else [],
            metrics=mets_by_rel.get(key.split("|")[0], []) if kind in ("anomaly", "cost") else [],
            now=now)
        latest_test = max(tests_by_id.get(key, []), key=lambda r: _dt(r.get("executed_at")) or datetime.min,
                          default=None) if kind == "test_failure" else None
        out.append({
            "incident_key": inc.get("incident_key"),
            "kind": kind,
            "node": key or None,
            "title": inc.get("title"),
            "severity": inc.get("severity"),
            "first_detected_at": _iso(inc.get("first_detected_at")),
            "occurrences": _int(inc.get("occurrences")),
            "acknowledged_by": inc.get("acknowledged_by"),
            "root_cause": inc.get("root_cause_unique_id") or (latest_test or {}).get("root_cause_unique_id"),
            "channel": channels.get(key) if kind == "silence" else None,
            "root_cause_kind": inc.get("root_cause_kind") or (latest_test or {}).get("root_cause_kind"),
            "node_unique_id": _incident_node(inc, resolve),
            "state": "open" if st == "stale" else st,
        })
    return out


def build(days: int = 30, layer: Any = None, now: datetime | None = None) -> dict:
    """The console's data object. `layer` is a queries.QueryLayer (default: the running page's)."""
    rd = _Reader(layer)
    now = _dt(now)
    inv = rd.read("INVOCATIONS", queries.invocations, limit=queries.MAX_LIMIT,
                  since=(now - timedelta(days=days)) if now else None)
    ref = now or _incidents.as_of(inv, keys=("started_at",)) or datetime.now(timezone.utc).replace(tzinfo=None)
    since = ref - timedelta(days=days)
    inv = [r for r in inv if (_dt(r.get("started_at")) or ref) >= since]
    execs = rd.read("NODE_EXECUTIONS", queries.node_executions_newest, since=since, limit=queries.MAX_LIMIT)
    tests = rd.read("TEST_RESULTS", queries.test_results, since=since, limit=queries.MAX_LIMIT)
    mets = rd.read("METRICS", queries.metrics, since=since, limit=queries.MAX_LIMIT)
    schema = rd.read("SCHEMA_CHANGES", queries.schema_changes, since=since, limit=queries.MAX_LIMIT)
    lineage = rd.read("LINEAGE_LATEST", queries.lineage_latest, limit=queries.MAX_LIMIT)
    declared = rd.read("SLA_DECLARED", queries.sla_declared, limit=queries.MAX_LIMIT)
    feed = rd.read("ALERTS_FEED", queries.alerts_feed, since=since, limit=queries.MAX_LIMIT)
    caps = rd.read("CAPABILITIES", queries.capabilities, limit=1)

    generated = _incidents.as_of(inv, execs, tests, mets, schema, lineage, feed, keys=_AS_OF_KEYS)

    test_edges = {r["test_unique_id"]: r["tested_unique_id"] for r in tests
                  if r.get("tested_unique_id") and r.get("test_unique_id")}
    nodes = _nodes(lineage, execs, tests, test_edges)
    names = {n["unique_id"]: n["name"] for n in nodes}

    node_of, resolve, target = _make_resolver(nodes, execs, schema, mets)

    runs = [{
        "invocation_id": r.get("invocation_id"), "command": r.get("command"), "started_at": _iso(r.get("started_at")),
        "duration_s": _num(r.get("duration_s")), "status": r.get("status"), "credits": _num(r.get("credits")),
        "nodes_total": _int(r.get("nodes_total")), "nodes_failed": _int(r.get("nodes_failed")),
        "target": r.get("target"),
    } for r in _by_time(inv, "started_at")]

    node_execs = [{
        "invocation_id": r.get("invocation_id"), "unique_id": r.get("unique_id"), "status": r.get("status"),
        "started_at": _iso(r.get("started_at")), "execution_time_s": _num(r.get("execution_time_s")),
        "credits": _num(r.get("credits")),
    } for r in _by_time(execs, "started_at")]

    test_out = [{
        "invocation_id": r.get("invocation_id"), "test_unique_id": r.get("test_unique_id"),
        "status": r.get("status"), "failures": _int(r.get("failures")),
        "root_cause_unique_id": r.get("root_cause_unique_id"), "root_cause_kind": r.get("root_cause_kind"),
    } for r in _by_time(tests, "executed_at")]

    metrics_out = []
    for r in _by_time(mets, "observed_at"):
        scale = 1 / 60 if r.get("metric") == FRESHNESS_METRIC else 1
        conv = (lambda v: _num(v, 2)) if scale != 1 else _num
        metrics_out.append({
            "relation": r.get("relation"),
            "unique_id": target(r),
            "metric": FRESHNESS_OUT if scale != 1 else r.get("metric"),
            "observed_at": _iso(r.get("observed_at")),
            "value": conv(None if r.get("value") is None else float(r["value"]) * scale),
            **{k: conv(None if r.get(src) is None else float(r[src]) * scale)
               for k, src in (("expected", "anomaly_expected"), ("lower", "anomaly_lower"),
                              ("upper", "anomaly_upper"))},
            "anomaly": _bool(r.get("anomaly_is_anomaly")), "engine": r.get("anomaly_engine"),
            "score": _num(r.get("anomaly_score")),
        })

    fresh: dict[str, dict] = {}   # the node the UI looks up (FRESH[unique_id]): the latest reading, one per node
    limits: dict[str, Any] = {}
    for r in _by_time([m for m in mets if m.get("metric") == FRESHNESS_METRIC], "observed_at"):
        uid = target(r)
        if uid is None:
            continue
        fresh[uid] = r
        if _num(r.get("threshold")) is not None:
            limits[uid] = _num(r["threshold"])
    freshness = []
    for uid, r in sorted(fresh.items()):
        lag, thr = _num(r.get("value")), limits.get(uid)
        freshness.append({
            "unique_id": uid, "name": names.get(uid) or _name_of(uid),
            "freshness_lag_min": None if lag is None else int(round(lag / 60)),
            "max_lag_min": None if thr is None else int(round(thr / 60)),
        })

    schema_out = []
    for r in _by_time(schema, "detected_at"):
        kind = r.get("change_kind")
        schema_out.append({
            "relation": r.get("relation"), "column": r.get("column"), "change": kind,
            "type": r.get("to_type") if kind == "added" else r.get("from_type") if kind == "removed" else None,
            "detected_at": _iso(r.get("detected_at")),
            "from": r.get("from_type") if kind == "type_changed" else None,
            "to": r.get("to_type") if kind == "type_changed" else None,
        })

    last_ok: dict[str, datetime] = {}
    for r in execs:
        at = _dt(r.get("started_at")) or _dt(r.get("completed_at"))
        if r.get("unique_id") and at and str(r.get("status") or "").lower() == "success":
            if r["unique_id"] not in last_ok or at > last_ok[r["unique_id"]]:
                last_ok[r["unique_id"]] = at
    slas = [{
        "unique_id": r["unique_id"], "name": names.get(r["unique_id"]) or _name_of(r["unique_id"]),
        "expected_every": _minutes(r.get("expected_every_minutes")), "grace": _minutes(r.get("grace_minutes")),
        "last_success_at": _iso(last_ok[r["unique_id"]]) if r["unique_id"] in last_ok else None,
    } for r in sorted((d for d in declared if d.get("unique_id")), key=lambda d: d["unique_id"])]

    incidents_out = _incidents_out(feed, tests, execs, mets, declared, resolve, now or generated or ref)

    cap = caps[0] if caps else {}
    versions = list(dict.fromkeys(str(r["dbt_version"]) for r in reversed(_by_time(inv, "started_at"))
                                  if r.get("dbt_version")))
    capabilities = {
        "edition": cap.get("edition"), "event_table": cap.get("event_table"),
        "cortex": _bool(cap.get("cortex_available")), "icecore_version": cap.get("icecore_version"),
        "dbt_versions": versions or None,
    } if caps or versions else dict(EMPTY_CAPABILITIES)

    projects = [r.get("project") for rows in (_by_time(inv, "started_at")[::-1], lineage, tests) for r in rows
                if r.get("project")]
    return {
        "project": projects[0] if projects else None,
        "generated_at": _iso(generated),
        "nodes": nodes, "runs": runs, "node_executions": node_execs, "test_results": test_out,
        "metrics": metrics_out, "source_freshness": freshness, "schema_changes": schema_out, "slas": slas,
        "incidents": incidents_out, "capabilities": capabilities,
        "errors": rd.errors,
    }
