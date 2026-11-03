// Minimal Streamlit component protocol, no libraries.
var n = 0, errors = [];
function send(type, data) {
  window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data), "*");
}
function report(extra) {
  send("streamlit:setComponentValue", {
    value: Object.assign({clicks: n, errors: errors, ua: navigator.userAgent, origin: location.origin}, extra),
    dataType: "json"
  });
}
document.addEventListener("securitypolicyviolation", function (e) {
  errors.push("CSP " + e.violatedDirective + " " + e.blockedURI);
});
window.addEventListener("error", function (e) { errors.push("error " + e.message); });
var first = true;
window.addEventListener("message", function (ev) {
  if (!ev.data || ev.data.type !== "streamlit:render") return;
  document.getElementById("s").textContent = " rendered, label=" + ev.data.args.label;
  send("streamlit:setFrameHeight", {height: 50});
  if (first) { first = false; report({event: "rendered"}); }  // proves render without a click
});
document.getElementById("b").addEventListener("click", function () {
  n += 1;
  report({event: "click", node_id: "n1"});
});
send("streamlit:componentReady", {apiVersion: 1});
