/* Entry point. In Streamlit the data comes from the component args (args.data, the ICECORE_DATA contract). Opened on its
   own, outside any frame (dev mode: `npm run dev`; or the build served next to a data file to compare screenshots), it
   loads window.ICECORE_DATA from the fixed relative file icecore-mock-data.js. Nothing is taken from the URL: a script
   source from the query string would run on the Streamlit app's (authenticated) origin. */
import React from 'react';
import { createRoot } from 'react-dom/client';
import 'reactflow/dist/style.css';
import './fonts.css';
import './console.css';
import './bundle.css';
import { loadData } from './lib/model.js';
import { App } from './App.jsx';
import { onRender, setFrameHeight } from './streamlit.js';

let root = null;
const mount = props => { root = root || createRoot(document.getElementById('root')); root.render(<App {...props} />); };

let stored = null;
try { const t = localStorage.getItem('icecore-theme'); if (t === 'dark' || t === 'light') { document.documentElement.dataset.theme = t; stored = t; } } catch (e) { }

const MOCK_FILE = 'icecore-mock-data.js'; // fixed: never read from the URL
if (window.parent === window) {
  const s = document.createElement('script');
  s.src = MOCK_FILE;
  s.onload = () => { loadData(window.ICECORE_DATA); mount({ dv: 1 }); };
  s.onerror = () => { document.getElementById('root').innerHTML = '<p style="padding:24px">Dev mode: the mock data file did not load (' + s.src + ').</p>'; };
  document.head.appendChild(s);
} else {
  let sig, dv = 0, lastNotice = null, notice = null, seq = 0, first = true;
  onRender((args, theme) => {
    setFrameHeight(Number(args.height) > 0 ? Number(args.height) : 900);
    if (first) { first = false; if (!stored && theme && theme.base === 'dark') document.documentElement.dataset.theme = 'dark'; }
    const next = JSON.stringify(args.data == null ? null : args.data);
    if (next !== sig) { sig = next; loadData(args.data); dv += 1; }
    const text = args.notice ? String(args.notice) : null;
    if (text && text !== lastNotice) notice = { text, seq: ++seq };
    lastNotice = text;
    mount({ dv, notice });
  });
}
