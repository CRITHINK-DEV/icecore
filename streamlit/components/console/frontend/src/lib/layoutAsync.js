/* layoutAsync(ids, edges) -> Promise<pos>: dagre in a web worker; when a worker cannot start (a host that forbids
   workers), the same layout runs on the main thread after the current frame has painted. */
import { layoutCore } from './layout.js';

let worker = null, failed = false, seq = 0;
const waiting = new Map();

function getWorker() {
  if (worker || failed) return worker;
  try {
    worker = new Worker(new URL('./layout.worker.js', import.meta.url));
    worker.onmessage = e => { const w = waiting.get(e.data.seq); if (!w) return; waiting.delete(e.data.seq); e.data.error ? w.reject(new Error(e.data.error)) : w.resolve(e.data.pos); };
    worker.onerror = () => { failed = true; worker = null; const pending = [...waiting.values()]; waiting.clear(); pending.forEach(w => w.fallback()); };
  } catch (e) { failed = true; worker = null; }
  return worker;
}

const onMainThread = (ids, edges) => new Promise(resolve => setTimeout(() => resolve(layoutCore(ids, edges)), 16));

export function layoutAsync(ids, edges) {
  const w = getWorker();
  if (!w) return onMainThread(ids, edges);
  return new Promise((resolve, reject) => {
    const n = ++seq;
    waiting.set(n, { resolve, reject, fallback: () => onMainThread(ids, edges).then(resolve, reject) });
    w.postMessage({ seq: n, ids, edges });
  });
}
