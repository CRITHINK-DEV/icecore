import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import ReactFlow, { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlowProvider, useReactFlow } from "reactflow";
import "reactflow/dist/style.css";
import "./style.css";
import { onRender, setComponentValue, setFrameHeight } from "./bridge.js";
import { NODE_H, NODE_W, adjacency, layout, walk } from "./graph.js";

const FALLBACK = { failed: "#c62828", warning: "#b26a00", ok: "#2e7d32", late: "#6a4fb3", not_run: "#9e9e9e" };
const LABELS = { failed: "Failed", warning: "Warning", ok: "OK", late: "Late", not_run: "Not run" };

function IceNode({ data }) {
  const cls = ["ice-node", data.dim && "dim", data.hl && "hl", data.sel && "sel", data.root && "root", data.trail && "trail", data.match && "match"]
    .filter(Boolean).join(" ");
  return (
    <div className={cls} style={{ "--sc": data.colour }} title={`${data.id}\n${LABELS[data.status] || data.status}`}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span className="bar" />
      {data.root && <span className="tag">Root cause</span>}
      <div className="nm">{data.label}</div>
      <div className="meta">
        <span>{data.type}{data.layer ? ` · ${data.layer}` : ""}</span>
        {data.tests_failing > 0 && <span className="nb">{data.tests_failing} failing</span>}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}
const nodeTypes = { ice: IceNode };

function Canvas({ args, theme }) {
  const rf = useReactFlow();
  const colours = { ...FALLBACK, ...(args.colours || {}) };
  const compact = !!args.compact;
  const allNodes = args.nodes || [];
  const allEdges = args.edges || [];
  const layers = useMemo(() => [...new Set(allNodes.map((n) => n.layer).filter(Boolean))].sort(), [allNodes]);
  const [hidden, setHidden] = useState([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(args.selected || args.focus || null);

  useEffect(() => { setSelected(args.selected || args.focus || null); }, [args.focus, args.selected]);

  const visible = useMemo(() => allNodes.filter((n) => !n.layer || !hidden.includes(n.layer)), [allNodes, hidden]);
  const ids = useMemo(() => new Set(visible.map((n) => n.id)), [visible]);
  const edges = useMemo(() => allEdges.filter((e) => ids.has(e.source) && ids.has(e.target)), [allEdges, ids]);
  const pos = useMemo(() => layout(visible, edges), [visible, edges]);
  const { up, down } = useMemo(() => adjacency(edges), [edges]);

  const trail = args.rootCausePath || [];
  const trailSet = useMemo(() => new Set(trail), [trail]);
  const trailEdges = useMemo(() => new Set(trail.slice(1).map((t, i) => `${trail[i]}>${t}`)), [trail]);
  const lit = useMemo(() => {
    if (!selected || !ids.has(selected)) return null;
    return new Set([selected, ...walk(selected, up), ...walk(selected, down)]);
  }, [selected, ids, up, down]);
  const q = query.trim().toLowerCase();
  const matches = useMemo(() => (q ? visible.filter((n) => n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q)) : []),
    [q, visible]);
  const matchSet = useMemo(() => new Set(matches.map((n) => n.id)), [matches]);

  const nodes = useMemo(() => visible.map((n) => ({
    id: n.id, type: "ice", position: pos[n.id], draggable: false, connectable: false,
    data: { ...n, colour: colours[n.status] || colours.not_run, sel: selected === n.id,
      root: trail.length > 0 && trail[0] === n.id, trail: trailSet.has(n.id),
      dim: (!!lit && !lit.has(n.id)) || (trail.length > 0 && !trailSet.has(n.id) && !lit) || (q && !matchSet.has(n.id)),
      hl: !!lit && lit.has(n.id), match: matchSet.has(n.id) },
  })), [visible, pos, selected, lit, trail, trailSet, q, matchSet]); // eslint-disable-line

  const rfEdges = useMemo(() => {
    const list = edges.map((e) => {
      const key = `${e.source}>${e.target}`;
      const red = trailEdges.has(key);
      const on = !red && !!lit && lit.has(e.source) && lit.has(e.target);
      const stroke = red ? colours.failed : on ? theme.primaryColor || "#3b5bdb" : theme.textColor || "#888";
      const faded = !red && ((lit && !on) || (trail.length > 0 && !lit));
      return { id: key, source: e.source, target: e.target, animated: red,
        style: { stroke, strokeWidth: red ? 2.6 : on ? 2 : 1.1, opacity: faded ? 0.12 : red || on ? 1 : 0.45 },
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: stroke } };
    });
    // a root-cause hop that is not an edge of the graph (a traced cause) is drawn dashed
    trail.slice(1).forEach((t, i) => {
      const key = `${trail[i]}>${t}`;
      if (ids.has(trail[i]) && ids.has(t) && !list.some((e) => e.id === key))
        list.push({ id: key, source: trail[i], target: t, animated: true,
          style: { stroke: colours.failed, strokeWidth: 2.6, strokeDasharray: "6 4" },
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: colours.failed } });
    });
    return list;
  }, [edges, lit, trail, trailEdges, ids, theme]); // eslint-disable-line

  useEffect(() => {
    const t = setTimeout(() => rf.fitView({ padding: 0.15, duration: 250, maxZoom: compact ? 1 : 1.4 }), 40);
    return () => clearTimeout(t);
  }, [visible.length, edges.length, compact]); // eslint-disable-line
  useEffect(() => {
    const target = trail[0] || args.focus;
    if (target && pos[target]) rf.setCenter(pos[target].x + NODE_W / 2, pos[target].y + NODE_H / 2, { zoom: compact ? 1 : 0.9, duration: 350 });
  }, [args.focus, trail.join(">")]); // eslint-disable-line

  const pick = (id) => { setSelected(id); setComponentValue(id); };
  const toggle = (l) => setHidden((h) => (h.includes(l) ? h.filter((x) => x !== l) : [...h, l]));
  const jump = () => {
    const m = matches[0];
    if (m && pos[m.id]) rf.setCenter(pos[m.id].x + NODE_W / 2, pos[m.id].y + NODE_H / 2, { zoom: 1.1, duration: 350 });
  };

  return (
    <div className="shell">
      {!compact && (
        <div className="bar-tools">
          <input className="search" placeholder="Find a node" value={query} aria-label="Find a node"
            onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && jump()} />
          {layers.length > 0 && (
            <div className="chips" role="group" aria-label="Layers">
              {layers.map((l) => (
                <button key={l} className={hidden.includes(l) ? "chip off" : "chip"} aria-pressed={!hidden.includes(l)}
                  onClick={() => toggle(l)}>{l}</button>
              ))}
            </div>
          )}
          <div className="legend">
            {Object.keys(LABELS).map((s) => <span key={s}><i style={{ background: colours[s] }} />{LABELS[s]}</span>)}
          </div>
        </div>
      )}
      <div className="canvas">
        <ReactFlow nodes={nodes} edges={rfEdges} nodeTypes={nodeTypes} minZoom={0.1} maxZoom={2} fitView
          nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
          onlyRenderVisibleElements={allNodes.length > 400} proOptions={{ hideAttribution: true }}
          onNodeClick={(_, n) => pick(n.id)} onPaneClick={() => pick(null)}>
          <Background gap={18} size={1} color={theme.secondaryBackgroundColor || "#ddd"} />
          {!compact && <MiniMap pannable zoomable nodeColor={(n) => n.data.colour} nodeStrokeWidth={0} style={{ width: 150, height: 96 }} />}
          <Controls showInteractive={false} position={compact ? "top-right" : "bottom-left"} />
        </ReactFlow>
      </div>
    </div>
  );
}

