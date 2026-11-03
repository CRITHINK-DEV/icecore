import React, { useState, useMemo, useEffect } from 'react';
import ReactFlowView, { MiniMap, Controls, Background, Handle, Position, MarkerType, useReactFlow } from 'reactflow';
import dagre from 'dagre';
import { MS, fmtSec, fmtSpan, fmtCr, sum, rcKind } from '../lib/format.js';
import { LAST, NODE, ASSETS, PARENTS, CHILDREN, TESTS_OF, TRF, EX, SLA_BY, FRESH, isBad, latestIdx, latestTest, LAYER_GROUP, LAYER_LABEL, LAYER_ORDER, STATUS_LABEL, STATUS_TONE, incAsset, declaredRoot, NOT_RECORDED } from '../lib/model.js';
import { useApp, pal, statusColor } from '../lib/charts.jsx';
import { Ic, ICON, StatusPill, Layer, Kpi, Seg, PageHead } from '../components/ui.jsx';

/* ---------------- Lineage (the one custom component) ---------------- */
export const NODE_W = 196, NODE_H = 48;
export function layoutGraph(ids) {
  const g = new dagre.graphlib.Graph(); g.setGraph({ rankdir: 'LR', nodesep: 14, ranksep: 64, marginx: 20, marginy: 20 }); g.setDefaultEdgeLabel(() => ({}));
  const set = new Set(ids); const edges = [];
  ids.forEach(id => g.setNode(id, { width: NODE_W, height: NODE_H }));
  ids.forEach(id => (PARENTS[id] || []).forEach(p => { if (set.has(p)) { g.setEdge(p, id); edges.push([p, id]); } }));
  dagre.layout(g);
  const pos = {}; ids.forEach(id => { const n = g.node(id); pos[id] = { x: n.x - NODE_W / 2, y: n.y - NODE_H / 2 }; });
  return { pos, edges };
}
export const walk = (id, map) => { const out = new Set(); const st = [id]; while (st.length) { const x = st.pop(); (map[x] || []).forEach(p => { if (!out.has(p)) { out.add(p); st.push(p); } }); } return out; };
export const ancestors = id => walk(id, PARENTS);
export const descendants = id => walk(id, CHILDREN);
export const hood = (id, hops) => { const out = new Set([id]); let f = [id]; for (let h = 0; h < hops; h++) { const nx = []; f.forEach(x => [...(PARENTS[x] || []), ...(CHILDREN[x] || [])].forEach(y => { if (!out.has(y)) { out.add(y); nx.push(y); } })); f = nx; } return [...out]; };
export function rootCause(id, status) {
  const bad = x => status[x] && ['failed', 'warn', 'stale'].includes(status[x].status);
  const anc = ancestors(id); const chain = [...anc, id].filter(bad);
  const roots = chain.filter(x => ![...ancestors(x)].some(bad));
  const path = new Set([...anc, id]); const dec = declaredRoot(id);
  if (dec) { path.add(dec.id); return { path, roots: [dec.id], impacted: descendants(id), declared: dec }; }
  return { path, roots: roots.length ? roots : [id], impacted: descendants(id) };
}
export function IceNode({ data }) {
  return <div className={`ice-node s-${data.status}${data.dim ? ' dim' : ''}${data.hl ? ' hl' : ''}${data.root ? ' root' : ''}${data.impacted ? ' impacted' : ''}${data.sel ? ' sel' : ''}`} title={`${data.name}: ${STATUS_LABEL[data.status]}`}>
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <span className="bar" />{data.root && <span className="tag r">Root cause</span>}{data.impacted && !data.root && <span className="tag i">Affected</span>}
    <div className="nm">{data.name}</div><span className="dot" />
    <div className="meta"><i style={{ background: `var(--${data.group}, var(--muted))` }} />{LAYER_LABEL[data.group] || data.group || NOT_RECORDED}{data.badge && <span className={'nb' + (data.fail ? ' f' : '')}>{data.badge}</span>}</div>
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </div>;
}
export const nodeTypes = { ice: IceNode };
export function FlowCtl({ fitKey, centerOn, pos }) {
  const rf = useReactFlow();
  useEffect(() => { const t = setTimeout(() => rf.fitView({ padding: 0.12, duration: 300 }), 30); return () => clearTimeout(t); }, [fitKey]); // eslint-disable-line
  useEffect(() => { if (centerOn && centerOn.id && pos[centerOn.id]) rf.setCenter(pos[centerOn.id].x + NODE_W / 2, pos[centerOn.id].y + NODE_H / 2, { zoom: 1.15, duration: 450 }); }, [centerOn]); // eslint-disable-line
  return null;
}
export function LineageCanvas({ ids, focus, mode, onSelect, compact, centerOn }) {
  const { status, theme } = useApp();
  const key = ids.join(',');
  const { pos, edges } = useMemo(() => layoutGraph(ids), [key]); // eslint-disable-line
  const hl = useMemo(() => {
    if (!focus || mode === 'none' || !ids.includes(focus)) return null;
    if (mode === 'root') { const rc = rootCause(focus, status); return { set: rc.path, root: new Set(rc.roots), impacted: rc.impacted, mode, declared: rc.declared }; }
    const set = new Set([focus]);
    if (mode === 'up' || mode === 'both') ancestors(focus).forEach(x => set.add(x));
    if (mode === 'down' || mode === 'both') descendants(focus).forEach(x => set.add(x));
    return { set, root: new Set(), impacted: new Set(), mode };
  }, [focus, mode, status, key]); // eslint-disable-line
  const p = useMemo(() => pal(), [theme]);
  const nodes = useMemo(() => ids.map(id => {
    const n = NODE[id]; const tids = TESTS_OF[id] || []; const ft = tids.filter(t => isBad(latestTest(t))).length;
    const imp = !!(hl && hl.impacted.has(id));
    return { id, type: 'ice', position: pos[id], draggable: false, connectable: false,
      data: { name: n.name, status: status[id] ? status[id].status : 'ok', group: LAYER_GROUP(n.layer), fail: ft > 0,
        badge: ft ? `${ft} failing test${ft > 1 ? 's' : ''}` : n.type === 'source' && FRESH[id] ? `data ${fmtSpan(FRESH[id].freshness_lag_min * MS.m)} old` : tids.length ? `${tids.length} tests` : n.type,
        dim: !!(hl && !hl.set.has(id) && !imp), hl: !!(hl && hl.set.has(id) && hl.mode !== 'root'), root: !!(hl && hl.root.has(id)), impacted: imp, sel: focus === id } };
  }), [key, pos, hl, status, focus]); // eslint-disable-line
  const rfEdges = useMemo(() => edges.map(([s, t]) => {
    const on = !!(hl && hl.set.has(s) && hl.set.has(t));
    const imp = !!(hl && hl.mode === 'root' && hl.impacted.has(t) && (hl.impacted.has(s) || s === focus));
    const col = on ? (hl.mode === 'root' ? p.fail : p.accent) : imp ? p.warn : p.ink3;
    return { id: s + '>' + t, source: s, target: t, animated: on && hl.mode === 'root',
      style: { stroke: col, strokeWidth: on || imp ? 2 : 1.1, opacity: hl && !on && !imp ? 0.2 : on || imp ? 1 : 0.55, strokeDasharray: imp ? '5 4' : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: col } };
  }).concat(hl && hl.declared && ids.includes(hl.declared.id) && hl.declared.id !== focus ? [{ id: 'rc-link', source: hl.declared.id, target: focus, animated: true, label: `root cause: ${rcKind(hl.declared.kind).toLowerCase() || 'traced'}`,
    labelStyle: { fill: p.fail, fontWeight: 600, fontSize: 11 }, labelBgStyle: { fill: p.panel }, labelBgPadding: [4, 2],
    style: { stroke: p.fail, strokeWidth: 2, strokeDasharray: '6 4' }, markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: p.fail } }] : []), [edges, hl, p, focus, ids]);
  return <ReactFlowView nodes={nodes} edges={rfEdges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.12 }} minZoom={0.15} maxZoom={2}
    nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
    onNodeClick={(e, n) => onSelect(n.id)} onPaneClick={() => onSelect(null)}>
    <Background gap={18} size={1} color={p.line} />
    {!compact && <MiniMap pannable zoomable nodeColor={n => statusColor(p, n.data.status)} nodeStrokeWidth={0} maskColor={theme === 'dark' ? 'rgba(10,14,20,.6)' : 'rgba(240,243,247,.65)'} style={{ width: 170, height: 110 }} />}
    <Controls showInteractive={false} position={compact ? 'top-right' : 'bottom-left'} />
    <FlowCtl fitKey={key} centerOn={centerOn} pos={pos} />
  </ReactFlowView>;
}

