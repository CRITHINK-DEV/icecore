import React, { useState, useMemo } from 'react';
import { esc, fmtTs, fmtSec, fmtCr, sum, median } from '../lib/format.js';
import { RUNS, LAST, RUN_IDX, NODE, ASSETS, FAILS, EX, EX_RUN, LAYER_GROUP, LAYER_LABEL, LAYER_ORDER } from '../lib/model.js';
import { useApp, pal, layerColor, base, ax, EChart } from '../lib/charts.jsx';
import { RunPill, Layer, Spark, Kpi, Delta, Seg, Card, Tabs, PageHead, DataTable, WINDOWS, runsInWindow, useSticky, sendFilters } from '../components/ui.jsx';

/* ---------------- Runs ---------------- */
export function Gantt({ run, filterLayer }) {
  const { theme, go } = useApp();
  const ex = (EX_RUN[run.invocation_id] || []).filter(e => filterLayer === 'all' || LAYER_GROUP(NODE[e.unique_id].layer) === filterLayer);
  const opt = useMemo(() => {
    const p = pal();
    return base(p, {
      grid: { left: 180, right: 30, top: 10, bottom: 46 },
      tooltip: { trigger: 'item', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 }, formatter: e => { const x = ex[e.dataIndex]; const n = NODE[x.unique_id]; return `<b>${esc(n.name)}</b><br/>${esc(LAYER_LABEL[LAYER_GROUP(n.layer)])}, ${esc(n.materialization || n.type)}<br/>Starts at +${esc(((x.t - run.t) / 1000).toFixed(1))} s<br/>Took ${esc(fmtSec(x.execution_time_s))}<br/>Credits ${esc(fmtCr(x.credits))}${esc(x.status !== 'success' ? '<br/><b style="color:' + p.fail + '">Error</b>' : '')}`; } },
      xAxis: ax(p, 'Seconds since run start', { type: 'value', min: 0 }),
      yAxis: ax(p, '', { type: 'category', inverse: true, data: ex.map(x => NODE[x.unique_id].name), splitLine: { show: false }, axisLabel: { color: p.ink2, fontSize: 10.5 } }),
      series: [
        { type: 'bar', stack: 'g', silent: true, itemStyle: { color: 'transparent' }, data: ex.map(x => (x.t - run.t) / 1000), tooltip: { show: false } },
        { type: 'bar', stack: 'g', barCategoryGap: '28%', data: ex.map(x => ({ value: x.execution_time_s, uid: x.unique_id, itemStyle: { color: x.status === 'success' ? layerColor(p, LAYER_GROUP(NODE[x.unique_id].layer)) : p.fail, borderRadius: 2, borderColor: x.status === 'success' ? 'transparent' : p.fail, borderWidth: x.status === 'success' ? 0 : 2 } })) },
      ],
    });
  }, [run.invocation_id, filterLayer, theme]); // eslint-disable-line
  if (!ex.length) return <div className="empty-state"><b>No node ran in this layer</b>Pick another layer.</div>;
  return <EChart option={opt} height={60 + ex.length * 17} onClick={e => e.data && e.data.uid && go('node', e.data.uid)} />;
}

