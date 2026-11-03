import React, { useState, useMemo } from 'react';
import { esc, fmtDay, fmtRun, fmtNum, pct, sum, humanMetric, metricUnit, rcKind } from '../lib/format.js';
import { D, RUNS, NR, LAST, NODE, TESTS, TR, TRF, TRC, FAILS, TESTED, SERIES, SERIES_LIST, CAP, testTarget, isBad, latestIdx, seriesLatest, seriesAnomalous } from '../lib/model.js';
import { useApp, pal, base, ax, EChart } from '../lib/charts.jsx';
import { Pill, TestPill, EngineBadge, Spark, RunStrip, Kpi, Seg, Card, Tabs, PageHead, DataTable, NodeLink } from '../components/ui.jsx';

/* ---------------- Tests & anomalies ---------------- */
export function BandChart({ series, engine, height = 300 }) {
  const { theme } = useApp();
  const pts = (series && series.pts[engine]) || [];
  const opt = useMemo(() => {
    const p = pal();
    return base(p, {
      grid: { left: 70, right: 20, top: 30, bottom: 50 },
      legend: { top: 0, right: 0, data: ['Observed', 'Expected', 'Expected band', 'Anomaly'], textStyle: { color: p.ink2, fontSize: 11 } },
      tooltip: { trigger: 'axis', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 },
        formatter: a => { const x = pts[a[0].dataIndex]; return `<b>${esc(fmtDay(x.t))}</b><br/>Observed ${esc(fmtNum(x.value))}<br/>Expected ${esc(fmtNum(x.expected))}<br/>Band ${esc(fmtNum(x.lower))} to ${esc(fmtNum(x.upper))}<br/>${x.anomaly ? '<b style="color:' + p.fail + '">Anomaly</b>' : 'Inside the band'}${x.score != null ? '<br/>Score ' + esc(x.score) : ''}`; } },
      xAxis: ax(p, 'Observed at (day)', { type: 'category', boundaryGap: false, data: pts.map(x => fmtDay(x.t)), splitLine: { show: false } }),
      yAxis: ax(p, `${humanMetric(series ? series.metric : '')} (${metricUnit(series ? series.metric : '')})`, { type: 'value', scale: true, nameGap: 52, axisLabel: { color: p.ink3, fontSize: 10.5, formatter: v => fmtNum(v) } }),
      series: [
        { name: 'Lower', type: 'line', stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, data: pts.map(x => x.lower), tooltip: { show: false } },
        { name: 'Expected band', type: 'line', stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: p.band, opacity: 1 }, color: p.band, data: pts.map(x => x.upper - x.lower) },
        { name: 'Expected', type: 'line', symbol: 'none', color: p.ink3, lineStyle: { type: 'dashed', width: 1 }, data: pts.map(x => x.expected) },
        { name: 'Observed', type: 'line', symbolSize: 4, color: p.accent, lineStyle: { width: 2 }, data: pts.map(x => x.value) },
        { name: 'Anomaly', type: 'scatter', symbolSize: 11, color: p.fail, data: pts.map(x => (x.anomaly ? x.value : null)) },
      ],
    });
  }, [series && series.key, engine, theme]); // eslint-disable-line
  if (!pts.length) return <div className="empty-state"><b>No points for this engine</b>Pick another engine for this metric.</div>;
  return <EChart option={opt} height={height} />;
}

