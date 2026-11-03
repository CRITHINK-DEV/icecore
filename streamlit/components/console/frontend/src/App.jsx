import React, { useState, useMemo, useEffect, Component } from 'react';
import { sendAction } from './streamlit.js';
import { parseT, fmtTs } from './lib/format.js';
import { NOT_RECORDED, D, RUNS, LAST, AS_OF, NODE, ASSETS, FAILS, SLAS, SLA_BY, SCHEMA, latestTest, incAsset, computeStatus, computeHealth } from './lib/model.js';
import { Ctx, useApp } from './lib/charts.jsx';
import { Ic, ICON, StatusPill, TestPill } from './components/ui.jsx';
import { Overview } from './pages/Overview.jsx';
import { Incidents } from './pages/Incidents.jsx';
import { Tests } from './pages/Tests.jsx';
import { Runs } from './pages/Runs.jsx';
import { Lineage } from './pages/Lineage.jsx';
import { Cost } from './pages/Cost.jsx';
import { Freshness } from './pages/Freshness.jsx';
import { Schema } from './pages/Schema.jsx';
import { Models } from './pages/Models.jsx';
import { NodePage } from './pages/NodePage.jsx';
import { Settings } from './pages/Settings.jsx';

/* ---------------- shell ---------------- */
export function parseHash() {
  const h = location.hash.replace(/^#\/?/, ''); const [path, qs] = h.split('?'); const parts = (path || '').split('/').filter(Boolean);
  const q = {}; new URLSearchParams(qs || '').forEach((v, k) => { q[k] = v; });
  return { page: parts[0] || 'overview', arg: parts.slice(1).map(decodeURIComponent).join('/'), q };
}
export const hashFor = (page, arg, q) => { let h = '#/' + page + (arg ? '/' + encodeURIComponent(arg) : ''); if (q) { const s = new URLSearchParams(Object.entries(q).filter(([, v]) => v != null && v !== '')).toString(); if (s) h += '?' + s; } return h; };

export function GlobalSearch() {
  const { go, status } = useApp();
  const [q, setQ] = useState(''); const [i, setI] = useState(0);
  const res = q ? D.nodes.filter(n => n.name.toLowerCase().includes(q.toLowerCase())).sort((a, b) => (a.type === 'test') - (b.type === 'test') || a.name.length - b.name.length).slice(0, 10) : [];
  const pick = n => { go('node', n.unique_id); setQ(''); };
  return <div className="gsearch">
    <Ic d={ICON.search} s={14} />
    <input placeholder="Search models, sources, tests" value={q} aria-label="Search nodes" onChange={e => { setQ(e.target.value); setI(0); }}
      onKeyDown={e => { if (e.key === 'ArrowDown') { setI(x => Math.min(x + 1, res.length - 1)); e.preventDefault(); } else if (e.key === 'ArrowUp') { setI(x => Math.max(x - 1, 0)); e.preventDefault(); } else if (e.key === 'Enter' && res[i]) pick(res[i]); else if (e.key === 'Escape') setQ(''); }} />
    {q && <div className="res" role="listbox">{res.length ? res.map((n, k) => <button key={n.unique_id} className={k === i ? 'on' : ''} role="option" aria-selected={k === i} onMouseDown={e => { e.preventDefault(); pick(n); }}>
      <span>{n.name}</span><span className="muted">{n.type === 'test' ? <TestPill s={latestTest(n.unique_id)} /> : <StatusPill s={status[n.unique_id].status} />}</span></button>)
      : <div className="muted" style={{ padding: 8 }}>No node named like “{q}”.</div>}</div>}
  </div>;
}

/* A section that throws shows its error instead of blanking the whole console. */
class Boundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() { return this.state.error ? <div className="page"><div className="empty-state"><b>This section could not be drawn</b>{String(this.state.error && this.state.error.message || this.state.error)}</div></div> : this.props.children; }
}

