# S14: custom component in Streamlit in Snowflake

Question: can Streamlit in Snowflake (warehouse runtime) load a component bundled as static files
(`components.declare_component(path=...)`) and send a click back to Python?

**Decision: React Flow in Snowflake: yes. A bundled custom component renders and returns values in Streamlit in Snowflake (warehouse runtime, Streamlit 1.50).**
The `st.graphviz_chart` fallback is not needed; the local build keeps React Flow too.

## Probe

`spikes/s14_component/`: `frontend/index.html` and `frontend/component.js` (Streamlit component protocol over
`postMessage`, no libraries, no CDN), `streamlit_app.py`, `environment.yml`, `snowflake.yml`.
The component sends a "rendered" value on render and a click value on click, and records CSP violations and JS errors.

## Result

| Item | Result |
|---|---|
| Renders | yes (button and "rendered, label=hello") |
| Click reaches Python | yes: first value `{"event":"rendered","clicks":0,"errors":[]}`, after one click `{"event":"click","clicks":1,"node_id":"n1","errors":[]}` |
| Runtime | warehouse (`SYSTEM$WAREHOUSE_RUNTIME`) |
| Streamlit / Python | 1.50.0 / 3.11.18 |
| Component origin | a `*.snowflake.app` host |
| CSP / console errors | none |

## Findings

- `snow streamlit deploy` failed at the upload step ("connection host not in the expected format"). Uploading with `PUT` to a stage and
  `CREATE STREAMLIT ... FROM @stage` worked.
- Opening the app needs USAGE on the STREAMLIT object for the viewer's role.
- Task 14 can ship the built React Flow bundle as static files and use the same value protocol (`node_id` back to Python).
