import React, { useState, useMemo, Fragment } from 'react';
import { MS, fmtRun } from '../lib/format.js';
import { RUNS, AS_OF, NODE, LAYER_GROUP, LAYER_LABEL, STATUS_LABEL, STATUS_TONE, NOT_RECORDED } from '../lib/model.js';
import { useApp } from '../lib/charts.jsx';

/* ---------------- small UI atoms ---------------- */
export const Ic = ({ d, s = 16 }) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>;
export const ICON = {
  overview: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  incidents: <><path d="M12 3 2.5 20h19z"/><path d="M12 10v4"/><path d="M12 17h.01"/></>,
  tests: <><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></>,
  runs: <><path d="M4 6h9"/><path d="M8 12h12"/><path d="M6 18h8"/></>,
  fresh: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  schema: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M10 10v10"/></>,
  cost: <><circle cx="12" cy="12" r="9"/><path d="M15 9.2c-.5-.8-1.6-1.3-3-1.3-1.8 0-3 .8-3 2s1.2 1.7 3 2.1 3 .9 3 2.1-1.2 2-3 2c-1.4 0-2.5-.5-3-1.3"/><path d="M12 6v1.9M12 16.1V18"/></>,
  lineage: <><circle cx="5" cy="6" r="2.2"/><circle cx="5" cy="18" r="2.2"/><circle cx="19" cy="12" r="2.2"/><path d="M7.2 6H10l6.8 5.2M7.2 18H10l6.8-5.2"/></>,
  models: <><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/></>,
  settings: <><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></>,
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>,
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></>,
  close: <path d="M6 6l12 12M18 6 6 18"/>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/></>,
  chev: <path d="m9 6 6 6-6 6"/>,
  core: <><path d="M12 2v20"/><rect x="7" y="3" width="10" height="18" rx="5"/><path d="M7 9h10M7 14h10"/></>,
};
export const Pill = ({ tone, children }) => <span className={'pill p-' + tone}>{children}</span>;
export const StatusPill = ({ s }) => <Pill tone={STATUS_TONE[s] || 'muted'}>{STATUS_LABEL[s] || s}</Pill>;
export const SevPill = ({ s }) => <Pill tone={s === 'error' ? 'fail' : s === 'warn' ? 'warn' : 'muted'}>{s === 'error' ? 'Error' : s === 'warn' ? 'Warning' : s}</Pill>;
/* Real run statuses also include warn, running and unfinished: the prototype's own pill tones, no new visual. */
const RUN_PILL = { success: ['ok', 'Success'], warn: ['warn', 'Warning'], running: ['muted', 'Running'], unfinished: ['muted', 'Unfinished'] };
export const RunPill = ({ s }) => { const [tone, label] = s == null ? ['muted', 'Not recorded yet'] : RUN_PILL[s] || ['fail', 'Error']; return <Pill tone={tone}>{label}</Pill>; };
export const TestPill = ({ s }) => (s == null ? <Pill tone="muted">No result</Pill> : <Pill tone={s === 'pass' ? 'ok' : s === 'warn' ? 'warn' : 'fail'}>{s === 'pass' ? 'Pass' : s === 'warn' ? 'Warning' : s === 'error' ? 'Error' : 'Fail'}</Pill>);
export const EngineBadge = ({ e }) => <span className={'badge ' + e}>{e === 'cortex' ? 'Cortex' : 'Native'}</span>;
export const Layer = ({ l }) => { if (l == null) return <span className="lyr muted"><i style={{ background: 'var(--muted)' }} />{NOT_RECORDED}</span>; const g = LAYER_GROUP(l); return <span className="lyr"><i style={{ background: `var(--${g}, var(--muted))` }} />{l === 'gold_analytics' ? 'Gold (analytics)' : LAYER_LABEL[g] || l}</span>; };

export function Spark({ data, tone = 'accent', w = 84, h = 28, type = 'line' }) {
  const vals = data.map(v => (v == null ? null : +v)); const nums = vals.filter(v => v != null);
  if (nums.length < 2) return null;
  const color = `var(--${tone === 'muted' ? 'ink-3' : tone})`;
  let min = type === 'bar' ? 0 : Math.min(...nums); const max = Math.max(...nums); const r = max - min || 1;
  const y = v => h - 2 - (v - min) / r * (h - 5);
  if (type === 'bar') {
    const bw = w / vals.length;
    return <svg width={w} height={h} className="strip" aria-hidden="true">{vals.map((v, i) => v == null ? null : <rect key={i} x={i * bw + .5} y={max === 0 ? h - 1 : y(v)} width={Math.max(1, bw - 1.2)} height={max === 0 ? 1 : h - y(v)} fill={color} opacity={v ? 1 : .35} rx="0.5" />)}</svg>;
  }
  const x = i => i * (w - 4) / (vals.length - 1) + 2;
  let d = '', pen = false;
  vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1); pen = true; });
  let li = vals.length - 1; while (li > 0 && vals[li] == null) li--;
  return <svg width={w} height={h} className="strip" aria-hidden="true"><path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" /><circle cx={x(li)} cy={y(vals[li])} r="2.2" fill={color} /></svg>;
}

export function RunStrip({ arr }) {
  const w = 4, gap = 1.5;
  return <svg width={arr.length * (w + gap)} height={14} className="strip" aria-label="Results of the last runs">{arr.map((s, i) => <rect key={i} x={i * (w + gap)} y={s == null ? 6 : 1} width={w} height={s == null ? 2 : 12} rx="1" fill={s == null ? 'var(--line)' : s === 'pass' ? 'var(--ok)' : s === 'warn' ? 'var(--warn)' : 'var(--fail)'} opacity={s === 'pass' ? .55 : 1}><title>{fmtRun(RUNS[i].t)}: {s || 'not run'}</title></rect>)}</svg>;
}

