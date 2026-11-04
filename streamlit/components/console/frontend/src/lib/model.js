/* Data model of prototype D, derived only from the data payload (args.data in Streamlit, ICECORE_DATA in dev mode).
   The prototype computed these as top-level constants from window.ICECORE_DATA; here they are module bindings that
   loadData() recomputes, so a new payload from Python replaces them (ES module imports are live bindings).
   The derivations are the prototype's, line for line; normalise() only makes a partial payload safe to derive from. */
import { MS, parseT, fmtSpan, durStr, sum, humanMetric } from './format.js';

export const NOT_RECORDED = 'not recorded yet';

const LISTS = ['nodes', 'runs', 'node_executions', 'test_results', 'metrics', 'source_freshness', 'schema_changes', 'slas', 'incidents'];
const num = v => (v == null || v === '' ? null : isFinite(+v) ? +v : null);
const str = v => (v == null ? null : String(v));
const lastSeg = id => String(id).split('.').pop();

/* Fill what the derivations below index into: lists, ids, names, numbers. A missing value stays null; the pages
   show it as "not recorded yet". Records without their key (unique_id, invocation_id, incident_key) are dropped. */
export function normalise(raw) {
  const d = raw && typeof raw === 'object' ? { ...raw } : {};
  LISTS.forEach(k => { d[k] = Array.isArray(d[k]) ? d[k].filter(r => r && typeof r === 'object') : []; });
  d.project = d.project == null ? NOT_RECORDED : String(d.project);
  d.capabilities = d.capabilities && typeof d.capabilities === 'object' ? { ...d.capabilities } : {};
  const std = d.capabilities.layer_standard;
  d.capabilities.layer_standard = Array.isArray(std) ? std.filter(s => s != null && String(s).trim() !== '').map(s => String(s).trim()) : null;
  d.errors = Array.isArray(d.errors) ? d.errors : [];
  d.nodes = d.nodes.filter(n => n.unique_id).map(n => {
    const uid = String(n.unique_id); const type = n.type || uid.split('.')[0];
    return { ...n, unique_id: uid, type, name: n.name == null ? lastSeg(uid) : String(n.name),
      layer: n.layer == null ? (['source', 'seed', 'snapshot'].includes(type) ? type : null) : String(n.layer),
      depends_on: Array.isArray(n.depends_on) ? n.depends_on.map(String) : [] };
  });
  d.runs = d.runs.filter(r => r.invocation_id && parseT(r.started_at) != null).map(r => ({ ...r, invocation_id: String(r.invocation_id),
    duration_s: num(r.duration_s), credits: num(r.credits), nodes_total: num(r.nodes_total), nodes_failed: num(r.nodes_failed),
    status: r.status == null ? null : String(r.status).toLowerCase(), command: str(r.command), target: str(r.target) }));
  d.node_executions = d.node_executions.filter(e => e.invocation_id && e.unique_id).map(e => ({ ...e,
    execution_time_s: num(e.execution_time_s), credits: num(e.credits), status: e.status == null ? null : String(e.status).toLowerCase() }));
  d.test_results = d.test_results.filter(r => r.invocation_id && r.test_unique_id).map(r => ({ ...r,
    status: r.status == null ? null : String(r.status).toLowerCase(), failures: num(r.failures) }));
  d.metrics = d.metrics.filter(m => m.unique_id && parseT(m.observed_at) != null).map(m => ({ ...m, relation: m.relation == null ? lastSeg(m.unique_id) : String(m.relation),
    metric: m.metric == null ? 'metric' : String(m.metric), engine: m.engine || 'native', anomaly: !!m.anomaly,
    value: num(m.value), expected: num(m.expected), lower: num(m.lower), upper: num(m.upper), score: num(m.score) }));
  d.source_freshness = d.source_freshness.filter(f => f.unique_id).map(f => ({ ...f, name: f.name == null ? lastSeg(f.unique_id) : String(f.name),
    freshness_lag_min: num(f.freshness_lag_min), max_lag_min: num(f.max_lag_min) }));
  d.schema_changes = d.schema_changes.map(c => ({ ...c, relation: c.relation == null ? NOT_RECORDED : String(c.relation), column: c.column == null ? '' : String(c.column) }));
  d.slas = d.slas.filter(s => s.unique_id).map(s => ({ ...s, name: s.name == null ? lastSeg(s.unique_id) : String(s.name) }));
  d.incidents = d.incidents.filter(i => i.incident_key).map(i => ({ ...i, incident_key: String(i.incident_key), node: i.node == null ? '' : String(i.node),
    title: i.title == null ? `${i.kind || 'Incident'} on ${i.node || NOT_RECORDED}` : String(i.title), severity: i.severity || 'warn',
    occurrences: num(i.occurrences) == null ? 1 : num(i.occurrences), state: i.state == null ? null : String(i.state).toLowerCase() }));
  return d;
}

