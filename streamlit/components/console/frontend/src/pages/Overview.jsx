import React, { useState, useMemo, Fragment } from 'react';
import { MS, parseT, fmtTs, fmtRun, fmtSec, sum, median } from '../lib/format.js';
import { D, RUNS, LAST, AS_OF, NODE, ASSETS, FAILS, TESTED, SLAS, LAYER_GROUP, LAYER_LABEL, MODEL_KEYS, KIND_LABEL, incNode, incAsset, incTarget, CAP, HAS_MODEL_LAYERS, LAYER_ISSUES, LAYER_ISSUE_LABEL } from '../lib/model.js';
import { useApp, pal, base, ax, EChart } from '../lib/charts.jsx';
import { stripClass, stripJoin } from '../lib/strip.js';
import { Ic, ICON, Pill, StatusPill, SevPill, Layer, Kpi, Seg, Card, PageHead, DataTable, WINDOWS, runsInWindow } from '../components/ui.jsx';

/* ---------------- Overview ---------------- */
export function HealthStrip({ health }) {
  const p = pal();
  return <div className={stripClass(health.length)}>{health.map((h, i) => <Fragment key={h.layer}>
    {stripJoin(i, health.length) && <div className="hjoin"><Ic d={ICON.chev} s={18} /></div>}
    <div className="htile" tabIndex={0} style={{ '--lc': `var(--${h.layer})` }} aria-label={`${LAYER_LABEL[h.layer]} health ${h.score == null ? 'not available' : Math.round(h.score)} out of 100`}>
      <div className="htop"><div className="hname">{LAYER_LABEL[h.layer]}<small>{h.models} models, {h.tests} tests</small></div>
        <div className={'hscore ' + (h.score == null ? '' : h.score >= 95 ? 't-ok' : h.score >= 80 ? 't-warn' : 't-fail')}>{h.score == null ? 'n/a' : Math.round(h.score)}<span>/100</span></div></div>
      <div className="hcomps">{h.comps.map(c => <div key={c.key} className="hc">{c.label}<div className="bar"><i style={{ width: (c.v == null ? 0 : c.v) + '%', background: c.v == null ? 'transparent' : c.v >= 95 ? p.ok : c.v >= 80 ? p.warn : p.fail }} /></div><b>{c.v == null ? 'n/a' : Math.round(c.v)}</b></div>)}</div>
      <div className="hpop" role="tooltip">
        <h4>How the {LAYER_LABEL[h.layer].toLowerCase()} score is computed</h4>
        Each test scores 100 when it passes, 50 on a warning, 0 when it fails. The layer score is the weighted average of four parts; a part with no data is left out and the remaining weights are rescaled.
        <div className="f">score = Σ(weight × part) ÷ Σ(weights used){'\n'}= {h.comps.filter(c => c.v != null).map(c => `${c.w} × ${c.v.toFixed(1)}`).join(' + ')}{h.W ? ` ÷ ${h.W.toFixed(2)}` : ''} = {h.score == null ? 'n/a' : h.score.toFixed(1)}</div>
        <table><tbody>{h.comps.map(c => <tr key={c.key}><td><b>{c.label}</b> <span className="muted">({Math.round(c.w * 100)}%)</span><div className={c.v == null ? 'drop' : ''}>{c.detail}{c.v == null ? '; weight dropped' : ''}</div></td><td className="n">{c.v == null ? 'n/a' : c.v.toFixed(1)}</td></tr>)}</tbody></table>
      </div>
    </div></Fragment>)}</div>;
}

/* Layer design, one line under the layer cards (placement A of story cfc1e7fc): the issues of lib/model.js layerIssues,
   listed in the .hpop popover on hover or focus. Informative only: not counted in any score. */
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
export function LayerCheck() {
  if (!HAS_MODEL_LAYERS) return null;
  if (!LAYER_ISSUES.length) return <div className="note lcheck-ok">Layer design · no issues</div>;
  const k = kind => LAYER_ISSUES.filter(i => i.kind === kind).length;
  const counts = [[k('backward'), 'backward dependency', 'backward dependencies'], [k('nolayer'), 'model with no layer', 'models with no layer'], [k('standard'), 'outside the standard', 'outside the standard']]
    .filter(([n]) => n).map(([n, one, many]) => plural(n, one, many)).join(', ');
  const std = CAP.layer_standard;
  return <div className="note hstack lcheck" tabIndex={0} aria-label={`Layer design: ${plural(LAYER_ISSUES.length, 'layering issue', 'layering issues')}`}>
    <span>Layer design</span><Pill tone="warn">{plural(LAYER_ISSUES.length, 'layering issue', 'layering issues')}</Pill><span>{counts}. Not counted in the scores above.</span>
    <div className="hpop" role="tooltip">
      <h4>Layer design: {plural(LAYER_ISSUES.length, 'issue', 'issues')} in {D.project}</h4>
      Checked inside this project: a model must not read from a layer further downstream, {std ? <>every model sits in a layer folder, and layers follow layer_standard ({std.join(', ')})</> : 'and every model sits in a layer folder'}. These flags inform only; they never change a layer score.
      <table><tbody>{LAYER_ISSUES.map(i => <tr key={i.unique_id + i.kind}><td><b>{i.name}</b><div>{i.reason}</div></td><td className="n">{LAYER_ISSUE_LABEL[i.kind]}</td></tr>)}</tbody></table>
    </div>
  </div>;
}

