import React, { useState, useMemo } from 'react';
import { fmtSec, sum } from '../lib/format.js';
import { LAST, ASSETS, TESTS, TESTS_OF, EX, isBad, latestTest, LAYER_GROUP, LAYER_LABEL, LAYER_ORDER, STATUS_LABEL } from '../lib/model.js';
import { useApp, pal, statusColor, base, ax, EChart } from '../lib/charts.jsx';
import { StatusPill, Layer, Spark, Kpi, Seg, Card, PageHead, DataTable } from '../components/ui.jsx';

/* ---------------- Models (catalog entry to node pages) ---------------- */
export function Models() {
  const { status, go, theme } = useApp();
  const [lay, setLay] = useState('all'); const [st, setSt] = useState('all'); const [q, setQ] = useState('');
  const rows = ASSETS.filter(n => (lay === 'all' || LAYER_GROUP(n.layer) === lay) && (st === 'all' || status[n.unique_id].status === st) && (!q || n.name.toLowerCase().includes(q.toLowerCase())));
  const opt = useMemo(() => { const p = pal(); const ss = ['failed', 'warn', 'stale', 'notrun', 'ok'];
    return base(p, {
      grid: { left: 120, right: 20, top: 30, bottom: 46 },
      legend: { top: 0, right: 0, textStyle: { color: p.ink2, fontSize: 11 } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 } },
      xAxis: ax(p, 'Nodes (count)', { type: 'value', minInterval: 1 }),
      yAxis: ax(p, '', { type: 'category', data: LAYER_ORDER.map(l => LAYER_LABEL[l]), inverse: true, splitLine: { show: false }, axisLabel: { color: p.ink2 } }),
      series: ss.map(s => ({ name: STATUS_LABEL[s], type: 'bar', stack: 'x', barMaxWidth: 16, color: statusColor(p, s),
        data: LAYER_ORDER.map(l => ASSETS.filter(n => LAYER_GROUP(n.layer) === l && status[n.unique_id].status === s).length) })),
    }); }, [status, theme]);
  return <div className="page">
    <PageHead title="Models & sources" sub="Every model, seed, snapshot and source in the project. Click a row for its node page." />
    <div className="kpis">
      <Kpi label="Models" value={ASSETS.filter(n => n.type === 'model').length} sub={`${ASSETS.filter(n => n.materialization === 'incremental').length} incremental, ${ASSETS.filter(n => n.materialization === 'table').length} tables`} />
      <Kpi label="Sources" value={ASSETS.filter(n => n.type === 'source').length} sub={`${ASSETS.filter(n => n.type === 'seed').length} seeds, ${ASSETS.filter(n => n.type === 'snapshot').length} snapshot`} />
      <Kpi label="Tests" value={TESTS.length} sub={`${(sum(ASSETS.map(n => (TESTS_OF[n.unique_id] || []).length)) / Math.max(1, ASSETS.length)).toFixed(1)} per node on average`} />
      <Kpi label="Not OK" value={ASSETS.filter(n => status[n.unique_id].status !== 'ok').length} tone="warn" sub="failed, warning, late or not run" />
    </div>
    <div className="fbar">
      <Seg label="Layer" options={[['all', 'All'], ...LAYER_ORDER.map(l => [l, LAYER_LABEL[l]])]} value={lay} onChange={setLay} />
      <Seg label="Status" options={[['all', 'All'], ...['failed', 'warn', 'stale', 'ok'].map(s => [s, STATUS_LABEL[s]])]} value={st} onChange={setSt} />
      <div className="fgrp"><label htmlFor="mq">Search</label><input id="mq" className="inp" placeholder="Name" value={q} onChange={e => setQ(e.target.value)} /></div>
    </div>
    <Card title="Status by layer" sub="Count of nodes per layer and status.">
      <EChart option={opt} height={230} />
    </Card>
    <Card title="All nodes" sub="Problems first. Click a row for the node page." flush>
      <DataTable rows={rows} rowKey={r => r.unique_id} onRow={r => go('node', r.unique_id)} sort={{ key: 'st', dir: -1 }} maxH={600}
        cols={[
          { key: 'name', label: 'Name', render: r => <b>{r.name}</b> },
          { key: 'type', label: 'Type' },
          { key: 'layer', label: 'Layer', render: r => <Layer l={r.layer} />, sort: r => LAYER_ORDER.indexOf(LAYER_GROUP(r.layer)) },
          { key: 'st', label: 'Status', render: r => <StatusPill s={status[r.unique_id].status} />, sort: r => ({ failed: 4, stale: 3, warn: 2, notrun: 1, ok: 0 })[status[r.unique_id].status] },
          { key: 'tests', label: 'Tests passing', num: true, render: r => { const t = TESTS_OF[r.unique_id] || []; const f = t.filter(x => isBad(latestTest(x))).length; return t.length ? <span className={f ? 't-fail' : ''}>{t.length - f} / {t.length}</span> : <span className="muted">—</span>; }, sort: r => (TESTS_OF[r.unique_id] || []).length },
          { key: 'dur', label: 'Latest build', num: true, desc: true, render: r => fmtSec(EX[r.unique_id] && EX[r.unique_id][LAST] ? EX[r.unique_id][LAST].execution_time_s : null), sort: r => (EX[r.unique_id] && EX[r.unique_id][LAST] ? EX[r.unique_id][LAST].execution_time_s : null) },
          { key: 'cr', label: 'Credits, 30 runs', num: true, desc: true, render: r => EX[r.unique_id] ? sum(EX[r.unique_id].map(x => (x ? x.credits : 0))).toFixed(4) : '—', sort: r => (EX[r.unique_id] ? sum(EX[r.unique_id].map(x => (x ? x.credits : 0))) : null) },
          { key: 'sp', label: 'Build time trend', ns: true, render: r => EX[r.unique_id] ? <Spark data={EX[r.unique_id].map(x => (x ? x.execution_time_s : null))} w={100} h={20} tone="muted" /> : null },
        ]} />
    </Card>
  </div>;
}
