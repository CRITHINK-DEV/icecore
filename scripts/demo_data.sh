#!/usr/bin/env bash
# Fill a persistent demo schema so every page of the app has content (docs/app.md).
#
# Runs a copy of examples/shop with the icecore hooks into DB.ICECORE_DEMO (models) and DB.ICECORE_DEMO_OBS (icecore:
# procedures and views), after `dbt run-operation icecore.setup`: one build per step, one of them with a failing test,
# then a jump in row count after 8 points of history (the anomaly engine needs 7; the tests do not score yet, so the jump
# is recorded, not flagged), with `meta.sla` on dim_customers
# and `cost: true`. Creates objects only in those two schemas. Run it again to add more history.
#
#   SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH, SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE  your login
#   SNOWFLAKE_DATABASE     default ICECORE_SANDBOX
#   DEMO_SCHEMA            default ICECORE_DEMO;  DEMO_OBS_SCHEMA default <DEMO_SCHEMA>_OBS
#   DBT                    a dbt-snowflake (1.8 or later) executable; default: dbt on the PATH
#   BUILDS                 builds before the volume change, default 8 (the anomaly engine needs 7 points)
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dbt="${DBT:-dbt}"
export SNOWFLAKE_DATABASE="${SNOWFLAKE_DATABASE:-ICECORE_SANDBOX}"
export SHOP_SCHEMA="${DEMO_SCHEMA:-ICECORE_DEMO}"
obs="${DEMO_OBS_SCHEMA:-${SHOP_SCHEMA}_OBS}"
builds="${BUILDS:-8}"
for v in SNOWFLAKE_ACCOUNT SNOWFLAKE_USER SNOWFLAKE_PRIVATE_KEY_PATH SNOWFLAKE_ROLE SNOWFLAKE_WAREHOUSE; do
  [ -n "${!v:-}" ] || { echo "set $v (see the header of this script)" >&2; exit 1; }
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp -R "$root/examples/shop/." "$work/"
cd "$work"
python3 - "$root" "$obs" <<'PY'
import re, sys
root, obs = sys.argv[1:3]
def edit(path, fn):
    text = open(path).read(); open(path, "w").write(fn(text))
edit("packages.yml", lambda t: t.replace("local: ../../", f"local: {root}"))
edit("dbt_project.yml", lambda t: t.replace("obs_schema: ICECORE_OBS", f"obs_schema: {obs}")
     .replace("    telemetry: true", "    cost: true\n    telemetry: true"))
# meta.sla on dim_customers: the SLAs page and the silence task have something to watch
edit("models/marts/_marts.yml", lambda t: t.replace(
    '  - name: dim_customers\n    description: "One row per customer, with order count and lifetime amount."',
    '  - name: dim_customers\n    description: "One row per customer, with order count and lifetime amount."\n'
    '    config:\n      meta:\n        sla: {expected_every: 1d, grace: 1h}'))
PY
grep -q "expected_every" models/marts/_marts.yml && grep -q "cost: true" dbt_project.yml

"$dbt" deps --profiles-dir .
"$dbt" run-operation icecore.setup --profiles-dir . --args "{schema: $obs, runs_per_day: 24}"
"$dbt" seed --profiles-dir .
build() { "$dbt" build --profiles-dir . --exclude resource_type:seed "$@" || echo "(build finished with failures, as intended when a test is broken)"; }

for i in $(seq 1 "$builds"); do
  echo "== build $i of $builds"
  if [ "$i" = 3 ]; then build --vars '{shop_fail: true}'; else build; fi   # build 3: one test fails
done
echo "== volume change: 8 orders become 208"
python3 - <<'PY'
rows = ["{i},{c},completed,{a}.00,2026-10-{d:02d} 10:00:00".format(i=100 + i, c=1 + i % 5, a=10 + i % 40, d=1 + i % 28) for i in range(200)]
open("seeds/raw_orders.csv", "a").write("\n".join(rows) + "\n")
PY
"$dbt" seed --profiles-dir . --select raw_orders
build
echo "Demo data is in $SNOWFLAKE_DATABASE.$obs (views) and $SNOWFLAKE_DATABASE.$SHOP_SCHEMA (models)."
echo "Records take 10 to 20 seconds to show. Start the app: ICECORE_VIEWS_SCHEMA=$SNOWFLAKE_DATABASE.$obs make app"
