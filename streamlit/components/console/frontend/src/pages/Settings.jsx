import React from 'react';
import { parseT, fmtTs } from '../lib/format.js';
import { D, RUNS, LAST, CAP, NOT_RECORDED } from '../lib/model.js';
import { useApp } from '../lib/charts.jsx';
import { Kpi, Card, PageHead, DataTable } from '../components/ui.jsx';

/* ---------------- Settings ---------------- */
export function Settings() {
  const { incidents } = useApp();
  const channels = {}; incidents.forEach(i => { channels[i.channel] = (channels[i.channel] || 0) + 1; });
  return <div className="page">
    <PageHead title="Settings & about" sub="What this account can do, the versions in use, and where alerts go." />
    <div className="kpis">
      <Kpi label="Edition" value={CAP.edition || '—'} />
      <Kpi label="icecore version" value={CAP.icecore_version || '—'} />
      <Kpi label="Cortex anomaly engine" value={CAP.cortex ? 'Available' : 'Not available'} tone={CAP.cortex ? 'ok' : 'warn'} sub={CAP.cortex ? 'can shadow-score metrics' : 'native engine only'} />
      <Kpi label="Alert channels" value={Object.keys(channels).length} />
    </div>
    <div className="grid2 even">
      <Card title="Capabilities" flush><div className="kv">
        <div>Project</div><div>{D.project}</div>
        <div>Edition</div><div>{CAP.edition || NOT_RECORDED}</div>
        <div>Event table</div><div className="mono">{CAP.event_table || NOT_RECORDED}</div>
        <div>Cortex</div><div>{CAP.cortex ? 'Available' : 'Not available'}</div>
        <div>icecore version</div><div>{CAP.icecore_version || NOT_RECORDED}</div>
        <div>dbt versions</div><div>{(CAP.dbt_versions || []).join('; ') || NOT_RECORDED}</div>
        <div>Report generated</div><div>{D.generated_at ? fmtTs(parseT(D.generated_at)) : NOT_RECORDED}</div>
        <div>Latest run</div><div>{RUNS[LAST] ? fmtTs(RUNS[LAST].t) : NOT_RECORDED}</div>
      </div></Card>
      <Card title="Alert channels" sub="Channels that received incidents in the window." flush>
        <DataTable rows={Object.entries(channels).map(([c, n]) => ({ c, n }))} rowKey={r => r.c} sort={{ key: 'n', dir: -1 }} cols={[{ key: 'c', label: 'Channel' }, { key: 'n', label: 'Incidents', num: true, desc: true }]} />
      </Card>
    </div>
    <Card title="Health score" sub="Shown per layer on the Overview; the same formula appears when you hover a layer tile.">
      <div style={{ maxWidth: '76ch', color: 'var(--ink-2)' }}>
        <p style={{ marginTop: 0 }}>Each test scores 100 when it passes, 50 on a warning and 0 when it fails. The layer score is a weighted average:</p>
        <p className="mono" style={{ background: 'var(--bg)', padding: '8px 10px', borderRadius: 6 }}>score = (0.50 × tests + 0.20 × freshness + 0.15 × schedules + 0.15 × anomalies) ÷ weights used</p>
        <p>Freshness is the share of the layer's models built successfully in the latest run. Schedules is the share of declared schedules met. Anomalies is the share of monitored metrics inside their expected band. A part with no data is left out and the other weights are rescaled. Gold includes gold analytics models.</p>
      </div>
    </Card>
  </div>;
}
