// Run by tests/test_console_layers.py: prints JSON of the layer model for the prototype's mock data and for a project with its own layer names.
import fs from 'node:fs';
import vm from 'node:vm';
const root = process.argv[2];
const m = await import(root + '/streamlit/components/console/frontend/src/lib/model.js');
const ctx = { window: {} }; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(root + '/docs/design/app-prototype/icecore-mock-data.js', 'utf8'), ctx);
const out = {};
m.loadData(JSON.parse(JSON.stringify(ctx.window.ICECORE_DATA)));
out.mock = { keys: m.MODEL_KEYS, labels: m.MODEL_KEYS.map(k => m.LAYER_LABEL[k]), order: m.LAYER_ORDER, health: m.computeHealth([]).map(h => h.layer),
  goldAnalytics: m.LAYER_GROUP('gold_analytics') };
const n = (id, type, layer, deps = []) => ({ unique_id: id, type, name: id.split('.').pop(), layer, depends_on: deps });
m.loadData({ nodes: [n('source.p.raw', 'source', 'source'), n('seed.p.s', 'seed', 'seed'), n('snapshot.p.sn', 'snapshot', 'snapshot'),
  n('model.p.fct', 'model', 'marts', ['model.p.stg']), n('model.p.stg', 'model', 'staging', ['source.p.raw']),
  n('model.p.nolayer', 'model', null), n('test.p.t', 'test', 'test', ['model.p.fct'])] });
out.own = { keys: m.MODEL_KEYS, labels: m.MODEL_KEYS.map(k => m.LAYER_LABEL[k]), order: m.LAYER_ORDER, health: m.computeHealth([]).map(h => h.layer),
  staging: m.LAYER_GROUP('staging'), marts: m.LAYER_GROUP('marts'), seed: m.LAYER_GROUP('seed') };
m.loadData({});
out.none = { keys: m.MODEL_KEYS, labels: m.MODEL_KEYS.map(k => m.LAYER_LABEL[k]) };
console.log(JSON.stringify(out));
