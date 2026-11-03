import React, { useState, useMemo } from 'react';
import { esc, MS, parseT, fmtDay, fmtTs, fmtSpan, rcKind } from '../lib/format.js';
import { AS_OF, NODE, KIND_LABEL, incNode, incAsset, incTarget } from '../lib/model.js';
import { useApp, pal, base, ax, EChart } from '../lib/charts.jsx';
import { SevPill, Kpi, Seg, Card, Tabs, PageHead, DataTable, NodeLink } from '../components/ui.jsx';

/* ---------------- Incidents ---------------- */
export function Incidents() {
  const { incidents, act, go, theme } = useApp();
  const [tab, setTab] = useState('open'); const [kind, setKind] = useState('all'); const [sev, setSev] = useState('all'); const [q, setQ] = useState('');
  const by = s => incidents.filter(i => i.state === s);
  const sevTxt = a => { const e = a.filter(i => i.severity === 'error').length, w = a.filter(i => i.severity === 'warn').length; return a.length ? `${e} error, ${w} warning` : ''; };
  const rows = by(tab).filter(i => (kind === 'all' || i.kind === kind) && (sev === 'all' || i.severity === sev) && (!q || (i.title + ' ' + i.node + ' ' + (i.channel || '')).toLowerCase().includes(q.toLowerCase())));
  const unresolved = incidents.filter(i => i.state !== 'resolved');
  const oldest = unresolved.length ? Math.min(...unresolved.map(i => parseT(i.first_detected_at))) : null;
  const shown = [...rows].sort((a, b) => parseT(a.first_detected_at) - parseT(b.first_detected_at));
  const opt = useMemo(() => {
    const p = pal(); const start = Math.min(...incidents.map(i => parseT(i.first_detected_at))) - MS.d / 2;
    return base(p, {
      grid: { left: 210, right: 40, top: 10, bottom: 46 },
      tooltip: { trigger: 'item', confine: true, backgroundColor: p.panel, borderColor: p.line, textStyle: { color: p.ink, fontSize: 12 }, formatter: e => { const i = shown[e.dataIndex]; return `<b>${esc(i.title)}</b><br/>First seen ${esc(fmtTs(parseT(i.first_detected_at)))}<br/>Open for ${esc(fmtSpan((i.resolvedAt || AS_OF) - parseT(i.first_detected_at)))}<br/>${esc(i.occurrences)} occurrence(s)`; } },
      xAxis: ax(p, 'Date (first seen to now)', { type: 'value', min: start, max: AS_OF + MS.h * 2, axisLabel: { color: p.ink3, fontSize: 10.5, formatter: v => fmtDay(v) }, splitNumber: 5 }),
      yAxis: ax(p, '', { type: 'category', inverse: true, data: shown.map(i => i.title.length > 30 ? i.title.slice(0, 29) + '…' : i.title), splitLine: { show: false } }),
      series: [
        { type: 'bar', stack: 'r', silent: true, itemStyle: { color: 'transparent' }, data: shown.map(i => parseT(i.first_detected_at)), barMaxWidth: 14, tooltip: { show: false } },
        { type: 'bar', stack: 'r', barMaxWidth: 14, data: shown.map(i => ({ value: (i.resolvedAt || AS_OF) - parseT(i.first_detected_at), itemStyle: { color: i.severity === 'error' ? p.fail : p.warn, borderRadius: 3 } })), label: { show: true, position: 'right', color: p.ink3, fontSize: 10.5, formatter: e => `${shown[e.dataIndex].occurrences}×` } },
      ],
    });
  }, [shown.map(i => i.incident_key + i.state).join(), theme]); // eslint-disable-line
  return <div className="page">
    <PageHead title="Incidents" sub="One row per issue. Acknowledge to tell the team someone is on it; resolve when it is fixed." />
    <div className="kpis">
      <Kpi label="Open" value={by('open').length} tone={by('open').length ? 'fail' : 'ok'} sub="not yet acknowledged" />
      <Kpi label="Acknowledged" value={by('acknowledged').length} sub="someone is on it" />
      <Kpi label="Errors" value={unresolved.filter(i => i.severity === 'error').length} tone="fail" sub="unresolved, severity error" />
      <Kpi label="Warnings" value={unresolved.filter(i => i.severity === 'warn').length} tone="warn" sub="unresolved, severity warning" />
      <Kpi label="Oldest unresolved" value={oldest ? fmtSpan(AS_OF - oldest) : '—'} sub={oldest ? `since ${fmtTs(oldest)}` : 'nothing open'} />
    </div>
    <Tabs value={tab} onChange={setTab} tabs={[
      { key: 'open', label: 'Open', count: by('open').length, extra: sevTxt(by('open')) },
      { key: 'acknowledged', label: 'Acknowledged', count: by('acknowledged').length, extra: sevTxt(by('acknowledged')) },
      { key: 'resolved', label: 'Resolved', count: by('resolved').length },
    ]} />
    <div className="fbar">
      <Seg label="Kind" options={[['all', 'All'], ...Object.entries(KIND_LABEL)]} value={kind} onChange={setKind} />
      <Seg label="Severity" options={[['all', 'All'], ['error', 'Error'], ['warn', 'Warning']]} value={sev} onChange={setSev} />
      <div className="fgrp"><label htmlFor="iq">Search</label><input id="iq" className="inp" placeholder="Title, node or channel" value={q} onChange={e => setQ(e.target.value)} /></div>
    </div>
    <Card title={`${tab === 'open' ? 'Open' : tab === 'acknowledged' ? 'Acknowledged' : 'Resolved'} incidents`} sub="Click a row to open the node; use the action button to change state." flush>
      <DataTable rows={rows} rowKey={r => r.incident_key} onRow={r => incNode(r) ? go('node', incNode(r)) : null} sort={{ key: 'sev', dir: 1 }}
        empty={tab === 'resolved' ? 'No resolved incidents yet. Resolve one from the Acknowledged tab when the fix is in.' : tab === 'acknowledged' ? 'Nothing acknowledged. Acknowledge an open incident to show someone is on it.' : 'No open incidents match these filters.'}
        cols={[
          { key: 'sev', label: 'Severity', render: r => <SevPill s={r.severity} />, sort: r => (r.severity === 'error' ? 0 : 1) * 1e13 - parseT(r.first_detected_at) },
          { key: 'title', label: 'Incident', cls: 'wrap', render: r => <div className="cell2"><span>{r.title}</span><small>{incNode(r) ? <NodeLink id={incNode(r)} label={incTarget(r)} /> : <span title="Not in the project graph">{incTarget(r)} (not in the project graph)</span>}</small></div> },
          { key: 'kind', label: 'Kind', render: r => KIND_LABEL[r.kind] || r.kind },
          { key: 'first', label: 'First seen', render: r => fmtTs(parseT(r.first_detected_at)), sort: r => parseT(r.first_detected_at) },
          { key: 'age', label: 'Open for', render: r => fmtSpan((r.resolvedAt || AS_OF) - parseT(r.first_detected_at)), sort: r => parseT(r.first_detected_at), desc: false },
          { key: 'occurrences', label: 'Times', num: true, desc: true },
          { key: 'channel', label: 'Channel', render: r => <span className="muted">{r.channel}</span> },
          { key: 'ack', label: 'Acknowledged by', render: r => r.acknowledged_by || <span className="muted">Nobody</span>, sort: r => r.acknowledged_by || '' },
          { key: 'rc', label: 'Root cause', ns: true, render: r => r.root_cause && NODE[r.root_cause] ? <div className="cell2"><NodeLink id={r.root_cause} /><small>{rcKind(r.root_cause_kind)}{incAsset(r) && <> <button className="linkish" onClick={e => { e.stopPropagation(); go('lineage', null, { focus: incAsset(r), mode: 'root' }); }}>Trace</button></>}</small></div> : r.root_cause || (incAsset(r) ? <button className="linkish" onClick={e => { e.stopPropagation(); go('lineage', null, { focus: incAsset(r), mode: 'root' }); }}>Trace in lineage</button> : <span className="muted">Not set</span>) },
          { key: 'act', label: '', ns: true, render: r => r.state === 'open' ? <button className="btn primary" onClick={e => { e.stopPropagation(); act(r.incident_key, 'acknowledged'); }}>Acknowledge</button>
            : r.state === 'acknowledged' ? <button className="btn" onClick={e => { e.stopPropagation(); act(r.incident_key, 'resolved'); }}>Resolve</button>
              : <button className="btn" onClick={e => { e.stopPropagation(); act(r.incident_key, 'open'); }}>Reopen</button> },
        ]} />
    </Card>
    <Card title="How long each incident has been open" sub="Bars run from first seen to now (or to resolution); the label is the number of occurrences. Red is error, amber is warning.">
      <EChart option={opt} height={Math.max(160, 60 + shown.length * 30)} />
    </Card>
    <p className="note">Acknowledge records your user on the incident in icecore. Resolve and Reopen are kept in this browser tab only; an incident resolves by itself once its check passes again.</p>
  </div>;
}