export function NodePanel({ id, mode, setMode, onClose }) {
  const { status, incidents, go } = useApp();
  const n = NODE[id]; const s = status[id]; const ex = EX[id] ? EX[id][LAST] : null; const tids = TESTS_OF[id] || [];
  const failing = tids.filter(t => isBad(latestTest(t)));
  const incs = incidents.filter(i => i.state !== 'resolved' && incAsset(i) === id);
  const rc = mode === 'root' ? rootCause(id, status) : null;
  return <div className="lpanel" role="dialog" aria-label={`Details of ${n.name}`}>
    <button className="iconbtn x" onClick={onClose} aria-label="Close panel"><Ic d={ICON.close} s={14} /></button>
    <h4>{n.name}</h4>
    <div className="hstack" style={{ marginTop: 6 }}><StatusPill s={s.status} /><Layer l={n.layer} /></div>
    {s.reasons.length > 0 && <div className="reasons">{s.reasons.map((r, i) => <span key={i}>{r}</span>)}</div>}
    <dl>
      <dt>Type</dt><dd>{n.type}{n.materialization && n.materialization !== n.type ? `, ${n.materialization}` : ''}</dd>
      <dt>Latest build</dt><dd>{ex ? `${fmtSec(ex.execution_time_s)}, ${ex.status}` : n.type === 'source' ? 'Not built (source)' : 'Not run'}</dd>
      <dt>Credits, latest</dt><dd>{ex ? (ex.credits == null ? 'Not yet attributed' : fmtCr(ex.credits)) : '—'}</dd>
      <dt>Tests</dt><dd>{tids.length ? `${tids.length - failing.length} of ${tids.length} passing` : 'None'}</dd>
      {FRESH[id] && <><dt>Source data age</dt><dd>{fmtSpan(FRESH[id].freshness_lag_min * MS.m)}{FRESH[id].max_lag_min != null ? ` (limit ${FRESH[id].max_lag_min} min)` : ''}</dd></>}
      {SLA_BY[id] && <><dt>Schedule</dt><dd>every {SLA_BY[id].expected_every}, {SLA_BY[id].late ? 'late' : 'on time'}</dd></>}
      <dt>Upstream / downstream</dt><dd>{ancestors(id).size} / {descendants(id).size}</dd>
    </dl>
    {failing.length > 0 && <div className="sec"><b>Failing tests</b><ul>{failing.map(t => <li key={t}><button className="linkish" onClick={() => go('node', t)}>{NODE[t].name}</button> ({TRF[t][latestIdx(t)]} rows)</li>)}</ul></div>}
    {incs.length > 0 && <div className="sec"><b>Open incidents</b><ul>{incs.map(i => <li key={i.incident_key}>{i.title}</li>)}</ul></div>}
    {rc && <div className="rc-box">{rc.declared ? <>icecore traced this failure to <b>{NODE[rc.declared.id].name}</b> ({rcKind(rc.declared.kind).toLowerCase() || 'traced cause'}). It is not a direct parent in the lineage, so the dashed red link shows the attribution.</> : rc.roots.length === 1 && rc.roots[0] === id
      ? <>No upstream node is failing, late or warning. The problem starts at <b>{n.name}</b>: check its own logic or the rows it receives.</>
      : <>First failing node upstream: <b>{rc.roots.map(r => NODE[r].name).join(', ')}</b>.</>}
      {' '}{rc.impacted.size ? <>Affected downstream: {[...rc.impacted].map(x => NODE[x].name).join(', ')}.</> : 'Nothing downstream is affected.'}</div>}
    <div className="acts">
      <button className="btn primary" onClick={() => go('node', id)}>Open node page</button>
      <div className="seg" role="group" aria-label="Highlight">{[['up', 'Upstream'], ['down', 'Downstream'], ['both', 'Both'], ['root', 'Root cause']].map(([k, l]) => <button key={k} className={mode === k ? 'on' : ''} aria-pressed={mode === k} onClick={() => setMode(mode === k ? 'none' : k)}>{l}</button>)}</div>
    </div>
  </div>;
}