export function Overview() {
  const { incidents, status, health, go, theme } = useApp();
  const [win, setWin] = useState('15'); const [layer, setLayer] = useState('all');
  const wr = useMemo(() => runsInWindow(win), [win]);
  const open = incidents.filter(i => i.state !== 'resolved');
  const missed = SLAS.filter(s => s.late || incidents.some(i => i.kind === 'silence' && i.state !== 'resolved' && incAsset(i) === s.unique_id));
  const credits = wr.map(r => r.credits); const pending = wr.filter(r => r.credits == null).length;
  const days = [...Array(7)].map((_, i) => AS_OF - (6 - i) * MS.d);
  const openTrend = days.map(d => incidents.filter(i => parseT(i.first_detected_at) <= d && (i.state !== 'resolved' || (i.resolvedAt || 0) > d)).length);
  const okRuns = wr.filter(r => r.status === 'success').length;
  const byState = st => incidents.filter(i => i.state === st).length;
  const winLabel = win === '7' ? 'last 7 days' : 'last 15 days';
  const attributed = wr.filter(r => r.credits != null);
  const rows = open.filter(i => layer === 'all' || (incAsset(i) && LAYER_GROUP(NODE[incAsset(i)].layer) === layer));
  const opt = useMemo(() => {
    const p = pal();
    return base(p, {
      grid: { left: 60, right: 56, top: 30, bottom: 54 },
      legend: { top: 0, right: 0, data: ['Run duration', 'Failing tests'], textStyle: { color: p.ink2, fontSize: 11 } },
      xAxis: ax(p, 'Run start time', { type: 'category', data: wr.map(r => fmtRun(r.t)), splitLine: { show: false }, nameGap: 34 }),
      yAxis: [ax(p, 'Duration (seconds)', { type: 'value', nameGap: 40 }), ax(p, 'Failing tests (count)', { type: 'value', minInterval: 1, position: 'right', nameGap: 30, splitLine: { show: false } })],
      series: [
        { name: 'Run duration', type: 'bar', barMaxWidth: 16, color: p.bar, data: wr.map(r => ({ value: r.duration_s, inv: r.invocation_id, itemStyle: { color: r.status === 'success' ? p.bar : p.fail, borderRadius: [2, 2, 0, 0] } })) },
        { name: 'Failing tests', type: 'line', yAxisIndex: 1, step: 'middle', symbol: 'none', color: p.ink2, lineStyle: { width: 1.5 }, data: wr.map(r => FAILS[r.idx]) },
      ],
    });
  }, [wr, theme]);
  const sevRank = s => (s === 'error' ? 0 : 1);
  return <div className="page">
    <PageHead title="Overview" sub={`What is broken now and how the platform ran, as of ${fmtTs(AS_OF)}. Project ${D.project}.`} />
    <div className="kpis">
      <Kpi label="Open incidents" value={incidents.filter(i => i.state === 'open').length} tone={incidents.some(i => i.state === 'open') ? 'fail' : 'ok'} sub={`${incidents.filter(i => i.state === 'acknowledged').length} acknowledged`} spark={openTrend} sparkTone="muted" help="Incidents that are neither acknowledged nor resolved. The sparkline is the number of unresolved incidents per day over the last 7 days."
        calc={[['Open', byState('open')], ['Acknowledged', byState('acknowledged')], ['Resolved', byState('resolved')], ['Open: errors', incidents.filter(i => i.state === 'open' && i.severity === 'error').length], ['Open: warnings', incidents.filter(i => i.state === 'open' && i.severity !== 'error').length]]} />
      <Kpi label="Failing tests" value={FAILS[LAST] || 0} tone={FAILS[LAST] ? 'fail' : 'ok'} sub={`of ${TESTED[LAST] || 0} in the latest run`} spark={wr.map(r => FAILS[r.idx])} sparkType="bar" sparkTone="fail" help="Tests that failed or errored in the latest run. A warning is not counted as failing. The bars are the failing tests of each run in the window."
        calc={[['Latest run', RUNS[LAST] ? fmtTs(RUNS[LAST].t) : '—'], ['Tests in the latest run', TESTED[LAST] || 0], ['Failed or errored', FAILS[LAST] || 0], [`Runs with a failing test, ${winLabel}`, wr.filter(r => FAILS[r.idx]).length]]} />
      <Kpi label="Successful runs" value={`${okRuns} of ${wr.length}`} tone={okRuns === wr.length ? 'ok' : 'warn'} sub={`median ${fmtSec(median(wr.map(r => r.duration_s)))}`} spark={wr.map(r => r.duration_s)} sparkTone="muted" help="dbt invocations that ended in success, out of all invocations in the window. A run with a failing model or test ends in error. The sparkline is the duration of each run."
        calc={[[`Runs, ${winLabel}`, wr.length], ['Success', okRuns], ['Error or not finished', wr.length - okRuns], ['Median duration', fmtSec(median(wr.map(r => r.duration_s)))], ['Longest run', wr.length ? fmtSec(Math.max(...wr.map(r => r.duration_s || 0))) : '—']]} />
      <Kpi label="Missed schedules" value={missed.length} tone={missed.length ? 'stale' : 'ok'} sub={`of ${SLAS.length} declared schedules`} help="A schedule is declared on a model (icecore_sla). It is missed when the last success is older than the expected interval plus the grace period, or when a missed-schedule alert is still open."
        calc={[['Declared schedules', SLAS.length], ['Met', SLAS.length - missed.length], ...missed.map(s => [`Missed: ${s.name}`, `every ${s.expected_every}, grace ${s.grace || '0'}`])]} />
      <Kpi label="Credits" value={sum(credits).toFixed(2)} sub={pending ? `${pending} recent runs not yet attributed` : 'all runs attributed'} spark={credits} sparkTone="accent" help="Warehouse credits used by the runs in the window, from Snowflake's query history. Snowflake publishes this with a delay of up to a few hours, so the most recent runs show no credits yet."
        calc={[[`Runs, ${winLabel}`, wr.length], ['With credits', attributed.length], ['Not yet attributed', pending], ['Credits', sum(credits).toFixed(4)], ['Average per attributed run', attributed.length ? (sum(attributed.map(r => r.credits)) / attributed.length).toFixed(4) : '—']]} />
    </div>
    <HealthStrip health={health} />
    <LayerCheck />
    <div className="fbar">
      <Seg label="Window" options={WINDOWS} value={win} onChange={setWin} />
      <Seg label="Layer" options={[['all', 'All'], ...MODEL_KEYS.map(k => [k, LAYER_LABEL[k]])]} value={layer} onChange={setLayer} />
    </div>
    <div className="grid2">
      <Card title="Needs attention" sub="Unresolved incidents, errors first. Click a row to open the node." flush>
        <DataTable rows={rows} rowKey={r => r.incident_key} onRow={r => incNode(r) ? go('node', incNode(r)) : go('incidents')}
          sort={{ key: 'sev', dir: 1 }}
          cols={[
            { key: 'sev', label: 'Severity', render: r => <SevPill s={r.severity} />, sort: r => sevRank(r.severity) * 1e13 - parseT(r.first_detected_at) },
            { key: 'title', label: 'Incident', cls: 'wrap', render: r => <div className="cell2"><span>{r.title}</span><small>{KIND_LABEL[r.kind]} on {incTarget(r)}{r.root_cause && NODE[r.root_cause] ? `; root cause ${NODE[r.root_cause].name}` : ''}</small></div> },
            { key: 'first', label: 'First seen', render: r => fmtTs(parseT(r.first_detected_at)), sort: r => parseT(r.first_detected_at) },
            { key: 'occurrences', label: 'Times', num: true, desc: true },
            { key: 'state', label: 'State', render: r => r.state === 'open' ? <Pill tone="fail">Open</Pill> : <Pill tone="info">Acknowledged</Pill> },
          ]} empty="Nothing needs attention in this layer." />
      </Card>
      <Card title="Runs: duration and failing tests" sub="Each bar is one run; red bars ended in error. Click a bar to open its node timeline.">
        <EChart option={opt} height={300} onClick={e => e.data && e.data.inv && go('runs', null, { run: e.data.inv })} />
      </Card>
    </div>
    <Card title="Nodes that are not OK" sub="Status from the latest run, tests, schedules and open incidents. Click a row to open the node." flush>
      <DataTable rows={ASSETS.filter(n => status[n.unique_id].status !== 'ok' && (layer === 'all' || LAYER_GROUP(n.layer) === layer))} rowKey={r => r.unique_id} onRow={r => go('node', r.unique_id)}
        sort={{ key: 'st', dir: -1 }}
        cols={[
          { key: 'name', label: 'Node', render: r => <b>{r.name}</b> },
          { key: 'layer', label: 'Layer', render: r => <Layer l={r.layer} /> },
          { key: 'st', label: 'Status', render: r => <StatusPill s={status[r.unique_id].status} />, sort: r => ({ failed: 4, stale: 3, warn: 2, notrun: 1, ok: 0 })[status[r.unique_id].status] },
          { key: 'why', label: 'Why', cls: 'wrap', ns: true, render: r => status[r.unique_id].reasons.join('; ') },
        ]} empty="Every model in this layer is OK." />
    </Card>
  </div>;
}
