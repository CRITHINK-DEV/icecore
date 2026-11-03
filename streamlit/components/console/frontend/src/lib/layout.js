/* The prototype's dagre layout (pages/Lineage.jsx layoutGraph), split so it can also run in a web worker.
   Same graph settings and the same order of setNode/setEdge calls, so the positions are identical. */
import dagre from 'dagre';

export const NODE_W = 196, NODE_H = 48;

export function layoutCore(ids, edges) {
  const g = new dagre.graphlib.Graph(); g.setGraph({ rankdir: 'LR', nodesep: 14, ranksep: 64, marginx: 20, marginy: 20 }); g.setDefaultEdgeLabel(() => ({}));
  ids.forEach(id => g.setNode(id, { width: NODE_W, height: NODE_H }));
  edges.forEach(([p, id]) => g.setEdge(p, id));
  dagre.layout(g);
  const pos = {}; ids.forEach(id => { const n = g.node(id); pos[id] = { x: n.x - NODE_W / 2, y: n.y - NODE_H / 2 }; });
  return pos;
}
