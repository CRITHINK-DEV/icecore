import dagre from "dagre";

export const NODE_W = 188;
export const NODE_H = 46;

export function layout(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 14, ranksep: 60, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  const pos = {};
  nodes.forEach((n) => { const p = g.node(n.id); pos[n.id] = { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 }; });
  return pos;
}

export function adjacency(edges) {
  const up = {}, down = {};
  edges.forEach(({ source, target }) => {
    (up[target] ||= []).push(source);
    (down[source] ||= []).push(target);
  });
  return { up, down };
}

export function walk(start, map) {
  const out = new Set();
  const stack = [start];
  while (stack.length) {
    for (const next of map[stack.pop()] || []) if (!out.has(next)) { out.add(next); stack.push(next); }
  }
  return out;
}
