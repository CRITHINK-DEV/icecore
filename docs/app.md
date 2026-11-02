# The icecore app

The app has six pages: Runs, Tests and anomalies, Lineage, Cost, SLAs and Settings. It reads only the icecore views (the [views reference](reference/views.md)), never the event table. You can run it in two places.

## Run the app on your laptop

This is the way we recommend for developers who want to browse their own observability.

**Why**

- No warehouse stays up for the app. A Streamlit in Snowflake object needs a warehouse and a running app in the account; a local run needs neither. The app only holds a Snowflake session while you use it.
- It reads only the views. Nothing is installed in the account for the app, and it writes nothing (the acknowledgement of an alert is the one write, through the `ICECORE_ACK` procedure, and only when you click it).
- It costs only the queries you make: each page runs a few small selects on the views, and results are cached for a few minutes.

**How**

1. Install the packages (Python 3.10 to 3.13):

   ```bash
   python3 -m venv .venv-app
   .venv-app/bin/pip install -r streamlit/requirements-local.txt
   ```

2. Tell the app how to log in. Use one of:

   - a connection name: `export SNOWFLAKE_CONNECTION_NAME=dev`, an entry of your `connections.toml`. The Snowflake library reads that file; the app never opens it.
   - a key pair: `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY_PATH` (and `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` if the key has one).
   - the browser login: `SNOWFLAKE_ACCOUNT` and `SNOWFLAKE_USER` only. Snowflake opens a browser window.

   `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE` and `SNOWFLAKE_DATABASE` are optional. The role needs USAGE on the database and the views schema, and SELECT on the views. The warehouse is used only while a page loads.

3. Say where the views are, then start it:

   ```bash
   export ICECORE_VIEWS_SCHEMA=MY_DB.ICECORE_OBS    # default: <your database>.ICECORE_OBS
   make app                                          # or scripts/run_app_locally.sh
   ```

   The app is at <http://localhost:8501>. `PORT=8600 make app` changes the port. You can also change the schema in the sidebar (Views schema).

If the browser console shows 404s for `/<page>/_stcore/health` when you open a page by its URL, that is Streamlit trying the page path as a base path before falling back to `/`: the page still loads. `scripts/smoke_app_local.sh` checks health and every page URL on a running app.

With no login configured the pages open and show their empty state, as in the tests.

**Try it with demo data.** `make demo-data` (`scripts/demo_data.sh`) runs a copy of [examples/shop](../examples/shop/README.md) several times into a demo schema (`<DB>.ICECORE_DEMO` for the models, `<DB>.ICECORE_DEMO_OBS` for the icecore views): one run with a failing test, a jump in row count after eight runs (recorded as a metric; the tests do not score anomalies yet, and the Tests and anomalies page is not built yet), a `meta.sla` on one model and `cost: true`. It needs a dbt-snowflake environment and the login variables above, and creates objects only in those two schemas. The Cost page fills a few hours later: Snowflake publishes query attribution with a lag, and a later dbt run writes the credits it finds.

## Deploy it to Snowflake

If your team wants one shared URL, deploy the same `streamlit/` folder as a Streamlit in Snowflake object from a Snowflake Git repository (`CREATE STREAMLIT ... FROM '@<repo>/tags/v<version>/streamlit'`; packages are in `streamlit/environment.yml`). Inside Snowflake the app uses the active session, so none of the variables above apply. The cost is a warehouse for the app, running while someone has it open. Local and deployed show the same pages.
