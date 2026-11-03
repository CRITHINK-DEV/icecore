/* Lays out a lineage graph off the main thread, so a large graph never blocks the tab. */
import { layoutCore } from './layout.js';

self.onmessage = e => {
  const { seq, ids, edges } = e.data;
  try { self.postMessage({ seq, pos: layoutCore(ids, edges) }); } catch (err) { self.postMessage({ seq, error: String(err && err.message || err) }); }
};