function App() {
  const [state, setState] = useState(null);
  useEffect(() => { onRender((args, theme) => setState({ args, theme })); }, []);
  const height = state?.args?.height || 640;
  useEffect(() => { if (state) setFrameHeight(height); }, [state, height]);
  useEffect(() => {
    if (!state) return;
    const t = state.theme;
    const dark = t.base === "dark";
    const r = document.documentElement.style;
    r.setProperty("--bg", t.backgroundColor || (dark ? "#0e1117" : "#ffffff"));
    r.setProperty("--panel", t.secondaryBackgroundColor || (dark ? "#262730" : "#f0f2f6"));
    r.setProperty("--ink", t.textColor || (dark ? "#fafafa" : "#31333f"));
    r.setProperty("--accent", t.primaryColor || "#3b5bdb");
    r.setProperty("--line", dark ? "rgba(250,250,250,.18)" : "rgba(49,51,63,.18)");
    const SANS = '-apple-system, "Source Sans Pro", "Source Sans 3", "Segoe UI", sans-serif';
    const f = (t.font || "").toLowerCase();
    r.setProperty("--font", f === "monospace" ? 'ui-monospace, "Source Code Pro", monospace' : f === "serif" ? 'Georgia, "Source Serif Pro", serif'
      : t.font && !/sans/.test(f) ? `"${t.font}", ${SANS}` : SANS);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [state]);
  if (!state) return null;
  return (
    <div style={{ height }}>
      <ReactFlowProvider><Canvas args={state.args} theme={state.theme} /></ReactFlowProvider>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
