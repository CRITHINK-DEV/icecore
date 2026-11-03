/* Minimal Streamlit component protocol (no streamlit-component-lib), as in components/lineage: ready, render
   (args + theme), value, frame height. */
const send = (type, extra = {}) => window.parent.postMessage({ isStreamlitMessage: true, type, ...extra }, '*');

export const setComponentValue = value => send('streamlit:setComponentValue', { value, dataType: 'json' });
export const setFrameHeight = height => send('streamlit:setFrameHeight', { height });

export function onRender(callback) {
  window.addEventListener('message', event => {
    if (event.data && event.data.type === 'streamlit:render') callback(event.data.args || {}, event.data.theme || {});
  });
  send('streamlit:componentReady', { apiVersion: 1 });
}

/* Everything the server must act on goes through here: {action, ...fields, nonce}. The nonce makes two identical
   actions (acknowledge, then refresh twice) distinct values, so Python can tell a new action from the sticky last one. */
let seq = 0;
export const sendAction = (action, fields = {}) => {
  if (window.parent === window) return; // dev mode, no Streamlit host
  setComponentValue({ action, ...fields, nonce: `${Date.now()}-${++seq}` });
};