export function ShadowPanel({ series }) {
  const engines = series ? Object.keys(series.pts) : [];
  if (!series) return null;
  if (engines.length < 2) {
    return <div className="empty-state" style={{ textAlign: 'left', padding: '6px 2px' }}>
      <b>Only the {engines[0] === 'cortex' ? 'Cortex' : 'native'} engine scored this metric</b>
      Shadow scoring shows native and Cortex side by side when both score the same point.
      Cortex is {CAP.cortex ? 'available on this account' : 'not available on this account'}; no Cortex score has been recorded for {series.relation} yet.
      <div style={{ marginTop: 10 }} className="hstack"><EngineBadge e="native" /> {(series.pts.native || []).length} points <span className="badge cortex">Cortex</span> {(series.pts.cortex || []).length} points</div>
    </div>;
  }
  const nat = series.pts.native || [], cx = series.pts.cortex || []; const byT = {}; cx.forEach(x => { byT[x.t] = x; });
  const rows = nat.filter(x => byT[x.t]).map(x => ({ t: x.t, v: x.value, n: x, c: byT[x.t] })).reverse();
  const agree = rows.filter(r => r.n.anomaly === r.c.anomaly).length;
  return <div>
    <div className="hstack" style={{ marginBottom: 8 }}><span>Engines agree on <b>{agree} of {rows.length}</b> shared points{rows.filter(r => r.n.anomaly || r.c.anomaly).length ? `; ${rows.filter(r => r.n.anomaly && r.c.anomaly).length} flagged by both, ${rows.filter(r => r.n.anomaly !== r.c.anomaly).length} by one only` : ''}. Newest first.</span></div>
    <DataTable rows={rows} rowKey={r => r.t} maxH={240} cols={[
      { key: 't', label: 'Day', render: r => fmtDay(r.t) },
      { key: 'v', label: 'Observed', num: true, render: r => fmtNum(r.v) },
      { key: 'n', label: 'Native band', render: r => `${fmtNum(r.n.lower)} to ${fmtNum(r.n.upper)}`, ns: true },
      { key: 'nf', label: 'Native', render: r => r.n.anomaly ? <Pill tone="fail">Anomaly</Pill> : <Pill tone="ok">Normal</Pill>, ns: true },
      { key: 'ns', label: 'Native score', num: true, desc: true, render: r => r.n.score == null ? '—' : r.n.score.toFixed(2), sort: r => r.n.score },
      { key: 'c', label: 'Cortex band', render: r => `${fmtNum(r.c.lower)} to ${fmtNum(r.c.upper)}`, ns: true },
      { key: 'cf', label: 'Cortex', render: r => r.c.anomaly ? <Pill tone="fail">Anomaly</Pill> : <Pill tone="ok">Normal</Pill>, ns: true },
      { key: 'cs', label: 'Cortex score', num: true, desc: true, render: r => r.c.score == null ? '—' : r.c.score.toFixed(2), sort: r => r.c.score },
    ]} />
  </div>;
}