export let D, RUNS, NR, LAST, RUN_IDX, AS_OF, NODE, ASSETS, TESTS, BY_NAME, PARENTS, CHILDREN, TESTS_OF, TR, TRF, TRC, FAILS, TESTED,
  EX, EX_RUN, SERIES, SERIES_LIST, SLAS, SLA_BY, FRESH, SCHEMA, CAP, LAYER_ISSUES, LAYER_ISSUES_OF;

export const testTarget = t => { const d = (t.depends_on || []).filter(x => NODE[x]); return d.length ? d[d.length - 1] : null; };
export const isBad = s => s === 'fail' || s === 'error';
export const latestIdx = tid => { const a = TR[tid]; if (!a) return -1; for (let i = a.length - 1; i >= 0; i--) if (a[i] != null) return i; return -1; };
export const latestTest = tid => { const i = latestIdx(tid); return i < 0 ? null : TR[tid][i]; };
export const seriesLatest = s => { let best = null; Object.values(s.pts).forEach(a => { const p = a[a.length - 1]; if (p && (!best || p.t > best.t)) best = p; }); return best; };
export const seriesAnomalous = s => Object.values(s.pts).some(a => a.length && a[a.length - 1].anomaly);

/* Layers. The prototype hardcoded bronze/silver/gold; real projects name their own. The model layers are the distinct
   `layer` values of the model nodes (not seed, source, snapshot, test, transient or null), ordered upstream first by DAG
   depth. Each takes a position key (bronze, silver, gold, then l4...): the key carries the accent colour (--bronze...)
   and the label is the project's own name. With no model layer the prototype's three are used. */
export const POS_KEYS = ['bronze', 'silver', 'gold', 'l4', 'l5', 'l6', 'l7', 'l8'];
const FIXED_GROUP = { transient: 'transient', seed: 'sources', source: 'sources', snapshot: 'snapshot' };
const canonLayer = l => (l === 'gold_analytics' ? 'gold' : l);
const titleCase = l => String(l).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
let POS_OF = {};
export let LAYER_SEQ = [];            // the project's model layers, upstream first (all of them, not only the first 8)
export let HAS_MODEL_LAYERS = false;
export let MODEL_KEYS = ['bronze', 'silver', 'gold'];
export let LAYER_LABEL = { sources: 'Sources & seeds', transient: 'Transient', bronze: 'Bronze', silver: 'Silver', gold: 'Gold', snapshot: 'Snapshots' };
export let LAYER_ORDER = ['sources', 'transient', 'bronze', 'silver', 'gold', 'snapshot'];
export const LAYER_GROUP = l => FIXED_GROUP[l] || POS_OF[canonLayer(l)] || canonLayer(l);