// help: what the number means; calc: the rows it is computed from ([label, value] pairs), shown on hover or focus of
// the card in the same popover as the layer health cards.
export function Kpi({ label, value, unit, sub, tone, spark, sparkType, sparkTone, help, calc, delta }) {
  return <div className={'kpi' + (help ? ' has-help' : '')} tabIndex={help ? 0 : undefined}>
    <div className="kpi-label">{label}{help && <span className="help" aria-hidden="true">?</span>}</div>
    {help && <div className="hpop" role="tooltip"><h4>{label}</h4>{help}
      {calc && calc.length > 0 && <table><tbody>{calc.map(([k, v]) => <tr key={k}><td>{k}</td><td className="n">{v}</td></tr>)}</tbody></table>}</div>}
    <div className="kpi-row"><div className={'kpi-value ' + (tone ? 't-' + tone : '')}>{value}{unit && <span className="unit">{unit}</span>}</div>{spark && <Spark data={spark} type={sparkType} tone={sparkTone || tone || 'accent'} />}</div>
    {(sub || delta) && <div className="kpi-sub">{delta}{sub}</div>}
  </div>;
}
export const Delta = ({ v, goodWhenDown, fmt }) => { if (v == null || !isFinite(v)) return null; const flat = Math.abs(v) < 1e-9; const bad = !flat && (goodWhenDown ? v > 0 : v < 0); return <span className={'delta ' + (flat ? 'flat' : bad ? 'bad' : 'good')}>{v > 0 ? '+' : ''}{fmt ? fmt(v) : v}</span>; };

export function Seg({ options, value, onChange, label }) {
  return <div className="fgrp">{label && <span className="l">{label}</span>}<div className="seg" role="group" aria-label={label}>{options.map(o => { const [k, l, dis] = Array.isArray(o) ? o : [o, o]; return <button key={k} className={value === k ? 'on' : ''} aria-pressed={value === k} disabled={dis} onClick={() => onChange(k)}>{l}</button>; })}</div></div>;
}
export function Card({ title, sub, right, children, flush }) {
  return <section className="card"><header className="card-h"><div><h3>{title}</h3>{sub && <p>{sub}</p>}</div>{right}</header><div className={flush ? 'card-b flush' : 'card-b'}>{children}</div></section>;
}
export function Tabs({ tabs, value, onChange }) {
  return <div className="tabs" role="tablist">{tabs.map(t => <button key={t.key} role="tab" aria-selected={value === t.key} className={'tab' + (value === t.key ? ' on' : '')} onClick={() => onChange(t.key)}>{t.label}{t.count != null && <span className="n">{t.count}</span>}{t.extra && <span className="sev">{t.extra}</span>}</button>)}</div>;
}
export function PageHead({ title, sub, crumbs, right }) {
  const { go } = useApp();
  return <div className="ph"><div>{crumbs && <div className="crumbs">{crumbs.map((c, i) => <Fragment key={i}>{i > 0 && ' / '}{c.to ? <button onClick={() => go(c.to)}>{c.label}</button> : c.label}</Fragment>)}</div>}<h1>{title}</h1>{sub && <p>{sub}</p>}</div>{right}</div>;
}
export function DataTable({ cols, rows, onRow, sort: init, rowKey, empty, maxH, selKey }) {
  const [sort, setSort] = useState(init || null);
  const sorted = useMemo(() => {
    if (!sort) return rows; const c = cols.find(x => x.key === sort.key); if (!c) return rows;
    const f = c.sort || (r => r[c.key]);
    return [...rows].sort((a, b) => { const x = f(a), y = f(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * sort.dir; });
  }, [rows, sort]); // eslint-disable-line
  return <div className="tbl-wrap" style={{ maxHeight: maxH }}><table className="tbl">
    <thead><tr>{cols.map(c => <th key={c.key} className={(c.ns ? 'ns ' : '') + (c.num ? 'num' : '')} style={{ width: c.w }} aria-sort={sort && sort.key === c.key ? (sort.dir > 0 ? 'ascending' : 'descending') : undefined}
      onClick={c.ns ? undefined : () => setSort(s => (s && s.key === c.key ? { key: c.key, dir: -s.dir } : { key: c.key, dir: c.desc ? -1 : 1 }))}>{c.label}{sort && sort.key === c.key && <span className="sort">{sort.dir > 0 ? '▲' : '▼'}</span>}</th>)}</tr></thead>
    <tbody>{sorted.length ? sorted.map((r, i) => { const k = rowKey ? rowKey(r) : i; return <tr key={k} className={(onRow ? 'click ' : '') + (selKey != null && k === selKey ? 'sel' : '')} tabIndex={onRow ? 0 : undefined}
      onClick={onRow ? () => onRow(r) : undefined} onKeyDown={onRow ? e => { if (e.key === 'Enter') onRow(r); } : undefined}>{cols.map(c => <td key={c.key} className={(c.num ? 'num ' : '') + (c.cls || '')}>{c.render ? c.render(r) : r[c.key]}</td>)}</tr>; })
      : <tr><td colSpan={cols.length} className="empty">{empty || 'Nothing matches these filters. Clear a filter to see more rows.'}</td></tr>}</tbody>
  </table></div>;
}
export const NodeLink = ({ id, label }) => { const { go } = useApp(); if (!id || !NODE[id]) return <span>{label}</span>; return <button className="linkish" onClick={e => { e.stopPropagation(); go('node', id); }}>{label || NODE[id].name}</button>; };

export const WINDOWS = [['7', 'Last 7 days'], ['15', 'Last 15 days']];
export const runsInWindow = days => RUNS.filter(r => r.t >= AS_OF - +days * MS.d);
