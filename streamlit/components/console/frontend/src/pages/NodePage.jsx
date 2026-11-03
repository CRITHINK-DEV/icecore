import React, { useState, useMemo, useEffect } from 'react';
import { esc, fmtTs, fmtRun, fmtSec, fmtCr, sum, median, humanMetric, rcKind } from '../lib/format.js';
import { RUNS, LAST, NODE, TESTS_OF, TR, TRF, TRC, EX, SERIES, SERIES_LIST, SLA_BY, FRESH, SCHEMA, testTarget, isBad, latestIdx, latestTest, KIND_LABEL, STATUS_LABEL, CHANGE_LABEL, incNode, incAsset } from '../lib/model.js';
import { useApp, pal, base, ax, EChart } from '../lib/charts.jsx';
import { Pill, StatusPill, RunPill, TestPill, EngineBadge, Layer, RunStrip, Kpi, Card, Tabs, PageHead, DataTable } from '../components/ui.jsx';
import { BandChart, ShadowPanel } from './Tests.jsx';
import { hood, around, LINEAGE_MAX, LineageCanvas } from './Lineage.jsx';

/* ---------------- Node page ---------------- */
export function NodePage({ id }) {
  const { status, incidents, go, theme } = useApp();
  const n = NODE[id];
  const [tab, setTab] = useState('history');
  const [ser, setSer] = useState(null);
  useEffect(() => { setTab('history'); setSer(null); }, [id]);
  const histOpt = useMemo(() => {
    if (!n) return null; const p = pal();
    if (n.type === 'test') {
      const a = TR[id] || [], f = TRF[id] || [];
      return base(p, { grid: { left: 60, right: 20, top: 24, bottom: 54 },
        tooltip: { trigger: 'axis', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 }, formatter: x => { const i = x[0].dataIndex; return `<b>${esc(fmtRun(RUNS[i].t))}</b><br/>${esc(a[i] || 'not run')}${f[i] ? `, ${esc(f[i])} failing rows` : ''}`; } },
        xAxis: ax(p, 'Run start time', { type: 'category', data: RUNS.map(r => fmtRun(r.t)), splitLine: { show: false }, nameGap: 34 }),
        yAxis: ax(p, 'Failing rows (count)', { type: 'value', minInterval: 1, nameGap: 40 }),
        series: [{ type: 'bar', barMaxWidth: 14, data: RUNS.map((r, i) => ({ value: a[i] == null ? null : (f[i] || 0), itemStyle: { color: isBad(a[i]) ? p.fail : p.ok, borderRadius: [2, 2, 0, 0] } })) }] });
    }
    const ex = EX[id] || [];
    return base(p, { grid: { left: 60, right: 60, top: 30, bottom: 54 },
      legend: { top: 0, right: 0, data: ['Build time', 'Credits'], textStyle: { color: p.ink2, fontSize: 11 } },
      xAxis: ax(p, 'Run start time', { type: 'category', data: RUNS.map(r => fmtRun(r.t)), splitLine: { show: false }, nameGap: 34 }),
      yAxis: [ax(p, 'Build time (seconds)', { type: 'value', nameGap: 40 }), ax(p, 'Credits', { type: 'value', position: 'right', nameGap: 46, splitLine: { show: false }, axisLabel: { color: p.ink3, fontSize: 10.5, formatter: v => v.toFixed(3) } })],
      series: [
        { name: 'Build time', type: 'bar', barMaxWidth: 14, color: p.bar, data: ex.map(x => (x ? { value: x.execution_time_s, itemStyle: { color: x.status === 'success' ? p.bar : p.fail, borderRadius: [2, 2, 0, 0] } } : null)) },
        { name: 'Credits', type: 'line', yAxisIndex: 1, color: p.accent, symbolSize: 4, connectNulls: false, data: ex.map(x => (x ? x.credits : null)) },
      ] });
  }, [id, theme]); // eslint-disable-line
  if (!n) return <div className="page"><PageHead title="Node not found" sub="This node is not in the project graph. Pick one from Models & sources." /></div>;
  const isTest = n.type === 'test';
  const target = isTest ? testTarget(n) : id;
  const s = status[target] || { status: 'ok', reasons: [] };
  const tids = TESTS_OF[id] || [];
  const failing = tids.filter(t => isBad(latestTest(t)));
  const incs = incidents.filter(i => i.state !== 'resolved' && (incNode(i) === id || incAsset(i) === id));
  const sers = SERIES_LIST.filter(x => x.uid === id); const curSer = SERIES[ser] || sers[0];
  const schema = SCHEMA.filter(c => c.relation === n.name);
  const ex = EX[id] || []; const lastEx = ex[LAST];
  const tabs = isTest ? [{ key: 'history', label: 'Result history' }, { key: 'lineage', label: 'Lineage' }]
    : [{ key: 'history', label: 'Build history' }, { key: 'tests', label: 'Tests', count: tids.length }, { key: 'metrics', label: 'Metrics', count: sers.length }, { key: 'schema', label: 'Schema history', count: schema.length }, { key: 'lineage', label: 'Lineage' }];
  const tst = isTest ? latestTest(id) : null; const tarr = isTest ? (TR[id] || []) : [];
  return <div className="page">
    <PageHead crumbs={[{ label: isTest ? 'Tests & anomalies' : 'Models & sources', to: isTest ? 'tests' : 'models' }, { label: n.name }]} title={n.name}
      sub={<span className="mono">{n.unique_id}</span>}
      right={<div className="hstack">{isTest ? <TestPill s={tst} /> : <StatusPill s={s.status} />}<Layer l={isTest && target ? NODE[target].layer : n.layer} /><span className="badge">{n.type}{n.materialization && n.materialization !== n.type ? `, ${n.materialization}` : ''}</span>
        <button className="btn" onClick={() => go('lineage', null, { focus: target || id, mode: (isTest ? isBad(tst) : s.status !== 'ok') ? 'root' : 'both' })}>Show in lineage</button></div>} />
    {!isTest && s.reasons.length > 0 && <div className="reasons" style={{ margin: '-6px 0 14px' }}>{s.reasons.map((r, i) => <span key={i}>{r}</span>)}</div>}
    {isTest ? <div className="kpis">
      <Kpi label="Latest result" value={tst ? (isBad(tst) ? 'Fail' : tst === 'pass' ? 'Pass' : tst) : '—'} tone={isBad(tst) ? 'fail' : 'ok'} sub={latestIdx(id) >= 0 ? fmtTs(RUNS[latestIdx(id)].t) : ''} />
      <Kpi label="Failing rows" value={latestIdx(id) >= 0 ? TRF[id][latestIdx(id)] : '—'} tone={isBad(tst) ? 'fail' : undefined} spark={TRF[id] || []} sparkType="bar" sparkTone="fail" />
      <Kpi label="Failing since" value={(() => { let i = tarr.length - 1; if (!isBad(tarr[i])) return '—'; while (i > 0 && isBad(tarr[i - 1])) i--; return fmtTs(RUNS[i].t); })()} sub={`${tarr.filter(isBad).length} failures in ${tarr.filter(x => x != null).length} runs`} />
      {isBad(tst) && TRC[id] && NODE[TRC[id].id] ? <Kpi label="Traced root cause" value={<button className="linkish" style={{ fontSize: 15, fontWeight: 650 }} onClick={() => go('node', TRC[id].id)}>{NODE[TRC[id].id].name}</button>} tone="stale" sub={rcKind(TRC[id].kind)} />
        : <Kpi label="Test type" value={n.test_name || 'singular'} sub={n.column ? `column ${n.column}` : 'no column'} />}
      <Kpi label="Model" value={target ? NODE[target].name : '—'} sub={target ? STATUS_LABEL[s.status] : ''} />
    </div> : <div className="kpis">
      <Kpi label="Latest build" value={lastEx ? fmtSec(lastEx.execution_time_s) : n.type === 'source' ? 'Source' : 'Not run'} tone={lastEx && lastEx.status !== 'success' ? 'fail' : undefined} spark={ex.map(x => (x ? x.execution_time_s : null))} sparkTone="muted" sub={lastEx ? `median ${fmtSec(median(ex.map(x => (x ? x.execution_time_s : null))))}` : FRESH[id] ? `data ${FRESH[id].freshness_lag_min} min old` : ''} />
      <Kpi label="Credits per run" value={lastEx ? (lastEx.credits == null ? 'Pending' : fmtCr(lastEx.credits)) : '—'} spark={ex.map(x => (x ? x.credits : null))} sub={ex.length ? `${sum(ex.map(x => (x ? x.credits : 0))).toFixed(4)} over 30 runs` : ''} />
      <Kpi label="Tests passing" value={tids.length ? `${tids.length - failing.length} of ${tids.length}` : 'None'} tone={failing.length ? 'fail' : tids.length ? 'ok' : undefined} />
      <Kpi label="Open incidents" value={incs.length} tone={incs.length ? 'warn' : 'ok'} sub={incs.map(i => KIND_LABEL[i.kind]).join(', ')} />
      {SLA_BY[id] && <Kpi label="Schedule" value={SLA_BY[id].late ? 'Late' : 'On time'} tone={SLA_BY[id].late ? 'stale' : 'ok'} sub={`every ${SLA_BY[id].expected_every}, last ${fmtTs(SLA_BY[id].last)}`} />}
    </div>}
    <Tabs value={tab} onChange={setTab} tabs={tabs} />
    {tab === 'history' && <>
      <Card title={isTest ? 'Failing rows per run' : 'Build time and credits per run'} sub={isTest ? 'Green bars passed; red bars failed, height is the number of failing rows.' : 'Red bars are failed builds. Credits of the newest runs may not be attributed yet.'}>
        {(isTest || ex.length) ? <EChart option={histOpt} height={240} /> : <div className="empty-state"><b>No builds recorded</b>{n.type === 'source' ? 'Sources are not built; see Schedules & freshness for their data age.' : 'This node did not run in the window.'}</div>}
      </Card>
      {isTest ? <Card title="Results" flush><DataTable rows={RUNS.filter((r, i) => tarr[i] != null)} rowKey={r => r.invocation_id} onRow={r => go('runs', null, { run: r.invocation_id })} sort={{ key: 't', dir: -1 }}
        cols={[{ key: 't', label: 'Run', render: r => fmtTs(r.t) }, { key: 's', label: 'Result', render: r => <TestPill s={tarr[r.idx]} />, sort: r => tarr[r.idx] }, { key: 'f', label: 'Failing rows', num: true, render: r => TRF[id][r.idx] || 0, sort: r => TRF[id][r.idx] }, { key: 'inv', label: 'Run id', render: r => <span className="mono muted">{r.invocation_id}</span> }]} /></Card>
        : ex.length > 0 && <Card title="Builds" sub="Click a row to open the run timeline." flush><DataTable rows={ex.filter(Boolean)} rowKey={r => r.invocation_id} onRow={r => go('runs', null, { run: r.invocation_id })} sort={{ key: 't', dir: -1 }} maxH={380}
          cols={[{ key: 't', label: 'Run', render: r => fmtTs(RUNS[r.i].t), sort: r => r.t }, { key: 'status', label: 'Status', render: r => <RunPill s={r.status} /> }, { key: 'execution_time_s', label: 'Build time', num: true, desc: true, render: r => fmtSec(r.execution_time_s) }, { key: 'credits', label: 'Credits', num: true, desc: true, render: r => r.credits == null ? <span className="muted">Not yet attributed</span> : fmtCr(r.credits) }]} /></Card>}
    </>}
    {tab === 'tests' && <Card title="Tests on this node" sub="Click a row to open the test." flush>
      <DataTable rows={tids.map(t => NODE[t])} rowKey={r => r.unique_id} onRow={r => go('node', r.unique_id)} sort={{ key: 's', dir: 1 }} empty="No test is attached to this node. Add tests to cover it."
        cols={[{ key: 's', label: 'Status', render: r => <TestPill s={latestTest(r.unique_id)} />, sort: r => (isBad(latestTest(r.unique_id)) ? 0 : 1) }, { key: 'name', label: 'Test' }, { key: 'test_name', label: 'Type', render: r => r.test_name || 'singular' }, { key: 'column', label: 'Column', render: r => r.column || <span className="muted">—</span> }, { key: 'strip', label: 'Last 30 runs', ns: true, render: r => <RunStrip arr={TR[r.unique_id] || []} /> }]} />
    </Card>}
    {tab === 'metrics' && (sers.length ? <div className="grid2">
      <Card title={`${humanMetric(curSer.metric)} of ${n.name}`} sub="Observed against expected and its band. A red dot is a point outside the band." right={<div className="hstack">{sers.length > 1 && <select className="inp" value={curSer.key} onChange={e => setSer(e.target.value)}>{sers.map(x => <option key={x.key} value={x.key}>{humanMetric(x.metric)}</option>)}</select>}{Object.keys(curSer.pts).map(e => <EngineBadge key={e} e={e} />)}</div>}>
        <BandChart series={curSer} engine={Object.keys(curSer.pts)[0]} />
      </Card>
      <Card title="Shadow scoring" sub="Native and Cortex side by side."><ShadowPanel series={curSer} /></Card>
    </div> : <Card title="Metrics"><div className="empty-state"><b>No metric is monitored on {n.name}</b>{incs.some(i => i.kind === 'anomaly') ? 'An anomaly alert names this node, but no metric series was recorded for it in this window.' : 'Monitored metrics such as row count appear here with their expected band.'}</div></Card>)}
    {tab === 'schema' && <Card title="Schema history" flush>
      <DataTable rows={schema} rowKey={r => r.column + r.t} empty="No schema change detected on this table in the window."
        cols={[{ key: 't', label: 'Detected', render: r => fmtTs(r.t) }, { key: 'column', label: 'Column', render: r => <span className="mono">{r.column}</span> }, { key: 'change', label: 'Change', render: r => <Pill tone={r.change === 'added' ? 'ok' : r.change === 'removed' ? 'fail' : 'warn'}>{CHANGE_LABEL[r.change]}</Pill> }, { key: 'd', label: 'Details', ns: true, render: r => r.change === 'type_changed' ? `${r.from} to ${r.to}` : r.type }]} />
    </Card>}
    {tab === 'lineage' && <Card title="Lineage neighbourhood" sub="Two steps up and down from this node. Click a node to open it." flush right={<button className="btn" onClick={() => go('lineage', null, { focus: target || id, mode: 'both' })}>Open full lineage</button>}>
      {target ? <div className="lin-wrap mini"><LineageCanvas compact ids={(h => (h.length > LINEAGE_MAX ? around(target, h) : h))(hood(target, 2))} focus={target} mode="both" onSelect={x => x && go('node', x)} /></div> : <div className="empty-state">This test has no model in the graph.</div>}
    </Card>}
  </div>;
}