function setLayers(nodes) {
  const byId = {}; nodes.forEach(n => { byId[n.unique_id] = n; });
  const depth = {};
  const dep = (id, seen = new Set()) => {
    if (depth[id] != null) return depth[id];
    if (seen.has(id)) return 0; seen.add(id);
    const n = byId[id]; if (!n) return 0;
    const ps = (n.depends_on || []).filter(p => byId[p] && byId[p].type !== 'test');
    return (depth[id] = ps.length ? 1 + Math.max(...ps.map(p => dep(p, seen))) : 0);
  };
  const acc = {};
  nodes.filter(n => n.type === 'model' && n.layer != null && !FIXED_GROUP[n.layer] && n.layer !== 'test').forEach(n => {
    const l = canonLayer(n.layer); const a = acc[l] || (acc[l] = { sum: 0, n: 0 }); a.sum += dep(n.unique_id); a.n += 1; });
  LAYER_SEQ = Object.keys(acc).sort((a, b) => acc[a].sum / acc[a].n - acc[b].sum / acc[b].n || a.localeCompare(b));
  HAS_MODEL_LAYERS = LAYER_SEQ.length > 0;
  const layers = LAYER_SEQ.slice(0, POS_KEYS.length);
  POS_OF = {}; layers.forEach((l, i) => { POS_OF[l] = POS_KEYS[i]; });
  MODEL_KEYS = layers.length ? layers.map((_, i) => POS_KEYS[i]) : ['bronze', 'silver', 'gold'];
  const label = { sources: 'Sources & seeds', transient: 'Transient', snapshot: 'Snapshots' };
  if (layers.length) layers.forEach(l => { label[POS_OF[l]] = titleCase(l); }); else Object.assign(label, { bronze: 'Bronze', silver: 'Silver', gold: 'Gold' });
  LAYER_LABEL = label;
  // the fixed groups only when the project has such nodes: no empty "Transient" or "Snapshots" button
  const present = new Set(nodes.filter(n => n.type !== 'test' && n.layer != null).map(n => FIXED_GROUP[n.layer]).filter(Boolean));
  LAYER_ORDER = [...['sources', 'transient'].filter(g => present.has(g)), ...MODEL_KEYS, ...(present.has('snapshot') ? ['snapshot'] : [])];
}

