import React, { useState, useMemo } from 'react';
import { MS, fmtDay, fmtTs } from '../lib/format.js';
import { AS_OF, BY_NAME, SCHEMA, CHANGE_LABEL } from '../lib/model.js';
import { useApp, pal, base, ax, EChart } from '../lib/charts.jsx';
import { Pill, Kpi, Seg, Card, PageHead, DataTable, NodeLink } from '../components/ui.jsx';

/* ---------------- Schema changes ---------------- */
export function Schema() {
  const { theme } = useApp();
  const [kind, setKind] = useState('all'); const [q, setQ] = useState('');
  const rows = SCHEMA.filter(c => (kind === 'all' || c.change === kind) && (!q || (c.relation + ' ' + c.column).toLowerCase().includes(q.toLowerCase())));
  const days = [...Array(15)].map((_, i) => { const d = new Date(AS_OF - (14 - i) * MS.d); d.setHours(0, 0, 0, 0); return d.getTime(); });
  const opt = useMemo(() => { const p = pal(); const col = { added: p.ok, removed: p.fail, type_changed: p.warn };
    return base(p, {
      grid: { left: 56, right: 20, top: 30, bottom: 54 },
      legend: { top: 0, right: 0, textStyle: { color: p.ink2, fontSize: 11 } },
      xAxis: ax(p, 'Day detected', { type: 'category', data: days.map(fmtDay), splitLine: { show: false }, nameGap: 34 }),
      yAxis: ax(p, 'Changes (count)', { type: 'value', minInterval: 1, nameGap: 34 }),
      series: Object.keys(CHANGE_LABEL).map(k => ({ name: CHANGE_LABEL[k], type: 'bar', stack: 's', barMaxWidth: 18, color: col[k],
        data: days.map(d => SCHEMA.filter(c => c.change === k && c.t >= d && c.t < d + MS.d).length) })),
    }); }, [theme]); // eslint-disable-line
  return <div className="page">
    <PageHead title="Schema changes" sub="Columns added, removed or changed type, per table, as detected between runs." />
    <div className="kpis">
      <Kpi label="Changes" value={SCHEMA.length} sub="last 15 days" spark={days.map(d => SCHEMA.filter(c => c.t >= d && c.t < d + MS.d).length)} sparkType="bar" />
      <Kpi label="Columns added" value={SCHEMA.filter(c => c.change === 'added').length} />
      <Kpi label="Columns removed" value={SCHEMA.filter(c => c.change === 'removed').length} tone={SCHEMA.some(c => c.change === 'removed') ? 'fail' : undefined} />
      <Kpi label="Type changes" value={SCHEMA.filter(c => c.change === 'type_changed').length} tone={SCHEMA.some(c => c.change === 'type_changed') ? 'warn' : undefined} />
      <Kpi label="Tables affected" value={new Set(SCHEMA.map(c => c.relation)).size} />
    </div>
    <div className="fbar">
      <Seg label="Change" options={[['all', 'All'], ...Object.entries(CHANGE_LABEL)]} value={kind} onChange={setKind} />
      <div className="fgrp"><label htmlFor="sq">Search</label><input id="sq" className="inp" placeholder="Table or column" value={q} onChange={e => setQ(e.target.value)} /></div>
    </div>
    <Card title="Schema changes per day" sub="Stacked by kind of change, last 15 days.">
      <EChart option={opt} height={230} />
    </Card>
    <Card title="Detected changes" sub="Newest first. Click a table name to open it; tables outside the project graph are not linked." flush>
      <DataTable rows={rows} rowKey={r => r.relation + r.column + r.t} sort={{ key: 't', dir: -1 }}
        cols={[
          { key: 't', label: 'Detected', render: r => fmtTs(r.t) },
          { key: 'relation', label: 'Table', render: r => BY_NAME[r.relation] ? <NodeLink id={BY_NAME[r.relation]} /> : <span title="Not in the project graph">{r.relation}</span> },
          { key: 'column', label: 'Column', render: r => <span className="mono">{r.column}</span> },
          { key: 'change', label: 'Change', render: r => <Pill tone={r.change === 'added' ? 'ok' : r.change === 'removed' ? 'fail' : 'warn'}>{CHANGE_LABEL[r.change] || r.change}</Pill> },
          { key: 'det', label: 'Details', ns: true, render: r => r.change === 'type_changed' ? <span className="mono">{r.from} to {r.to}</span> : <span className="mono">{r.type || ''}</span> },
        ]} />
    </Card>
  </div>;
}
