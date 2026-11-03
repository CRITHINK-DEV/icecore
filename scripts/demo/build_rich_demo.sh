#!/usr/bin/env bash
# Build a rich demo schema through the real path: real `dbt build` runs of a copy of examples/shop (plus the models of
# scripts/demo/rich_shop), icecore emitting to the event table, the views computing every result. No row is written by
# hand: history comes from repeated runs with the seeds varied from run to run (metrics are stamped with the time of
# the run, they cannot be backfilled through dbt).
#
# What the 15 builds produce (see the comments in the loop for the run numbers):
#   - 14 history points of the row count per volume-tested model, then a last build with the anomalies
#   - row-count anomalies: fct_orders (build 9, resolved afterwards), dim_customers and revenue_by_day (build 15, recent)
#   - a freshness breach (daily_revenue, built early, tested at the end) and one missed SLA (the same model)
#   - schema changes: column added on fct_orders (build 7), type changed on dim_customers (build 11)
#   - failed models (fct_payments, builds 5 and 6) and skipped ones (payments_summary; revenue_by_day in build 3)
#   - incidents: open (stale freshness, revenue_by_day anomaly), acknowledged (dim_customers anomaly, through the real
#     ICECORE_ACK procedure), resolved (the not_null failure of build 3, the fct_orders anomaly)
#
# Sandbox only: it refuses to run unless SNOWFLAKE_DATABASE is ICECORE_SANDBOX. It creates objects only in
# DEMO_SCHEMA (models) and DEMO_OBS_SCHEMA (icecore).
#
#   SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH, SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE  your login
#   SNOWFLAKE_DATABASE   must be ICECORE_SANDBOX (default)
#   DEMO_SCHEMA          default ICECORE_DEMO_PLUS;  DEMO_OBS_SCHEMA default <DEMO_SCHEMA>_OBS
#   DBT                  a dbt-snowflake (1.8 or later) executable, in an environment that has snowflake-connector-python
#   GAP                  seconds to wait between builds (the history delay is 1 minute), default 45
#   FORCE=1              rebuild although the demo is already there: both schemas are dropped and rebuilt. Records the
#                        event table already holds for these schema names stay, so use it rarely.
#
# Idempotent: when the obs schema already holds 14 or more row_count points of fct_orders, it prints so and stops.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
dbt="${DBT:-dbt}"
py="$(dirname "$(command -v "$dbt")")/python"
[ -x "$py" ] || py=python3
export SNOWFLAKE_DATABASE="${SNOWFLAKE_DATABASE:-ICECORE_SANDBOX}"
[ "$SNOWFLAKE_DATABASE" = "ICECORE_SANDBOX" ] || { echo "refusing to run: SNOWFLAKE_DATABASE is '$SNOWFLAKE_DATABASE', this script only runs on ICECORE_SANDBOX" >&2; exit 1; }
export SHOP_SCHEMA="${DEMO_SCHEMA:-ICECORE_DEMO_PLUS}"
obs="${DEMO_OBS_SCHEMA:-${SHOP_SCHEMA}_OBS}"
for name in "$SHOP_SCHEMA" "$obs"; do
  [[ "$name" =~ ^[A-Za-z0-9_]+$ ]] || { echo "schema name '$name' must use only letters, digits and underscores" >&2; exit 1; }
done
gap="${GAP:-45}"
for v in SNOWFLAKE_ACCOUNT SNOWFLAKE_USER SNOWFLAKE_PRIVATE_KEY_PATH SNOWFLAKE_ROLE SNOWFLAKE_WAREHOUSE; do
  [ -n "${!v:-}" ] || { echo "set $v (see the header of this script)" >&2; exit 1; }
done
sf() { "$py" "$here/sf.py" "$1"; }
db="$SNOWFLAKE_DATABASE"

# idempotent: stop when the demo is already built (the views may not exist yet: that reads as not built)
points="$(sf "select count(*) from $db.$obs.METRICS where metric = 'row_count' and relation ilike '%\"$SHOP_SCHEMA\".\"FCT_ORDERS\"'" 2>/dev/null || echo '[["0"]]')"
if [ "$points" != '[["0"]]' ] && [ -z "${FORCE:-}" ]; then
  n="${points//[^0-9]/}"
  if [ "${n:-0}" -ge 14 ]; then echo "demo already built in $db.$obs ($n row_count points of fct_orders). FORCE=1 rebuilds it."; exit 0; fi
fi
if [ -n "${FORCE:-}" ]; then
  sf "drop schema if exists $db.$SHOP_SCHEMA cascade" >/dev/null
  sf "drop schema if exists $db.$obs cascade" >/dev/null
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp -R "$root/examples/shop/." "$work/"
cp "$here/rich_shop/models/"*.sql "$work/models/marts/"
cp "$here/rich_shop/models/_marts.yml" "$work/models/marts/_marts.yml"
cd "$work"
"$py" - "$root" "$obs" <<'PY'
import sys
root, obs = sys.argv[1:3]
def edit(path, fn):
    text = open(path).read(); open(path, "w").write(fn(text))