export function loadData(raw) {
  D = normalise(raw);
  /* ---------------- data model (derived only from window.ICECORE_DATA) ---------------- */
  RUNS = [...D.runs].sort((a, b) => parseT(a.started_at) - parseT(b.started_at)).map((r, i) => ({ ...r, t: parseT(r.started_at), idx: i }));
  NR = RUNS.length, LAST = NR - 1;
  RUN_IDX = {}; RUNS.forEach((r, i) => { RUN_IDX[r.invocation_id] = i; });
  AS_OF = Math.max(parseT(D.generated_at) || 0, ...RUNS.map(r => r.t + (r.duration_s || 0) * 1000)) || Date.now();
  NODE = {}; D.nodes.forEach(n => { NODE[n.unique_id] = n; });
  ASSETS = D.nodes.filter(n => n.type !== 'test');
  TESTS = D.nodes.filter(n => n.type === 'test');
  BY_NAME = {}; ASSETS.forEach(n => { BY_NAME[n.name] = n.unique_id; });
  PARENTS = {}, CHILDREN = {};
  ASSETS.forEach(n => { PARENTS[n.unique_id] = n.depends_on.filter(p => NODE[p] && NODE[p].type !== 'test'); CHILDREN[n.unique_id] = []; });
  ASSETS.forEach(n => PARENTS[n.unique_id].forEach(p => CHILDREN[p].push(n.unique_id)));
  setLayers(D.nodes);
  TESTS_OF = {}; TESTS.forEach(t => { const m = testTarget(t); if (m) { (TESTS_OF[m] = TESTS_OF[m] || []).push(t.unique_id); } });
  TR = {}, TRF = {};
  D.test_results.forEach(r => {
    const i = RUN_IDX[r.invocation_id]; if (i == null) return;
    if (!TR[r.test_unique_id]) { TR[r.test_unique_id] = Array(NR).fill(null); TRF[r.test_unique_id] = Array(NR).fill(null); }
    TR[r.test_unique_id][i] = r.status; TRF[r.test_unique_id][i] = r.failures;
  });
  TRC = {};
  D.test_results.forEach(r => { const i = RUN_IDX[r.invocation_id]; if (i == null || !r.root_cause_unique_id) return; if (!TRC[r.test_unique_id] || i > TRC[r.test_unique_id].i) TRC[r.test_unique_id] = { id: r.root_cause_unique_id, kind: r.root_cause_kind, i }; });
  FAILS = RUNS.map((r, i) => TESTS.filter(t => TR[t.unique_id] && isBad(TR[t.unique_id][i])).length);
  TESTED = RUNS.map((r, i) => TESTS.filter(t => TR[t.unique_id] && TR[t.unique_id][i] != null).length);
  EX = {}, EX_RUN = {};
  D.node_executions.forEach(e => {
    const i = RUN_IDX[e.invocation_id]; if (i == null) return;
    const x = { ...e, t: parseT(e.started_at) || RUNS[i].t, i };
    if (!EX[e.unique_id]) EX[e.unique_id] = Array(NR).fill(null);
    EX[e.unique_id][i] = x; (EX_RUN[e.invocation_id] = EX_RUN[e.invocation_id] || []).push(x);
  });
  Object.values(EX_RUN).forEach(a => a.sort((x, y) => x.t - y.t));
  SERIES = {};
  D.metrics.forEach(m => {
    const k = m.unique_id + '|' + m.metric;
    if (!SERIES[k]) SERIES[k] = { key: k, uid: m.unique_id, relation: m.relation, metric: m.metric, pts: {} };
    (SERIES[k].pts[m.engine] = SERIES[k].pts[m.engine] || []).push({ ...m, t: parseT(m.observed_at) });
  });
  Object.values(SERIES).forEach(s => Object.values(s.pts).forEach(a => a.sort((x, y) => x.t - y.t)));
  SERIES_LIST = Object.values(SERIES);
  /* A schedule whose last success is not recorded is neither late nor on time: `unknown`. */
  SLAS = D.slas.map(s => { const last = parseT(s.last_success_at); const allowed = durStr(s.expected_every) + durStr(s.grace); if (last == null) return { ...s, last: null, allowed, due: null, late: false, used: null, unknown: true };
    const due = last + allowed; return { ...s, last, allowed, due, late: AS_OF > due, used: (AS_OF - last) / allowed }; });
  SLA_BY = {}; SLAS.forEach(s => { SLA_BY[s.unique_id] = s; });
  FRESH = {}; D.source_freshness.forEach(f => { FRESH[f.unique_id] = f; });
  SCHEMA = D.schema_changes.map(c => ({ ...c, t: parseT(c.detected_at) })).sort((a, b) => b.t - a.t);
  CAP = D.capabilities || {};
  LAYER_ISSUES = layerIssues(); LAYER_ISSUES_OF = {};
  LAYER_ISSUES.forEach(i => { (LAYER_ISSUES_OF[i.unique_id] = LAYER_ISSUES_OF[i.unique_id] || []).push(i); });
}

/* Layer design checks, inside one project (story cfc1e7fc). They inform only: nothing here feeds a score, a status
   or an incident. Models only; a model in a group icecore makes on its own (transient, seed, source, snapshot) is
   never flagged, and with no model layer at all there is nothing to check.
   - backward: the model reads a model of a layer further downstream (layers upstream first, as setLayers orders them);
   - nolayer: the model sits directly under models/ (no layer), counted only when the model ran or is in the lineage,
     so a layer that is merely not recorded is not flagged;
   - standard: var icecore.layer_standard is declared (capabilities.layer_standard, a list) and the layer is not in it. */