export function Tests() {
  const { go, route, nav, theme } = useApp();
  const tab = route.q.tab || 'results';
  const [st, setSt] = useState('all'); const [ty, setTy] = useState('all'); const [q, setQ] = useState(''); const [eng, setEng] = useState('all');
  const [sel, setSel] = useState(route.q.series || ((SERIES_LIST.find(x => seriesAnomalous(x) && Object.keys(x.pts).length > 1) || SERIES_LIST.find(seriesAnomalous) || SERIES_LIST[0] || {}).key));
  const [selEng, setSelEng] = useState('native');
  const types = [...new Set(TESTS.map(t => t.test_name || 'singular'))].sort();
  const rows = useMemo(() => TESTS.map(t => {
    const a = TR[t.unique_id] || Array(NR).fill(null); const ran = a.filter(x => x != null).length; const f = a.filter(isBad).length; const li = latestIdx(t.unique_id);
    return { ...t, a, last: li < 0 ? null : a[li], failRate: ran ? f / ran * 100 : null, rowsFailing: li < 0 ? null : TRF[t.unique_id][li], model: testTarget(t), type: t.test_name || 'singular' };
  }), []);
  const fr = rows.filter(r => (st === 'all' || (st === 'fail' ? isBad(r.last) : r.last === 'pass')) && (ty === 'all' || r.type === ty) && (!q || (r.name + ' ' + (r.model ? NODE[r.model].name : '')).toLowerCase().includes(q.toLowerCase())));
  const passRate = 100 - sum(FAILS) / Math.max(1, sum(TESTED)) * 100;
  const opt = useMemo(() => { const p = pal(); return base(p, {
    grid: { left: 56, right: 20, top: 24, bottom: 54 },
    tooltip: { trigger: 'axis', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 }, formatter: a => { const i = a[0].dataIndex; return `<b>${esc(fmtRun(RUNS[i].t))}</b><br/>${esc(FAILS[i])} failing of ${esc(TESTED[i])} tests<br/>Pass rate ${esc(pct(100 - FAILS[i] / Math.max(1, TESTED[i]) * 100))}`; } },
    xAxis: ax(p, 'Run start time', { type: 'category', data: RUNS.map(r => fmtRun(r.t)), splitLine: { show: false }, nameGap: 34 }),
    yAxis: ax(p, 'Failing tests (count)', { type: 'value', minInterval: 1, nameGap: 34 }),
    series: [{ type: 'bar', barMaxWidth: 14, color: p.fail, data: FAILS.map(v => ({ value: v, itemStyle: { color: v ? p.fail : p.line, borderRadius: [2, 2, 0, 0] } })) }],
  }); }, [theme]);
  const series = SERIES[sel];
  const sl = SERIES_LIST.filter(s => (eng === 'all' || s.pts[eng]) && (!q || s.relation.toLowerCase().includes(q.toLowerCase())));
  const engineCount = e => SERIES_LIST.filter(s => s.pts[e]).length;
  return <div className="page">
    <PageHead title="Tests & anomalies" sub="Test results across the last 30 runs, and monitored metrics with their expected band." />
    <div className="kpis">
      <Kpi label="Tests" value={TESTS.length} sub={`${types.length} test types`} />
      <Kpi label="Failing now" value={FAILS[LAST] || 0} tone={FAILS[LAST] ? 'fail' : 'ok'} sub="in the latest run" spark={FAILS} sparkType="bar" sparkTone="fail" />
      <Kpi label="Pass rate" value={pct(passRate)} sub={`${sum(FAILS)} failures in ${NR} runs`} spark={RUNS.map((r, i) => 100 - FAILS[i] / Math.max(1, TESTED[i]) * 100)} sparkTone="ok" />
      <Kpi label="Monitored metrics" value={SERIES_LIST.length} sub={`native ${engineCount('native')}, Cortex ${engineCount('cortex')}`} />
      <Kpi label="Anomalies now" value={SERIES_LIST.filter(seriesAnomalous).length} tone={SERIES_LIST.some(seriesAnomalous) ? 'fail' : 'ok'} sub={`${D.metrics.filter(m => m.anomaly).length} anomalous points in 30 days`} />
    </div>
    <Tabs value={tab} onChange={k => nav('tests', null, { tab: k })} tabs={[{ key: 'results', label: 'Test results', count: TESTS.length }, { key: 'anomalies', label: 'Anomaly detection', count: SERIES_LIST.length }]} />
    {tab === 'results' ? <>
      <div className="fbar">
        <Seg label="Status" options={[['all', 'All'], ['fail', 'Failing'], ['pass', 'Passing']]} value={st} onChange={setSt} />
        <div className="fgrp"><label htmlFor="tty">Test type</label><select id="tty" className="inp" value={ty} onChange={e => setTy(e.target.value)}><option value="all">All types</option>{types.map(t => <option key={t} value={t}>{t}</option>)}</select></div>
        <div className="fgrp"><label htmlFor="tq">Search</label><input id="tq" className="inp" placeholder="Test or model name" value={q} onChange={e => setQ(e.target.value)} /></div>
        <span className="muted">{fr.length} of {rows.length} tests</span>
      </div>
      <Card title="Failing tests per run" sub="Count of tests that failed or errored in each of the last 30 runs.">
        <EChart option={opt} height={220} />
      </Card>
      <Card title="Test results" sub="Failing first. Click a row to open the test." flush>
        <DataTable rows={fr} rowKey={r => r.unique_id} onRow={r => go('node', r.unique_id)} maxH={560} sort={{ key: 'last', dir: 1 }}
          cols={[
            { key: 'last', label: 'Status', render: r => <TestPill s={r.last} />, sort: r => (isBad(r.last) ? 0 : r.last === 'warn' ? 1 : 2) },
            { key: 'name', label: 'Test', render: r => <span title={r.name}>{r.name.length > 52 ? r.name.slice(0, 51) + '…' : r.name}</span> },
            { key: 'type', label: 'Type' },
            { key: 'model', label: 'Model', render: r => r.model ? <NodeLink id={r.model} /> : <span className="muted">—</span>, sort: r => (r.model ? NODE[r.model].name : '') },
            { key: 'column', label: 'Column', render: r => r.column || <span className="muted">—</span> },
            { key: 'strip', label: 'Last 30 runs', ns: true, render: r => <RunStrip arr={r.a} /> },
            { key: 'failRate', label: 'Fail rate', num: true, desc: true, render: r => pct(r.failRate) },
            { key: 'rowsFailing', label: 'Failing rows', num: true, desc: true, render: r => r.rowsFailing ? <b className="t-fail">{r.rowsFailing}</b> : <span className="muted">0</span> },
            { key: 'rc', label: 'Root cause', ns: true, render: r => isBad(r.last) && TRC[r.unique_id] && NODE[TRC[r.unique_id].id] ? <div className="cell2"><NodeLink id={TRC[r.unique_id].id} /><small>{rcKind(TRC[r.unique_id].kind)}</small></div> : <span className="muted">—</span> },
          ]} />
      </Card>
    </> : <>
      <div className="fbar">
        <Seg label="Engine" options={[['all', 'All'], ['native', 'Native'], ['cortex', `Cortex${engineCount('cortex') ? '' : ' (none yet)'}`]]} value={eng} onChange={setEng} />
        <div className="fgrp"><label htmlFor="aq">Search</label><input id="aq" className="inp" placeholder="Table name" value={q} onChange={e => setQ(e.target.value)} /></div>
      </div>
      <div className="grid2">
        <Card title={series ? `${humanMetric(series.metric)} of ${series.relation}` : 'Pick a metric'} sub="Observed value against the expected value and its band, daily, last 31 days. A red dot is a point outside the band."
          right={series && (Object.keys(series.pts).length > 1 ? <Seg label="Engine" options={Object.keys(series.pts).map(e => [e, e === 'cortex' ? 'Cortex' : 'Native'])} value={series.pts[selEng] ? selEng : Object.keys(series.pts)[0]} onChange={setSelEng} /> : <EngineBadge e={Object.keys(series.pts)[0]} />)}>
          <BandChart series={series} engine={series && series.pts[selEng] ? selEng : series ? Object.keys(series.pts)[0] : 'native'} />
        </Card>
        <Card title="Shadow scoring" sub="Native and Cortex scores for the same points, side by side.">
          <ShadowPanel series={series} />
        </Card>
      </div>
      <Card title="Monitored metrics" sub="Click a row to chart it. Anomalies first." flush>
        <DataTable rows={sl} rowKey={r => r.key} selKey={sel} onRow={r => { setSel(r.key); setSelEng(Object.keys(r.pts)[0]); }} sort={{ key: 'an', dir: -1 }}
          empty={eng === 'cortex' ? 'Cortex has not scored any metric yet. Cortex scores appear here once the Cortex engine runs alongside native scoring.' : undefined}
          cols={[
            { key: 'an', label: 'Status', render: r => seriesAnomalous(r) ? <Pill tone="fail">Anomaly</Pill> : <Pill tone="ok">In band</Pill>, sort: r => (seriesAnomalous(r) ? 1 : 0) },
            { key: 'relation', label: 'Table', render: r => <NodeLink id={r.uid} label={r.relation} /> },
            { key: 'metric', label: 'Metric', render: r => humanMetric(r.metric) },
            { key: 'eng', label: 'Engine', ns: true, render: r => <span className="hstack">{Object.keys(r.pts).map(e => <EngineBadge key={e} e={e} />)}</span> },
            { key: 'v', label: 'Latest', num: true, render: r => fmtNum(seriesLatest(r).value), sort: r => seriesLatest(r).value },
            { key: 'band', label: 'Expected band', ns: true, render: r => { const x = seriesLatest(r); return `${fmtNum(x.lower)} to ${fmtNum(x.upper)}`; } },
            { key: 'dev', label: 'vs expected', num: true, render: r => { const x = seriesLatest(r); if (x.value == null || !x.expected) return '—'; const d = (x.value - x.expected) / x.expected * 100; return `${d > 0 ? '+' : ''}${d.toFixed(1)}%`; }, sort: r => { const x = seriesLatest(r); return Math.abs((x.value - x.expected) / x.expected); } },
            { key: 'sp', label: 'Last 31 days', ns: true, render: r => <Spark data={(r.pts.native || Object.values(r.pts)[0]).map(x => x.value)} tone="accent" w={96} h={22} /> },
          ]} />
      </Card>
    </>}
  </div>;
}