/* dv: data version (a new payload from Python); notice: {text, seq} toast text from Python (e.g. the ack result). */
export function App({ dv = 0, notice = null }) {
  const [route, setRoute] = useState(parseHash());
  const [theme, setTheme] = useState(document.documentElement.dataset.theme || 'light');
  const initIncidents = () => D.incidents.map(i => ({ ...i, state: ['open', 'acknowledged', 'resolved'].includes(i.state) ? i.state : i.acknowledged_by ? 'acknowledged' : 'open', resolvedAt: i.resolved_at ? parseT(i.resolved_at) : null }));
  const [incidents, setIncidents] = useState(initIncidents);
  const [toast, setToast] = useState(null);
  /* New data or a server notice: incidents come back from the views (drops this tab's Resolve/Reopen and any acknowledgement the server refused). */
  useEffect(() => { setIncidents(initIncidents()); }, [dv, notice && notice.seq]); // eslint-disable-line
  useEffect(() => { if (notice && notice.text) setToast(notice.text); }, [notice && notice.seq]); // eslint-disable-line
  useEffect(() => { const f = () => { setRoute(parseHash()); }; window.addEventListener('hashchange', f); return () => window.removeEventListener('hashchange', f); }, []);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 3200); return () => clearTimeout(t); }, [toast]);
  const go = (page, arg, q) => { location.hash = hashFor(page, arg, q); const m = document.querySelector('.main'); if (m && page !== route.page) m.scrollTop = 0; };
  const nav = (page, arg, q) => { history.replaceState(null, '', hashFor(page, arg, q)); setRoute(parseHash()); };
  const toggleTheme = () => { const t = theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = t; try { localStorage.setItem('icecore-theme', t); } catch (e) { } setTheme(t); };
  const act = (key, state) => {
    if (state === 'acknowledged') sendAction('ack', { incident_key: key });
    setIncidents(list => list.map(i => i.incident_key !== key ? i : { ...i, state, acknowledged_by: state === 'acknowledged' ? (i.acknowledged_by || 'you') : state === 'open' ? null : i.acknowledged_by, resolvedAt: state === 'resolved' ? AS_OF : null }));
    const i = incidents.find(x => x.incident_key === key);
    setToast(`${state === 'acknowledged' ? 'Acknowledged' : state === 'resolved' ? 'Resolved' : 'Reopened'}: ${i ? i.title : key}`);
  };
  const status = useMemo(() => computeStatus(incidents), [incidents]);
  const health = useMemo(() => computeHealth(incidents), [incidents]);
  const ctx = { route, go, nav, theme, incidents, act, status, health, dv };
  const openN = incidents.filter(i => i.state === 'open').length;
  const late = SLAS.filter(s => s.late).length + incidents.filter(i => i.kind === 'silence' && i.state !== 'resolved' && !(SLA_BY[incAsset(i)] || {}).late).length;
  const items = [
    ['overview', 'Overview', ICON.overview],
    ['incidents', 'Incidents', ICON.incidents, openN, 'fail'],
    ['tests', 'Tests & anomalies', ICON.tests, FAILS[LAST] || null, 'fail'],
    ['runs', 'Runs', ICON.runs, RUNS[LAST] && RUNS[LAST].status !== 'success' ? '!' : null, 'fail'],
    ['freshness', 'Schedules & freshness', ICON.fresh, late || null, 'stale'],
    ['schema', 'Schema changes', ICON.schema, SCHEMA.length || null, 'warn'],
    ['cost', 'Cost', ICON.cost],
    null,
    ['lineage', 'Lineage', ICON.lineage],
    ['models', 'Models & sources', ICON.models, ASSETS.length, ''],
    null,
    ['settings', 'Settings & about', ICON.settings],
  ];
  const active = route.page === 'node' ? (NODE[route.arg] && NODE[route.arg].type === 'test' ? 'tests' : 'models') : route.page;
  let page;
  switch (route.page) {
    case 'incidents': page = <Incidents />; break;
    case 'tests': page = <Tests />; break;
    case 'runs': page = <Runs />; break;
    case 'lineage': page = <Lineage />; break;
    case 'cost': page = <Cost />; break;
    case 'freshness': page = <Freshness />; break;
    case 'schema': page = <Schema />; break;
    case 'models': page = <Models />; break;
    case 'node': page = <NodePage id={route.arg} />; break;
    case 'settings': page = <Settings />; break;
    default: page = <Overview />;
  }
  return <Ctx.Provider value={ctx}>
    <div className="app">
      <header className="top">
        <div className="brand"><Ic d={ICON.core} s={18} />icecore</div>
        <div className="proj"><b>{D.project}</b>{RUNS[LAST] && RUNS[LAST].target ? <span className="env">{RUNS[LAST].target}</span> : null}</div>
        <div className="grow" />
        <GlobalSearch />
        <span className="asof" title="Latest data in this report">Data as of {fmtTs(AS_OF)}</span>
        <button className="iconbtn" onClick={() => { sendAction('refresh'); setToast('Reading the views again…'); }} aria-label="Refresh data" title="Refresh data"><Ic d={ICON.refresh} s={15} /></button>
        <button className="iconbtn" onClick={toggleTheme} aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} title={theme === 'dark' ? 'Light theme' : 'Dark theme'}><Ic d={theme === 'dark' ? ICON.sun : ICON.moon} s={15} /></button>
      </header>
      <nav className="side" aria-label="Sections">
        {items.map((it, k) => it ? <button key={it[0]} className={'nav' + (active === it[0] ? ' on' : '')} aria-current={active === it[0] ? 'page' : undefined} onClick={() => go(it[0])}>
          <Ic d={it[2]} />{it[1]}{it[3] != null && <span className={'cnt ' + (it[4] || '')}>{it[3]}</span>}</button> : <hr key={'h' + k} />)}
        <div className="foot">Views read at {D.generated_at ? fmtTs(parseT(D.generated_at)) : NOT_RECORDED}. Acknowledge is recorded in icecore; Resolve and Reopen are kept in this tab only.</div>
      </nav>
      <main className="main" key={route.page + '/' + route.arg}>
        {D.errors.length > 0 && <div className="errbar" role="alert"><b>Some views could not be read; their sections show what is recorded so far.</b>{D.errors.map((e, k) => <div key={k}><span className="mono">{String(e && e.view != null ? e.view : 'view')}</span>: {String(e && e.error != null ? e.error : 'unknown error')}</div>)}</div>}
        <Boundary key={dv}>{page}</Boundary>
      </main>
    </div>
    {toast && <div className="toast" role="status">{toast}</div>}
  </Ctx.Provider>;
}
