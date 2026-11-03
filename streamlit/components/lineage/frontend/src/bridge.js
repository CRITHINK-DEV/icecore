// Minimal Streamlit component protocol (no streamlit-component-lib): ready, render (args + theme), value, frame height.
const send = (type, extra = {}) => window.parent.postMessage({ isStreamlitMessage: true, type, ...extra }, "*");

export const setComponentValue = (value) => send("streamlit:setComponentValue", { value, dataType: "json" });
export const setFrameHeight = (height) => send("streamlit:setFrameHeight", { height });

export function onRender(callback) {
  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "streamlit:render") callback(event.data.args || {}, event.data.theme || {});
  });
  send("streamlit:componentReady", { apiVersion: 1 });
}