export function Runs() {
  const { route, go, nav } = useApp();
  const [win, setWin] = useSticky('win', '15'); const [st, setSt] = useState('all'); const [lay, setLay] = useState('all'); const [tab, setTab] = useState('runs');
  if (!RUNS.length) return <div className="page"><PageHead title="Runs" sub="Every build in the window, with its node timeline. Pick a run in the table to draw its timeline." /><div className="empty-state"><b>No run recorded yet</b>Runs appear here once icecore has recorded a dbt invocation.</div></div>;
  const runId = route.q.run || RUNS[LAST].invocation_id; const run = RUNS[RUN_IDX[runId]] || RUNS[LAST];
  const wr = runsInWindow(win); const fr = wr.filter(r => st === 'all' || r.status === st);
  const lastEx = (EX_RUN[RUNS[LAST].invocation_id] || []).slice().sort((a, b) => b.execution_time_s - a.execution_time_s);
  const slow = ASSETS.filter(n => EX[n.unique_id]).map(n => { const a = EX[n.unique_id].map(x => (x ? x.execution_time_s : null)); const recent = a.slice(-4).filter(v => v != null); const before = a.slice(0, -4).filter(v => v != null);
    const rm = median(recent), bm = median(before); return { ...n, a, latest: a[LAST], med: median(a), change: rm != null && bm ? rm / bm : null, credits: sum(EX[n.unique_id].map(x => (x ? x.credits : 0))) }; });
  return <div className="page">
    <PageHead title="Runs" sub="Every build in the window, with its node timeline. Pick a run in the table to draw its timeline." />
    <div className="kpis">
      <Kpi label="Runs" value={wr.length} sub={`${win === '7' ? 'last 7 days' : 'last 15 days'}, target ${RUNS[LAST].target}`} />
      <Kpi label="Runs ended in error" value={wr.filter(r => r.status !== 'success').length} tone={wr.some(r => r.status !== 'success') ? 'fail' : 'ok'} spark={wr.map(r => r.nodes_failed)} sparkType="bar" sparkTone="fail" sub="bars: failed nodes per run" />
      <Kpi label="Median duration" value={fmtSec(median(wr.map(r => r.duration_s)))} spark={wr.map(r => r.duration_s)} sparkTone="muted" delta={<Delta v={RUNS[LAST].duration_s - median(wr.map(r => r.duration_s))} goodWhenDown fmt={v => v.toFixed(1) + ' s'} />} sub=" latest vs median" />
      <Kpi label="Credits" value={sum(wr.map(r => r.credits)).toFixed(3)} spark={wr.map(r => r.credits)} sub={`${wr.filter(r => r.credits == null).length} runs not yet attributed`} />
      <Kpi label="Slowest model, latest run" value={lastEx[0] ? fmtSec(lastEx[0].execution_time_s) : '—'} sub={lastEx[0] ? NODE[lastEx[0].unique_id].name : ''} />
    </div>
    <div className="fbar">
      <Seg label="Window" options={WINDOWS} value={win} onChange={v => { setWin(v); sendFilters(v, lay); }} />
      <Seg label="Status" options={[['all', 'All'], ['success', 'Success'], ['error', 'Error']]} value={st} onChange={setSt} />
      <Seg label="Timeline layer" options={[['all', 'All'], ...LAYER_ORDER.map(l => [l, LAYER_LABEL[l]])]} value={lay} onChange={setLay} />
    </div>
    <Card title={`Node timeline, run of ${fmtTs(run.t)}`} sub={`Each bar is one node, coloured by layer; red outline means the build failed. ${run.nodes_total} nodes in the run including tests, ${fmtSec(run.duration_s)} total. Click a bar to open the node.`}
      right={<div className="hstack"><RunPill s={run.status} /><span className="muted mono">{run.invocation_id}</span></div>}>
      <div className="hstack" style={{ marginBottom: 4, fontSize: 11.5 }}>{LAYER_ORDER.map(l => <span key={l} className="lyr"><i style={{ background: `var(--${l})` }} />{LAYER_LABEL[l]}</span>)}</div>
      <Gantt run={run} filterLayer={lay} />
    </Card>
    <Tabs value={tab} onChange={setTab} tabs={[{ key: 'runs', label: 'Run list', count: fr.length }, { key: 'slow', label: 'Slowest models', count: slow.length }]} />
    {tab === 'runs' ? <Card title="Run list" sub="Newest first. Click a row to draw its timeline above." flush>
      <DataTable rows={fr} rowKey={r => r.invocation_id} selKey={run.invocation_id} onRow={r => { nav('runs', null, { run: r.invocation_id }); document.querySelector('.main').scrollTo({ top: 0, behavior: 'smooth' }); }} sort={{ key: 't', dir: -1 }}
        cols={[
          { key: 't', label: 'Started', render: r => fmtTs(r.t) },
          { key: 'status', label: 'Status', render: r => <RunPill s={r.status} /> },
          { key: 'command', label: 'Command' },
          { key: 'duration_s', label: 'Duration', num: true, desc: true, render: r => fmtSec(r.duration_s) },
          { key: 'credits', label: 'Credits', num: true, desc: true, render: r => r.credits == null ? <span className="muted">Not yet attributed</span> : fmtCr(r.credits) },
          { key: 'nodes_failed', label: 'Failed nodes', num: true, desc: true, render: r => r.nodes_failed ? <b className="t-fail">{r.nodes_failed}</b> : <span className="muted">0</span> },
          { key: 'ft', label: 'Failing tests', num: true, desc: true, render: r => FAILS[r.idx] ? <b className="t-fail">{FAILS[r.idx]}</b> : <span className="muted">0</span>, sort: r => FAILS[r.idx] },
          { key: 'nodes_total', label: 'Nodes', num: true },
          { key: 'invocation_id', label: 'Run id', render: r => <span className="mono muted">{r.invocation_id}</span> },
        ]} />
    </Card> : <Card title="Slowest models" sub="Median build time over the last 30 runs; change compares the last 4 runs to the runs before. Click a row to open the model." flush>
      <DataTable rows={slow} rowKey={r => r.unique_id} onRow={r => go('node', r.unique_id)} sort={{ key: 'med', dir: -1 }} maxH={520}
        cols={[
          { key: 'name', label: 'Model', render: r => <b>{r.name}</b> },
          { key: 'layer', label: 'Layer', render: r => <Layer l={r.layer} /> },
          { key: 'materialization', label: 'Materialization' },
          { key: 'med', label: 'Median', num: true, desc: true, render: r => fmtSec(r.med) },
          { key: 'latest', label: 'Latest', num: true, desc: true, render: r => fmtSec(r.latest) },
          { key: 'change', label: 'Change', num: true, desc: true, render: r => r.change == null ? '—' : <span className={r.change > 1.5 ? 't-fail' : r.change > 1.15 ? 't-warn' : ''}>{r.change.toFixed(2)}×</span> },
          { key: 'sp', label: 'Build time, 30 runs', ns: true, render: r => <Spark data={r.a} w={110} h={22} tone={r.change > 1.5 ? 'fail' : 'muted'} /> },
        ]} />
    </Card>}
  </div>;
}