export function Lineage() {
  const { status, route, nav } = useApp();
  const [groups, setGroups] = useState(new Set(LAYER_ORDER));
  const [q, setQ] = useState(''); const [centerOn, setCenterOn] = useState(null);
  const focus = route.q.focus || null; const mode = route.q.mode || (focus ? 'both' : 'none');
  const setFocus = (id, m) => nav('lineage', null, { focus: id || undefined, mode: id ? (m || (mode === 'none' ? 'both' : mode)) : undefined });
  const ids = useMemo(() => ASSETS.filter(n => { const g = LAYER_GROUP(n.layer); return groups.has(g) || !LAYER_ORDER.includes(g); }).map(n => n.unique_id), [groups]); // a layer outside the prototype's six is always shown
  const counts = {}; ASSETS.forEach(n => { const s = status[n.unique_id].status; counts[s] = (counts[s] || 0) + 1; });
  const failingNow = ASSETS.filter(n => status[n.unique_id].status === 'failed');
  const affected = new Set(); failingNow.forEach(n => descendants(n.unique_id).forEach(x => affected.add(x)));
  const toggle = g => setGroups(s => { const n = new Set(s); if (n.has(g)) n.delete(g); else n.add(g); return n.size ? n : new Set(LAYER_ORDER); });
  const matches = q ? ASSETS.filter(n => n.name.toLowerCase().includes(q.toLowerCase())).slice(0, 8) : [];
  const pick = id => { const g = LAYER_GROUP(NODE[id].layer); if (!groups.has(g) && LAYER_ORDER.includes(g)) setGroups(s => new Set([...s, g])); setFocus(id, mode === 'none' ? 'both' : mode); setCenterOn({ id, n: Date.now() }); setQ(''); };
  return <div className="page" style={{ paddingBottom: 20 }}>
    <PageHead title="Lineage" sub="Models and sources, left to right. Click a node for details and to highlight what feeds it, what it feeds, or the root-cause path of a failure." />
    <div className="kpis">
      <Kpi label="Nodes" value={ASSETS.length} sub={`${PARENTS && sum(ASSETS.map(n => PARENTS[n.unique_id].length))} dependencies`} />
      <Kpi label="Failed" value={counts.failed || 0} tone={counts.failed ? 'fail' : 'ok'} sub={failingNow.map(n => n.name).join(', ') || 'none'} />
      <Kpi label="Late or stale" value={counts.stale || 0} tone={counts.stale ? 'stale' : 'ok'} sub="missed schedule or old source data" />
      <Kpi label="Affected downstream" value={affected.size} tone={affected.size ? 'warn' : 'ok'} sub="nodes fed by a failed node" />
    </div>
    <div className="fbar lin-tools">
      <div className="fgrp"><span className="l">Layers</span><div className="seg" role="group" aria-label="Layers">{LAYER_ORDER.map(g => <button key={g} className={groups.has(g) ? 'on' : ''} aria-pressed={groups.has(g)} onClick={() => toggle(g)}>{LAYER_LABEL[g]}</button>)}</div></div>
      <div className="fgrp gsearch" style={{ width: 240 }}><Ic d={ICON.search} s={14} /><input className="inp" style={{ width: '100%', paddingLeft: 28 }} placeholder="Find a node" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && matches[0]) pick(matches[0].unique_id); }} aria-label="Find a node in the lineage" />
        {matches.length > 0 && <div className="res">{matches.map(m => <button key={m.unique_id} onClick={() => pick(m.unique_id)}><span>{m.name}</span><StatusPill s={status[m.unique_id].status} /></button>)}</div>}</div>
      <Seg label="Highlight" options={[['none', 'Off'], ['up', 'Upstream', !focus], ['down', 'Downstream', !focus], ['both', 'Both', !focus], ['root', 'Root cause', !focus]]} value={mode} onChange={m => nav('lineage', null, { focus: focus || undefined, mode: m })} />
    </div>
    <div className="lin-wrap">
      <div className="legend" aria-label="Status legend">
        {['failed', 'warn', 'stale', 'ok', 'notrun'].map(s => <div key={s} className="row"><i style={{ background: `var(--${STATUS_TONE[s] === 'muted' ? 'muted' : STATUS_TONE[s]})` }} />{STATUS_LABEL[s]}<b>{counts[s] || 0}</b></div>)}
        {failingNow.length > 0 && <div className="fl"><span className="muted">Trace root cause</span>{failingNow.map(n => <button key={n.unique_id} onClick={() => { pick(n.unique_id); nav('lineage', null, { focus: n.unique_id, mode: 'root' }); }}>{n.name}</button>)}</div>}
      </div>
      <LineageCanvas ids={ids} focus={focus} mode={mode} onSelect={id => setFocus(id)} centerOn={centerOn} />
      {focus && NODE[focus] && <NodePanel id={focus} mode={mode} setMode={m => nav('lineage', null, { focus, mode: m })} onClose={() => setFocus(null)} />}
    </div>
  </div>;
}