export const LAYER_ISSUE_LABEL = { backward: 'Backward dependency', nolayer: 'No layer', standard: 'Not in the standard' };
const layerName = l => (POS_OF[l] ? LAYER_LABEL[POS_OF[l]] : titleCase(l));
function layerIssues() {
  if (!HAS_MODEL_LAYERS) return [];
  const rank = {}; LAYER_SEQ.forEach((l, i) => { rank[l] = i; });
  const std = CAP.layer_standard ? CAP.layer_standard.map(s => s.toLowerCase()) : null;
  const out = [];
  ASSETS.filter(n => n.type === 'model' && !FIXED_GROUP[n.layer]).forEach(n => {
    const id = n.unique_id;
    if (n.layer == null) {
      if (EX[id] || PARENTS[id].length || CHILDREN[id].length) out.push({ unique_id: id, name: n.name, kind: 'nolayer', reason: 'Sits directly under models/, in no layer folder' });
      return;
    }
    const l = canonLayer(n.layer);
    const down = PARENTS[id].map(p => NODE[p]).filter(p => p.type === 'model' && p.layer != null && rank[canonLayer(p.layer)] > rank[l]).sort((a, b) => a.name.localeCompare(b.name));
    if (down.length) out.push({ unique_id: id, name: n.name, kind: 'backward', parents: down.map(p => p.unique_id),
      reason: `Reads ${down.map(p => `${p.name}, from ${layerName(canonLayer(p.layer))}`).join(', and ')}, ${down.length > 1 ? 'downstream layers' : 'a downstream layer'}` });
    if (std && !std.includes(l.toLowerCase())) out.push({ unique_id: id, name: n.name, kind: 'standard', reason: `Layer \u201c${n.layer}\u201d is not one of ${CAP.layer_standard.join(', ')}` });
  });
  const order = { backward: 0, nolayer: 1, standard: 2 };
  return out.sort((a, b) => order[a.kind] - order[b.kind] || a.name.localeCompare(b.name));
}
loadData({});

export const KIND_LABEL = { test_failure: 'Test failure', anomaly: 'Anomaly', schema_change: 'Schema change', silence: 'Missed schedule', cost: 'Cost' };
export const STATUS_LABEL = { failed: 'Failed', warn: 'Warning', stale: 'Late', ok: 'OK', notrun: 'Not run' };
export const STATUS_TONE = { failed: 'fail', warn: 'warn', stale: 'stale', ok: 'ok', notrun: 'muted' };
export const CHANGE_LABEL = { added: 'Column added', removed: 'Column removed', type_changed: 'Type changed' };

export const incNode = inc => { if (inc.node_unique_id && NODE[inc.node_unique_id]) return inc.node_unique_id; if (NODE[inc.node]) return inc.node; return BY_NAME[String(inc.node).split('|')[0]] || null; };
export const incAsset = inc => { const id = incNode(inc); if (!id) return null; return NODE[id].type === 'test' ? testTarget(NODE[id]) : id; };
export const declaredRoot = id => { for (const t of (TESTS_OF[id] || [])) if (isBad(latestTest(t)) && TRC[t] && NODE[TRC[t].id]) return TRC[t];
  const inc = D.incidents.find(i => i.root_cause && NODE[i.root_cause] && incAsset(i) === id); return inc ? { id: inc.root_cause, kind: inc.root_cause_kind } : null; };
export const incTarget = inc => String(inc.node).split('|')[0].replace(/^test\.[^.]+\./, '').replace(/\.[0-9a-f]{10}$/, '');

