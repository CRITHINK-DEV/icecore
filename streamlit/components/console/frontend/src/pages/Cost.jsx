import React, { useState, useMemo } from 'react';
import { fmtRun, fmtCr, pct, sum } from '../lib/format.js';
import { NODE, ASSETS, EX, EX_RUN, LAYER_GROUP, LAYER_LABEL, LAYER_ORDER } from '../lib/model.js';
import { useApp, pal, layerColor, base, ax, EChart } from '../lib/charts.jsx';
import { Layer, Spark, Kpi, Seg, Card, PageHead, DataTable, WINDOWS, runsInWindow, useSticky, sendFilters } from '../components/ui.jsx';

/* ---------------- Cost ---------------- */
export function Cost() {
  const { go, theme } = useApp();
  const [win, setWin] = useSticky('win', '15'); const [lay, setLay] = useSticky('cost.layer', 'all');
  const wr = runsInWindow(win); const wi = wr.map(r => r.idx);
  const attributed = wr.filter(r => r.credits != null);
  const half = Math.floor(attributed.length / 2);
  const firstHalf = sum(attributed.slice(0, half).map(r => r.credits)), secondHalf = sum(attributed.slice(half).map(r => r.credits));
  const rows = ASSETS.filter(n => EX[n.unique_id] && (lay === 'all' || LAYER_GROUP(n.layer) === lay)).map(n => {
    const a = wi.map(i => (EX[n.unique_id][i] ? EX[n.unique_id][i].credits : null)); const known = a.filter(v => v != null);
    const recent = known.slice(-4), before = known.slice(0, -4);
    const rAvg = recent.length ? sum(recent) / recent.length : null, bAvg = before.length ? sum(before) / before.length : null;
    return { ...n, a, total: sum(known), avg: known.length ? sum(known) / known.length : null, change: rAvg != null && bAvg ? rAvg / bAvg : null };
  });
  const grand = sum(rows.map(r => r.total));
  const top = [...rows].filter(r => r.change != null).sort((a, b) => b.change - a.change)[0];
  const opt = useMemo(() => {
    const p = pal(); const groups = LAYER_ORDER.filter(g => lay === 'all' || g === lay);
    return base(p, {
      grid: { left: 64, right: 20, top: 34, bottom: 54 },
      legend: { top: 0, right: 0, textStyle: { color: p.ink2, fontSize: 11 } },
      tooltip: { trigger: 'axis', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 }, valueFormatter: v => (v == null ? 'not yet attributed' : v.toFixed(4)) },
      xAxis: ax(p, 'Run start time', { type: 'category', data: wr.map(r => fmtRun(r.t)), splitLine: { show: false }, nameGap: 34 }),
      yAxis: ax(p, 'Credits per run', { type: 'value', nameGap: 46, axisLabel: { color: p.ink3, fontSize: 10.5, formatter: v => v.toFixed(3) } }),
      series: groups.map(g => ({ name: LAYER_LABEL[g], type: 'bar', stack: 'c', barMaxWidth: 18, color: layerColor(p, g),
        data: wr.map(r => { const xs = (EX_RUN[r.invocation_id] || []).filter(e => LAYER_GROUP(NODE[e.unique_id].layer) === g); if (r.credits == null && xs.every(e => e.credits == null)) return null; return +sum(xs.map(e => e.credits)).toFixed(5); }) })),
    });
  }, [win, lay, theme]); // eslint-disable-line
  return <div className="page">
    <PageHead title="Cost" sub="Warehouse credits per run and per model. Recent runs may not be attributed yet; they show as gaps." />
    <div className="kpis">
      <Kpi label="Credits" value={sum(wr.map(r => r.credits)).toFixed(3)} sub={win === '7' ? 'last 7 days' : 'last 15 days'} spark={wr.map(r => r.credits)} />
      <Kpi label="Average per run" value={attributed.length ? (sum(attributed.map(r => r.credits)) / attributed.length).toFixed(4) : '—'} sub={`${attributed.length} attributed runs`} />
      <Kpi label="Second half vs first half" value={firstHalf ? `${((secondHalf / firstHalf - 1) * 100).toFixed(1)}%` : '—'} tone={secondHalf > firstHalf * 1.05 ? 'warn' : undefined} sub="credits, attributed runs of the window" />
      <Kpi label="Biggest increase" value={top ? `${top.change.toFixed(1)}×` : '—'} tone={top && top.change > 1.5 ? 'fail' : undefined} sub={top ? `${top.name}, last 4 runs vs before` : ''} spark={top ? top.a : null} sparkTone="fail" />
      <Kpi label="Runs not yet attributed" value={wr.filter(r => r.credits == null).length} sub="credits arrive later" />
    </div>
    <div className="fbar">
      <Seg label="Window" options={WINDOWS} value={win} onChange={v => { setWin(v); sendFilters(v, lay); }} />
      <Seg label="Layer" options={[['all', 'All'], ...LAYER_ORDER.map(l => [l, LAYER_LABEL[l]])]} value={lay} onChange={v => { setLay(v); sendFilters(win, v); }} />
    </div>
    <Card title="Credits per run, by layer" sub="Stacked by layer. Runs without bars are not attributed yet.">
      <EChart option={opt} height={280} onClick={e => { const r = wr[e.dataIndex]; if (r) go('runs', null, { run: r.invocation_id }); }} />
    </Card>
    <Card title="Credits per model" sub="Largest increase first: average of the last 4 attributed runs against the runs before. Click a row to open the model." flush>
      <DataTable rows={rows} rowKey={r => r.unique_id} onRow={r => go('node', r.unique_id)} sort={{ key: 'change', dir: -1 }} maxH={520}
        cols={[
          { key: 'name', label: 'Model', render: r => <b>{r.name}</b> },
          { key: 'layer', label: 'Layer', render: r => <Layer l={r.layer} /> },
          { key: 'total', label: 'Credits', num: true, desc: true, render: r => r.total.toFixed(4) },
          { key: 'share', label: 'Share', num: true, desc: true, sort: r => r.total, render: r => grand ? pct(r.total / grand * 100) : '—' },
          { key: 'avg', label: 'Per run', num: true, desc: true, render: r => fmtCr(r.avg) },
          { key: 'change', label: 'Change', num: true, desc: true, render: r => r.change == null ? '—' : <span className={r.change > 1.5 ? 't-fail' : r.change > 1.15 ? 't-warn' : ''}><b>{r.change.toFixed(2)}×</b></span> },
          { key: 'sp', label: 'Credits per run', ns: true, render: r => <Spark data={r.a} w={110} h={22} tone={r.change > 1.5 ? 'fail' : 'muted'} /> },
        ]} />
    </Card>
  </div>;
}
