// Run by tests/test_console_layer_design.py: prints JSON of the layer design checks (lib/model.js) for small projects.
const root = process.argv[2];
const m = await import(root + '/streamlit/components/console/frontend/src/lib/model.js');
const n = (id, type, layer, deps = []) => ({ unique_id: id, type, name: id.split('.').pop(), layer, depends_on: deps });
const ex = ids => ({ runs: [{ invocation_id: 'r1', started_at: '2026-11-03T08:00:00', status: 'success' }],
  node_executions: ids.map(id => ({ invocation_id: 'r1', unique_id: id, status: 'success', started_at: '2026-11-03T08:00:00' })) });
const issues = () => m.LAYER_ISSUES.map(i => ({ id: i.unique_id, kind: i.kind, reason: i.reason }));
const snap = () => ({ health: JSON.stringify(m.computeHealth([])), status: JSON.stringify(m.computeStatus([])) });
const BASE = [n('source.p.raw', 'source', 'source'), n('seed.p.s', 'seed', 'seed'), n('snapshot.p.sn', 'snapshot', 'snapshot'),
  n('model.p.stg_a', 'model', 'staging', ['source.p.raw']), n('model.p.stg_b', 'model', 'staging', ['seed.p.s']),
  ...['c', 'd', 'e'].map(k => n('model.p.stg_' + k, 'model', 'staging', ['source.p.raw'])),
  n('model.p.int_a', 'model', 'intermediate', ['model.p.stg_a']), n('model.p.fct', 'model', 'marts', ['model.p.int_a', 'model.p.stg_b']),
  n('model.p.tmp', 'model', 'transient', ['model.p.fct']), n('test.p.t', 'test', 'test', ['model.p.fct'])];
const IDS = BASE.filter(x => x.type === 'model').map(x => x.unique_id);
const out = {};
const run = (key, nodes, caps, extraIds = []) => { m.loadData({ nodes, capabilities: caps, ...ex([...IDS, ...extraIds]) });
  out[key] = { issues: issues(), count: m.LAYER_ISSUES.length, hasLayers: m.HAS_MODEL_LAYERS, seq: m.LAYER_SEQ, ...snap() }; };
run('clean', BASE, {});
run('cleanStd', BASE, { layer_standard: ['staging', 'Intermediate', 'marts'] });
// stg_b reads fct (marts, two layers downstream) and int_a (one downstream): one backward issue naming both
run('backward', BASE.map(x => x.unique_id === 'model.p.stg_b' ? { ...x, depends_on: ['seed.p.s', 'model.p.fct', 'model.p.int_a'] } : x), {});
run('nolayer', [...BASE, n('model.p.root_model', 'model', null, ['model.p.stg_a']), n('model.p.never_ran', 'model', null)], {}, ['model.p.root_model']);
run('standard', [...BASE, n('model.p.fx', 'model', 'exports', ['model.p.fct'])], { layer_standard: ['staging', 'intermediate', 'marts'] }, ['model.p.fx']);
run('nostandard', [...BASE, n('model.p.fx', 'model', 'exports', ['model.p.fct'])], { layer_standard: null }, ['model.p.fx']);
run('nolayers', [n('source.p.raw', 'source', 'source'), n('model.p.a', 'model', null, ['source.p.raw'])], {}, ['model.p.a']);
// review of #138: an empty standard is no standard; the raw layer is compared; the checked edges cannot flip the order
run('emptyStd', BASE, { layer_standard: [] });
out.emptyStd.cap = m.CAP.layer_standard;
const GA = [n('source.p.raw', 'source', 'source'), n('model.p.brz', 'model', 'bronze', ['source.p.raw']),
  n('model.p.fct', 'model', 'gold', ['model.p.brz']), n('model.p.agg', 'model', 'gold_analytics', ['model.p.fct'])];
m.loadData({ nodes: GA, capabilities: { layer_standard: ['bronze', 'gold_analytics'] }, ...ex(['model.p.brz', 'model.p.fct', 'model.p.agg']) });
out.rawStd = issues();
m.loadData({ nodes: GA, capabilities: { layer_standard: ['bronze', 'gold'] }, ...ex(['model.p.brz', 'model.p.fct', 'model.p.agg']) });
out.rawStdOut = issues();
const REV = [n('source.p.raw', 'source', 'source'), n('model.p.stg_a', 'model', 'staging', ['source.p.raw']),
  n('model.p.fct', 'model', 'marts', ['model.p.stg_a']), n('model.p.stg_b', 'model', 'staging', ['model.p.fct'])];
const REV_IDS = ['model.p.stg_a', 'model.p.fct', 'model.p.stg_b'];
m.loadData({ nodes: REV, capabilities: {}, ...ex(REV_IDS) }); out.reviewer = issues();
m.loadData({ nodes: REV, capabilities: { layer_standard: ['staging', 'marts'] }, ...ex(REV_IDS) }); out.reviewerStd = issues();
m.loadData({ nodes: REV, capabilities: { layer_standard: ['marts', 'staging'] }, ...ex(REV_IDS) }); out.reviewerStdReversed = issues();
// the flags inform only: the same project with and without a standard scores the same
m.loadData({ nodes: [...BASE, n('model.p.fx', 'model', 'exports', ['model.p.fct'])], capabilities: { layer_standard: ['staging'] }, ...ex([...IDS, 'model.p.fx']) });
const a = snap(); m.loadData({ nodes: [...BASE, n('model.p.fx', 'model', 'exports', ['model.p.fct'])], capabilities: {}, ...ex([...IDS, 'model.p.fx']) });
out.informOnly = { same: JSON.stringify(a) === JSON.stringify(snap()), issuesWith: 0 };
m.loadData({ nodes: BASE, capabilities: { layer_standard: 'staging' } }); out.badStandard = m.CAP.layer_standard;
console.log(JSON.stringify(out));