export function computeStatus(incidents) {
  const rank = { ok: 0, notrun: 1, warn: 2, stale: 3, failed: 4 };
  const st = {};
  ASSETS.forEach(n => {
    const id = n.unique_id; let s = 'ok'; const reasons = [];
    const up = (x, r) => { reasons.push(r); if (rank[x] > rank[s]) s = x; };
    const ex = EX[id] ? EX[id][LAST] : null;
    if (n.type === 'source') { const f = FRESH[id]; if (f && f.max_lag_min != null && f.freshness_lag_min != null && f.freshness_lag_min > f.max_lag_min) up('stale', `Source data is ${fmtSpan(f.freshness_lag_min * MS.m)} old (limit ${f.max_lag_min} min)`); }
    else if (!ex) up('notrun', 'Not built in the latest run');
    else if (ex.status !== 'success') up('failed', 'Build failed in the latest run');
    const tids = TESTS_OF[id] || [];
    const ft = tids.filter(t => isBad(latestTest(t))); if (ft.length) up('failed', `${ft.length} failing test${ft.length > 1 ? 's' : ''}`);
    const wt = tids.filter(t => latestTest(t) === 'warn'); if (wt.length) up('warn', `${wt.length} test warning${wt.length > 1 ? 's' : ''}`);
    SERIES_LIST.filter(x => x.uid === id && seriesAnomalous(x)).forEach(x => up('warn', `${humanMetric(x.metric)} outside its expected band`));
    const sla = SLA_BY[id]; if (sla && sla.late) up('stale', `Missed its ${sla.expected_every} schedule`);
    incidents.filter(i => i.state !== 'resolved' && i.kind !== 'test_failure' && incAsset(i) === id)
      .forEach(i => up(i.kind === 'silence' ? 'stale' : (i.severity === 'error' ? 'failed' : 'warn'), i.title));
    st[id] = { status: s, reasons };
  });
  return st;
}

export const WEIGHTS = { tests: 0.5, fresh: 0.2, sched: 0.15, anom: 0.15 };
export function computeHealth(incidents) {
  return MODEL_KEYS.map(L => {
    const models = ASSETS.filter(n => LAYER_GROUP(n.layer) === L);
    const ids = new Set(models.map(m => m.unique_id));
    let pass = 0, warn = 0, fail = 0;
    TESTS.filter(t => ids.has(testTarget(t))).forEach(t => { const s = latestTest(t.unique_id); if (s === 'pass') pass++; else if (s === 'warn') warn++; else if (s) fail++; });
    const nt = pass + warn + fail;
    const built = models.filter(m => EX[m.unique_id] && EX[m.unique_id][LAST]);
    const okb = built.filter(m => EX[m.unique_id][LAST].status === 'success');
    const slas = SLAS.filter(s => ids.has(s.unique_id) && !s.unknown);
    const missed = slas.filter(s => s.late || incidents.some(i => i.kind === 'silence' && i.state !== 'resolved' && incAsset(i) === s.unique_id));
    const ser = SERIES_LIST.filter(s => ids.has(s.uid)); const an = ser.filter(seriesAnomalous);
    const comps = [
      { key: 'tests', label: 'Tests', w: WEIGHTS.tests, v: nt ? (pass * 100 + warn * 50) / nt : null, detail: nt ? `(${pass} pass × 100 + ${warn} warn × 50 + ${fail} fail × 0) ÷ ${nt} tests` : 'No tests attached to this layer' },
      { key: 'fresh', label: 'Freshness', w: WEIGHTS.fresh, v: built.length ? okb.length / built.length * 100 : null, detail: built.length ? `${okb.length} of ${built.length} models built successfully in the latest run` : 'No model built in the latest run' },
      { key: 'sched', label: 'Schedules', w: WEIGHTS.sched, v: slas.length ? (slas.length - missed.length) / slas.length * 100 : null, detail: slas.length ? `${slas.length - missed.length} of ${slas.length} declared schedules met (late now or open missed-schedule alert counts as missed)` : 'No schedule declared in this layer' },
      { key: 'anom', label: 'Anomalies', w: WEIGHTS.anom, v: ser.length ? (ser.length - an.length) / ser.length * 100 : null, detail: ser.length ? `${ser.length - an.length} of ${ser.length} monitored metrics inside their expected band` : 'No monitored metric in this layer' },
    ];
    const used = comps.filter(c => c.v != null); const W = sum(used.map(c => c.w));
    const score = W ? sum(used.map(c => c.w * c.v)) / W : null;
    return { layer: L, score, comps, W, models: models.length, tests: nt, fail };
  });
}
