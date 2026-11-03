import React, { useState, useMemo } from 'react';
import { esc, MS, fmtTs, fmtSpan } from '../lib/format.js';
import { D, AS_OF, TESTS_OF, SERIES_LIST, SLAS, seriesAnomalous, incAsset, incTarget, NOT_RECORDED } from '../lib/model.js';
import { useApp, pal, base, ax, EChart } from '../lib/charts.jsx';
import { Pill, Spark, Kpi, Seg, Card, PageHead, DataTable } from '../components/ui.jsx';

/* ---------------- Schedules & freshness ---------------- */
export function Freshness() {
  const { incidents, go, theme } = useApp();
  const [tab, setTab] = useState('sched');
  const silences = incidents.filter(i => i.kind === 'silence' && i.state !== 'resolved');
  const late = SLAS.filter(s => s.late);
  const src = D.source_freshness.map(f => ({ ...f, over: f.max_lag_min != null && f.freshness_lag_min > f.max_lag_min }));
  const limits = [...new Set(src.map(f => f.max_lag_min).filter(v => v != null))];
  const optS = useMemo(() => { const p = pal(); return base(p, {
    grid: { left: 170, right: 70, top: 10, bottom: 46 },
    tooltip: { trigger: 'item', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 }, formatter: e => { const s = SLAS[e.dataIndex]; return `<b>${esc(s.name)}</b><br/>Last success ${esc(fmtTs(s.last))}<br/>Allowed ${esc(s.expected_every)} + ${esc(s.grace)} grace<br/>${esc(s.late ? 'Late by ' + fmtSpan(AS_OF - s.due) : 'Due in ' + fmtSpan(s.due - AS_OF))}`; } },
    xAxis: ax(p, 'Time since last success (% of allowed interval + grace)', { type: 'value', min: 0, max: v => Math.max(140, Math.ceil(v.max / 20) * 20), axisLabel: { color: p.ink3, fontSize: 10.5, formatter: '{value}%' } }),
    yAxis: ax(p, '', { type: 'category', inverse: true, data: SLAS.map(s => s.name), splitLine: { show: false }, axisLabel: { color: p.ink2 } }),
    series: [{ type: 'bar', barMaxWidth: 14, data: SLAS.map(s => ({ value: s.used == null ? null : +(s.used * 100).toFixed(1), itemStyle: { color: s.late ? p.stale : p.ok, borderRadius: 2 } })),
      label: { show: true, position: 'right', color: p.ink3, fontSize: 10.5, formatter: e => fmtSpan(AS_OF - SLAS[e.dataIndex].last) },
      markLine: { symbol: 'none', silent: true, lineStyle: { color: p.ink2, type: 'dashed' }, label: { formatter: 'Due', color: p.ink2, fontSize: 10.5 }, data: [{ xAxis: 100 }] } }],
  }); }, [theme]);
  const optF = useMemo(() => { const p = pal(); return base(p, {
    grid: { left: 170, right: 40, top: 10, bottom: 46 },
    tooltip: { trigger: 'item', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 }, formatter: e => `<b>${esc(src[e.dataIndex].name)}</b><br/>Newest data is ${esc(src[e.dataIndex].freshness_lag_min)} min old<br/>Limit ${esc(src[e.dataIndex].max_lag_min == null ? 'not declared' : src[e.dataIndex].max_lag_min + ' min')}` },
    xAxis: ax(p, 'Age of newest source data (minutes, log scale)', { type: 'log', min: 1, logBase: 10 }),
    yAxis: ax(p, '', { type: 'category', inverse: true, data: src.map(s => s.name), splitLine: { show: false }, axisLabel: { color: p.ink2 } }),
    series: [{ type: 'bar', barMaxWidth: 14, data: src.map(s => ({ value: s.freshness_lag_min, itemStyle: { color: s.over ? p.stale : p.ok, borderRadius: 2 } })), label: { show: true, position: 'right', color: p.ink3, formatter: '{c} min' },
      markLine: limits.length === 1 ? { symbol: 'none', silent: true, lineStyle: { color: p.ink2, type: 'dashed' }, label: { formatter: `Limit ${limits[0]} min`, color: p.ink2, fontSize: 10.5 }, data: [{ xAxis: limits[0] }] } : undefined }],
  }); }, [theme]); // eslint-disable-line
  return <div className="page">
    <PageHead title="Schedules & freshness" sub={`Declared schedules against the last success, missed-schedule alerts, and how old the newest source data is against each source's limit. Checked at ${fmtTs(AS_OF)}.`} />
    <div className="kpis">
      <Kpi label="Declared schedules" value={SLAS.length} sub={[...new Set(SLAS.map(s => `every ${s.expected_every}`))].join(', ')} />
      <Kpi label="Late now" value={late.length} tone={late.length ? 'stale' : 'ok'} sub={late.map(s => s.name).join(', ') || 'all on time'} />
      <Kpi label="Missed-schedule alerts" value={silences.length} tone={silences.length ? 'fail' : 'ok'} sub={silences.map(i => incTarget(i)).join(', ') || 'none open'} />
      <Kpi label="Oldest source data" value={D.source_freshness.some(f => f.freshness_lag_min != null) ? fmtSpan(Math.max(...D.source_freshness.map(f => f.freshness_lag_min)) * MS.m) : NOT_RECORDED} sub={`${src.filter(s => s.over).length} of ${src.length} sources over their limit`} tone={src.some(s => s.over) ? 'stale' : 'ok'} spark={(SERIES_LIST.find(x => x.metric === 'freshness_lag_minutes') || { pts: {} }).pts.native ? SERIES_LIST.find(x => x.metric === 'freshness_lag_minutes').pts.native.map(x => x.value) : null} sparkTone="stale" />
    </div>
    <div className="fbar">
      <Seg label="View" options={[['sched', 'Schedules'], ['src', 'Source freshness']]} value={tab} onChange={setTab} />
    </div>
    {tab === 'sched' ? <>
      <Card title="Time since last success" sub="100% is the declared interval plus grace; a bar past the dashed line is late. Label: time since last success.">
        <EChart option={optS} height={70 + SLAS.length * 30} onClick={e => go('node', SLAS[e.dataIndex].unique_id)} />
      </Card>
      <Card title="Schedules" sub="Late first. Click a row to open the model." flush>
        <DataTable rows={SLAS} rowKey={r => r.unique_id} onRow={r => go('node', r.unique_id)} sort={{ key: 'st', dir: -1 }}
          cols={[
            { key: 'name', label: 'Model', render: r => <b>{r.name}</b> },
            { key: 'st', label: 'Status', render: r => r.unknown ? <Pill tone="muted">Not recorded yet</Pill> : r.late ? <Pill tone="stale">Late</Pill> : <Pill tone="ok">On time</Pill>, sort: r => r.used },
            { key: 'expected_every', label: 'Expected every' },
            { key: 'grace', label: 'Grace' },
            { key: 'last', label: 'Last success', render: r => r.last == null ? <span className="muted">{NOT_RECORDED}</span> : fmtTs(r.last) },
            { key: 'due', label: 'Due by', render: r => fmtTs(r.due) },
            { key: 'gap', label: 'Late by / due in', render: r => r.unknown ? <span className="muted">—</span> : r.late ? <b className="t-stale">late by {fmtSpan(AS_OF - r.due)}</b> : `in ${fmtSpan(r.due - AS_OF)}`, sort: r => AS_OF - r.due },
            { key: 'alert', label: 'Missed-schedule alert', render: r => { const i = silences.find(x => incAsset(x) === r.unique_id); return i ? <span>{i.acknowledged_by ? <Pill tone="info">Acknowledged by {i.acknowledged_by}</Pill> : <Pill tone="fail">Open</Pill>}</span> : <span className="muted">None</span>; }, ns: true },
          ]} />
      </Card>
    </> : <>
      <Card title="Age of newest source data" sub="Minutes since the newest row landed in each source, log scale. Purple bars are over the source's declared limit.">
        <EChart option={optF} height={70 + src.length * 32} onClick={e => go('node', src[e.dataIndex].unique_id)} />
      </Card>
      <Card title="Source freshness" sub="Click a row to open the source." flush>
        <DataTable rows={src} rowKey={r => r.unique_id} onRow={r => go('node', r.unique_id)} sort={{ key: 'freshness_lag_min', dir: -1 }}
          cols={[
            { key: 'name', label: 'Source', render: r => <b>{r.name}</b> },
            { key: 'st', label: 'Status', render: r => r.over ? <Pill tone="stale">Stale</Pill> : <Pill tone="ok">Fresh</Pill>, sort: r => r.freshness_lag_min },
            { key: 'freshness_lag_min', label: 'Data age', num: true, desc: true, render: r => r.over ? <b className="t-stale">{fmtSpan(r.freshness_lag_min * MS.m)}</b> : `${r.freshness_lag_min} min` },
            { key: 'max_lag_min', label: 'Limit', num: true, render: r => r.max_lag_min == null ? <span className="muted">Not declared</span> : `${r.max_lag_min} min` },
            { key: 'an', label: 'Lag trend', ns: true, render: r => { const x = SERIES_LIST.find(z => z.uid === r.unique_id); return x ? <Spark data={Object.values(x.pts)[0].map(v => v.value)} w={100} h={20} tone={seriesAnomalous(x) ? 'stale' : 'muted'} /> : <span className="muted">—</span>; } },
            { key: 'tests', label: 'Tests', num: true, render: r => (TESTS_OF[r.unique_id] || []).length },
          ]} />
      </Card>
    </>}
  </div>;
}