edit("packages.yml", lambda t: t.replace("local: ../../", f"local: {root}"))
edit("dbt_project.yml", lambda t: t.replace("obs_schema: ICECORE_OBS", f"obs_schema: {obs}"))
PY
grep -q "$root" packages.yml && grep -q "obs_schema: $obs" dbt_project.yml

# raw_customers.csv and raw_orders.csv of build $1 (customers, orders and days of orders vary from build to build)
write_seeds() {
  "$py" - "$1" <<'PY'
import datetime
import sys
i = int(sys.argv[1])
orders = {9: 400}.get(i, 100 + [0, 2, -1, 1, 3, -2, 0, 1, 0, -1, 2, 0, 1, -2, 0][i - 1])
customers = 60 if i == 15 else 20 + [0, 1, -1, 0, 1, -1, 1, 0, -1, 1, 0, -1, 1, 0, 0][i - 1]
days = 40 if i == 15 else 14
countries = ["FR", "US", "FI", "GB", "DE", "ES"]
with open("seeds/raw_customers.csv", "w") as f:
    f.write("id,first_name,country\n")
    for c in range(1, customers + 1):
        f.write(f"{c},Customer{c},{countries[c % 6]}\n")
with open("seeds/raw_orders.csv", "w") as f:
    f.write("id,customer_id,status,amount,ordered_at\n")
    for o in range(1, orders + 1):
        status = ["completed", "completed", "completed", "placed", "returned"][o % 5]
        day = datetime.date(2026, 10, 31) - datetime.timedelta(days=o % days)
        f.write(f"{o},{1 + o % customers},{status},{10 + o % 40}.00,{day} {9 + o % 8}:00:00\n")
PY
}
vars_of() { printf '{demo_stage: %s, shop_fail: %s, demo_break_payments: %s}' "$1" "$2" "$3"; }
build() { "$dbt" build --profiles-dir . "$@" || echo "(build finished with failures, as intended)"; }

"$dbt" deps --profiles-dir .
"$dbt" run-operation icecore.setup --profiles-dir . --args "{schema: $obs, runs_per_day: 24}"

daily_revenue_last=0
for i in $(seq 1 15); do
  echo "== build $i of 15"
  write_seeds "$i"
  fail=false; brk=false
  [ "$i" = 3 ] && fail=true            # a not_null test fails, revenue_by_day is skipped; build 4 resolves it
  { [ "$i" = 5 ] || [ "$i" = 6 ]; } && brk=true   # fct_payments fails, payments_summary is skipped
  "$dbt" seed --profiles-dir . --full-refresh
  if [ "$i" -le 3 ]; then
    build --exclude resource_type:seed --vars "$(vars_of "$i" "$fail" "$brk")"
    daily_revenue_last=$(date +%s)
  else
    build --exclude resource_type:seed --exclude daily_revenue --vars "$(vars_of "$i" "$fail" "$brk")"
  fi
  [ "$i" -lt 15 ] && sleep "$gap"
done

# the freshness breach and the missed SLA: daily_revenue was last built in build 3; its test runs now, past max_lag (10m)
wait_s=$(( 12 * 60 - ($(date +%s) - daily_revenue_last) ))
[ "$wait_s" -gt 0 ] && { echo "== waiting ${wait_s}s so daily_revenue is past its 10 minute limits"; sleep "$wait_s"; }
echo "== freshness test of daily_revenue (breach)"
"$dbt" test --profiles-dir . --select daily_revenue --vars "$(vars_of 15 false false)" || echo "(failed as intended)"

# the acknowledged incident: the anomaly of dim_customers, acknowledged through ICECORE_ACK (waits out the ingestion lag)
echo "== acknowledging the dim_customers anomaly"
sleep 40
key=""
for attempt in 1 2 3 4 5 6; do
  out="$(sf "select incident_key from $db.$obs.ALERTS_FEED where kind = 'anomaly' and node_key ilike '%DIM_CUSTOMERS\"|row_count|%' and acknowledged_at is null limit 1")"
  key="$(echo "$out" | "$py" -c "import json,sys; r=json.load(sys.stdin); print(r[0][0] if r else '')")"
  [ -n "$key" ] && break
  sleep 20
done
[ -n "$key" ] || { echo "no open anomaly incident of dim_customers found to acknowledge" >&2; exit 1; }
"$dbt" run-operation icecore.acknowledge --profiles-dir . --args "{incident_key: $key}"

echo "Demo data is in $db.$obs (views) and $db.$SHOP_SCHEMA (models)."
echo "Records take 10 to 20 seconds to show. Start the app: ICECORE_VIEWS_SCHEMA=$db.$obs make app"
