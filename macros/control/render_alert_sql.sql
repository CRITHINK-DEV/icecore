{#- UC6 render_alert_sql and create_alerts: one Snowflake ALERT per notification channel (spec sections 5 and 8; setup
    step `create_alerts`).

    Channels are configured in var icecore.alert_channels, a mapping name -> settings:
        alert_channels:
          data_platform: {type: email, integration: ICECORE_EMAIL, recipients: [data@example.com], default: true}
          chat: {type: slack, integration: ICECORE_SLACK}
          teams: {type: teams, integration: ICECORE_TEAMS}
    type is email, slack or teams; integration is the name of the notification integration (an admin creates it);
    recipients (email only) are the allowed and default recipients. With two or more channels exactly one is `default: true`. Names, types, integrations and recipients are
    validated against strict patterns and render only through _icecore_sql_literal / _icecore_identifier.
    Which incidents a channel gets comes from meta.alerts (collect_sla_config): a node whose meta.alerts.channel is the
    channel's name sends its incidents there (matched on the feed's node_key, the node's unique id) and only its
    meta.alerts.severity or above (error: only error rows); the channel marked `default: true` (or the only channel)
    also gets every incident no channel claims.

    Each ALERT is `CREATE OR REPLACE ALERT <obs schema>.ICECORE_ALERT_<NAME>`:
      condition   EXISTS (dedup_window_sql(channel) filtered to the channel's incidents): new, unacknowledged and
                  not-yet-notified incidents of ALERTS_FEED (acknowledged and already notified ones stay quiet);
      action      one block: the message text is built IN SQL from the condition's result columns (RESULT_SCAN of the
                  condition query), never as SQL text, so a node name or message holding quotes, $$, ; or newlines is only
                  data; SYSTEM$SEND_SNOWFLAKE_NOTIFICATION sends it (named model, kind, root cause when present);
                  then one CALL of ICECORE_NOTIFIED(incident_key, channel) per incident (never an INSERT, never ICECORE_EMIT, which
                  refuses `icecore.notified`), so the incident is not sent again inside the dedup window. The alerts' owner
                  role needs USAGE on ICECORE_NOTIFIED; the dbt role must not have it. At most 50 incidents per run.
    Alerts are created only when var icecore.alerts is true or var icecore.alert_resume is true, and are resumed only
    with consent: alert_resume (true or false) when set, otherwise alerts (alerts: false is the default). false keeps
    a created alert suspended. A
    resumed alert runs on its warehouse every schedule period and costs the owner warehouse time. Resuming needs
    EXECUTE ALERT on the account: when it fails the grant is printed and the alert stays suspended. CREATE OR REPLACE
    ALERT suspends an alert and resets its grants (the dedup state is not affected: it lives in the event table), so
    a re-run of setup resumes it again under the same rule (one alert per channel, never a duplicate: the name is
    fixed by the channel). An alert of an earlier setup that the vars no longer ask for is never touched: setup
    prints its state and the SUSPEND and DROP statements (_icecore_warn_unmanaged).
    The alert text is constant: node ids from meta, the channel, the integration and the recipients enter only as one
    PARSE_JSON string literal of a JSON config object (condition and action each have one), read inside the text.
    Notification integrations need an admin: create_alerts prints the CREATE NOTIFICATION INTEGRATION statements and
    the grants, and never runs them. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Creates every channel's alert in <target db>.<schema>; returns [{channel, alert, status}]. Prints the
    integration statements and grants. With no channel configured it creates nothing and says how to configure. -#}
{% macro create_alerts(schema=none, resume=none) %}
  {%- set created = [] -%}
  {%- if execute -%}
    {%- set want_resume = icecore._icecore_resume_flag('alert_resume') if resume is none else resume -%}
    {%- set channels = icecore.alert_channels() -%}
    {%- if not channels -%}
      {%- do log('icecore: create_alerts: no alert channel configured; set vars: icecore: alert_channels: {<name>: {type: email|slack|teams, integration: <notification integration>}}', info=True) -%}
    {%- endif -%}
    {%- set entries = icecore.collect_sla_config() -%}
    {%- for channel in channels -%}
      {%- set status = icecore._icecore_guarded_ddl(icecore.render_alert_sql(channel, entries, schema)) -%}
      {%- if status != 'ok' -%}
        {%- do log('icecore: warning: alert ' ~ channel['name'] ~ ' not created (' ~ status ~ '); the role needs CREATE ALERT on the schema, USAGE on the warehouse and on the notification integration', info=True) -%}
      {%- endif -%}
      {%- set resumed = false -%}
      {%- if status == 'ok' and want_resume -%}
        {%- set r = icecore._icecore_guarded_ddl('ALTER ALERT ' ~ icecore.alert_name(channel, schema) ~ ' RESUME') -%}
        {%- if r == 'ok' -%}
          {%- set resumed = true -%}
        {%- else -%}
          {%- do log('icecore: warning: could not resume alert ' ~ channel['name'] ~ ' (' ~ r ~ '); it stays suspended. An admin must run: GRANT EXECUTE ALERT ON ACCOUNT TO ROLE ' ~ icecore._icecore_identifier(target.role), info=True) -%}
        {%- endif -%}
      {%- endif -%}
      {%- do created.append({'channel': channel['name'], 'alert': icecore.alert_name(channel, schema), 'status': status, 'resumed': resumed}) -%}
      {%- do log('icecore: alert ' ~ icecore.alert_name(channel, schema) ~ ': ' ~ status ~ (', resumed (it uses warehouse time on every schedule period)' if resumed else ', suspended'), info=True) -%}
    {%- endfor -%}
    {%- if channels -%}
      {%- do log('icecore: an admin runs these once (icecore never creates integrations):\n' ~ (icecore.alert_integration_sql(channels, schema) | join('\n')), info=True) -%}
    {%- endif -%}
  {%- endif -%}
  {{ return(created) }}
{% endmacro %}


{#- Whether setup creates an object: var alerts is true, or the object's own resume var (alert_resume,
    silence_task_resume) is true. The own var overrides alerts in both directions: true creates and resumes even
    with alerts false; false keeps the object suspended (still created) with alerts true; null follows alerts. -#}
{% macro _icecore_control_enabled(key) %}
  {{ return(icecore._icecore_flag('alerts', false) or (icecore._icecore_setting(key, none) is not none and icecore._icecore_flag(key, false))) }}
{% endmacro %}


{#- The existing icecore task or alert objects of the obs schema that setup does not manage under the current vars:
    [{kind, name, state}]. kind TASK matches ICECORE_SILENCE, kind ALERT matches ICECORE_ALERT_*. Reads only. -#}
{% macro _icecore_existing_control_objects(schema, kind) %}
  {%- set found = [] -%}
  {%- if kind == 'TASK' -%}
    {%- set rows = run_query('SHOW TASKS LIKE ' ~ icecore._icecore_sql_literal('ICECORE_SILENCE') ~ ' IN SCHEMA ' ~ icecore.obs_schema(schema)).rows -%}
  {%- else -%}
    {%- set rows = run_query('SHOW ALERTS LIKE ' ~ icecore._icecore_sql_literal('ICECORE_ALERT_%') ~ ' IN SCHEMA ' ~ icecore.obs_schema(schema)).rows -%}
  {%- endif -%}
  {%- for r in rows -%}
    {%- if (r['name'] | string).startswith('ICECORE_') -%}
      {%- do found.append({'kind': kind, 'name': r['name'] | string, 'state': r['state'] | string | lower}) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(found) }}
{% endmacro %}


{#- The warning lines for objects setup leaves alone (pure; offline tests render it). Statements are printed, never run. -#}
{% macro _icecore_unmanaged_lines(objects, schema=none) %}
  {%- set lines = [] -%}
  {%- for o in objects -%}
    {%- set q = icecore.obs_schema(schema) ~ '.' ~ icecore._icecore_identifier(o['name']) -%}
    {%- do lines.append('icecore: warning: ' ~ (o['kind'] | lower) ~ ' ' ~ q ~ ' exists and is ' ~ o['state']
        ~ ' but var alerts is false: setup does not update, suspend or drop it'
        ~ (' and it still uses warehouse time on every schedule period' if o['state'] in ['started', 'resumed'] else '')
        ~ '. To stop it run: ALTER ' ~ o['kind'] ~ ' ' ~ q ~ ' SUSPEND; to remove it run: DROP ' ~ o['kind'] ~ ' IF EXISTS ' ~ q ~ ';') -%}
  {%- endfor -%}
  {{ return(lines) }}
{% endmacro %}


{#- Logs one warning per existing object of `kind` that the vars do not ask setup to manage; returns the lines. -#}
{% macro _icecore_warn_unmanaged(schema, kind) %}
  {%- set lines = icecore._icecore_unmanaged_lines(icecore._icecore_existing_control_objects(schema, kind), schema) -%}
  {%- for line in lines -%}{%- do log(line, info=True) -%}{%- endfor -%}
  {{ return(lines) }}
{% endmacro %}


{#- Whether setup resumes an object it created: its own var (alert_resume, silence_task_resume) when set to true or
    false, otherwise the var `alerts`. Both default to not resuming. -#}
{% macro _icecore_resume_flag(key) %}
  {%- if icecore._icecore_setting(key, none) is none -%}
    {{ return(icecore._icecore_flag('alerts', false)) }}
  {%- endif -%}
  {{ return(icecore._icecore_flag(key, false)) }}
{% endmacro %}


{#- The validated channels of var icecore.alert_channels: [{name, type, integration, recipients, default}], sorted by
    name. A bad entry is a compile error naming it. -#}
{% macro alert_channels() %}
  {%- set raw = icecore._icecore_setting('alert_channels', {}) -%}
  {%- if raw is none or raw == '' or raw == [] -%}{{ return([]) }}{%- endif -%}
  {%- if raw is not mapping -%}
    {{ exceptions.raise_compiler_error('icecore: var alert_channels must be a mapping of channel name to {type, integration}') }}
  {%- endif -%}
  {%- set channels = [] -%}
  {%- set defaults = [] -%}
  {%- for name in raw.keys() | sort -%}
    {%- set cfg = raw[name] -%}
    {%- if not modules.re.match('^[A-Za-z][A-Za-z0-9_]{0,39}$', name | string) -%}
      {{ exceptions.raise_compiler_error('icecore: alert channel name ' ~ (name | string | truncate(60)) ~ ' must match [A-Za-z][A-Za-z0-9_]{0,39}') }}
    {%- endif -%}
    {%- if cfg is not mapping -%}
      {{ exceptions.raise_compiler_error('icecore: alert channel ' ~ name ~ ' must be a mapping {type, integration}') }}
    {%- endif -%}
    {%- set type = (cfg.get('type') or '') | string | lower -%}
    {%- if type not in ['email', 'slack', 'teams'] -%}
      {{ exceptions.raise_compiler_error('icecore: alert channel ' ~ name ~ ' has type ' ~ (type | truncate(30)) ~ '; use email, slack or teams') }}
    {%- endif -%}
    {%- set integration = (cfg.get('integration') or '') | string -%}
    {%- if not modules.re.match('^[A-Za-z_][A-Za-z0-9_$]{0,254}$', integration) -%}
      {{ exceptions.raise_compiler_error('icecore: alert channel ' ~ name ~ ' needs integration, the name of a notification integration (a plain identifier)') }}
    {%- endif -%}
    {%- set recipients = cfg.get('recipients') or [] -%}
    {%- set recipients = [recipients] if recipients is string else recipients -%}
    {%- for r in recipients -%}
      {%- if not modules.re.match('^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$', r | string) -%}
        {{ exceptions.raise_compiler_error('icecore: alert channel ' ~ name ~ ' has an invalid recipient ' ~ (r | string | truncate(60))) }}
      {%- endif -%}
    {%- endfor -%}
    {%- if type == 'email' and not recipients -%}
      {{ exceptions.raise_compiler_error('icecore: alert channel ' ~ name ~ ' is an email channel and needs recipients') }}
    {%- endif -%}
    {%- set is_default = (cfg.get('default') is sameas true) or (raw | length == 1) -%}
    {%- if is_default -%}{%- do defaults.append(name | string) -%}{%- endif -%}
    {%- do channels.append({'name': name | string, 'type': type, 'integration': integration, 'recipients': recipients | list,
        'default': is_default}) -%}
  {%- endfor -%}
  {%- if channels | length >= 2 and defaults | length != 1 -%}
    {{ exceptions.raise_compiler_error('icecore: with two or more alert channels exactly one must have default: true (it gets the incidents no node routes elsewhere); found ' ~ (defaults | length) ~ (': ' ~ (defaults | join(', ')) if defaults else '')) }}
  {%- endif -%}
  {{ return(channels) }}
{% endmacro %}


{#- The alert's name, qualified: <obs schema>."ICECORE_ALERT_<NAME>". -#}
{% macro alert_name(channel, schema=none) %}
  {{ return(icecore.obs_schema(schema) ~ '."ICECORE_ALERT_' ~ (channel['name'] | upper) ~ '"') }}
{% endmacro %}


{#- SQL text of one channel's CREATE OR REPLACE ALERT. `channel` is an entry of alert_channels(); `entries` the
    collect_sla_config() result (default: read from the graph); `all_channels` every configured channel, for the
    routing (default: the configured ones). -#}
{% macro render_alert_sql(channel, entries=none, schema=none, event_table=none, warehouse=none, schedule_minutes=none, all_channels=none, nodes=none) %}
  {%- set entries = entries if entries is not none else icecore.collect_sla_config() -%}
  {%- set all_channels = all_channels if all_channels is not none else icecore.alert_channels() -%}
  {%- set warehouse = warehouse or icecore._icecore_setting('alert_warehouse', none) or target.warehouse -%}
  {%- if not warehouse and execute -%}
    {{ exceptions.raise_compiler_error('icecore: alerts run on a warehouse; set var icecore.alert_warehouse or the target warehouse') }}
  {%- endif -%}
  {%- set minutes = schedule_minutes if schedule_minutes is not none else icecore._icecore_alert_schedule_minutes() -%}
  {%- set routing = icecore._icecore_alert_routing_config(channel, entries, all_channels, nodes) -%}
  {%- set routing_json = 'PARSE_JSON(' ~ icecore._icecore_block_literal(tojson(routing)) ~ ')' -%}
  {%- set channel_sql = 'PARSE_JSON(' ~ icecore._icecore_block_literal(tojson({'channel': channel['name']})) ~ '):channel::string' -%}
  {%- set dedup = icecore.dedup_window_sql(channel['name'], schema, event_table, channel_sql=channel_sql) -%}
  {%- set condition = 'with cfg as (select ' ~ routing_json ~ ' as c)\nselect pending.* from (\n' ~ dedup ~ '\n) as pending cross join cfg\nwhere ' ~ icecore._icecore_alert_routing_sql() -%}
  {%- set sql -%}
CREATE OR REPLACE ALERT {{ icecore.alert_name(channel, schema) }}
  WAREHOUSE = {{ icecore._icecore_identifier(warehouse) }}
  SCHEDULE = '{{ minutes }} MINUTE'
  COMMENT = 'icecore: new unacknowledged incidents of ALERTS_FEED to one channel'
  IF (EXISTS (
{{ condition }}
  ))
  THEN
{{ icecore._icecore_alert_action_sql(channel, schema) }}
  {%- endset -%}
  {{ return(sql | trim) }}
{% endmacro %}


{#- The notification statements an admin runs, and the grants: a list of SQL texts, never run by icecore. -#}
{% macro alert_integration_sql(channels, schema=none) %}
  {%- set out = [] -%}
  {%- set role = target.role or '<the role that runs icecore>' -%}
  {%- set wh = icecore._icecore_setting('alert_warehouse', none) or target.warehouse or '<warehouse>' -%}
  {%- for c in channels -%}
    {%- set i = icecore._icecore_identifier(c['integration']) -%}
    {%- if c['type'] == 'email' -%}
      {%- set rec = [] -%}
      {%- for r in c['recipients'] -%}{%- do rec.append(icecore._icecore_sql_literal(r)) -%}{%- endfor -%}
      {%- do out.append('CREATE NOTIFICATION INTEGRATION ' ~ i ~ ' TYPE = EMAIL ENABLED = TRUE ALLOWED_RECIPIENTS = (' ~ (rec | join(', ')) ~ ');') -%}
    {%- elif c['type'] == 'slack' -%}
      {%- do out.append('CREATE SECRET ' ~ icecore.obs_schema(schema) ~ '."' ~ (c['integration'] | upper) ~ '_SECRET" TYPE = GENERIC_STRING SECRET_STRING = \'<the Slack webhook path T000/B000/XXXX>\';') -%}
      {%- do out.append('CREATE NOTIFICATION INTEGRATION ' ~ i ~ ' TYPE = WEBHOOK ENABLED = TRUE WEBHOOK_URL = \'https://hooks.slack.com/services/SNOWFLAKE_WEBHOOK_SECRET\' WEBHOOK_SECRET = ' ~ icecore.obs_schema(schema) ~ '."' ~ (c['integration'] | upper) ~ '_SECRET" WEBHOOK_BODY_TEMPLATE = \'{"text": "SNOWFLAKE_WEBHOOK_MESSAGE"}\' WEBHOOK_HEADERS = (\'Content-Type\' = \'application/json\');') -%}
    {%- else -%}
      {%- do out.append('CREATE NOTIFICATION INTEGRATION ' ~ i ~ ' TYPE = WEBHOOK ENABLED = TRUE WEBHOOK_URL = \'<the Teams workflow webhook URL>\' WEBHOOK_BODY_TEMPLATE = \'{"text": "SNOWFLAKE_WEBHOOK_MESSAGE"}\' WEBHOOK_HEADERS = (\'Content-Type\' = \'application/json\');') -%}
    {%- endif -%}
    {%- do out.append('GRANT USAGE ON INTEGRATION ' ~ i ~ ' TO ROLE ' ~ icecore._icecore_identifier(role) ~ ';') -%}
  {%- endfor -%}
  {%- for c in channels -%}
    {%- do out.append('ALTER ALERT ' ~ icecore.alert_name(c, schema) ~ ' RESUME;  -- only once the integration and its grants exist; setup resumes alerts itself only when var alerts is true') -%}
  {%- endfor -%}
  {%- do out.append('GRANT EXECUTE ALERT ON ACCOUNT TO ROLE ' ~ icecore._icecore_identifier(role) ~ ';') -%}
  {%- do out.append('GRANT USAGE ON WAREHOUSE ' ~ icecore._icecore_identifier(wh) ~ ' TO ROLE ' ~ icecore._icecore_identifier(role) ~ ';') -%}
  {{ return(out) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- var icecore.alert_schedule_minutes, a positive whole number, default 5. -#}
{% macro _icecore_alert_schedule_minutes() %}
  {%- set m = icecore._icecore_setting('alert_schedule_minutes', 5) -%}
  {%- set m = (m | float(0)) if m is not none and m is not mapping else 0 -%}
  {{ return(m | round | int if m >= 1 else 5) }}
{% endmacro %}


{#- The routing config of one channel's condition: {channel, default, any, error, claimed}. `any` are the keys of the
    nodes that route to this channel (every severity), `error` those limited to error rows, `claimed` the keys of every
    node routed to any configured channel. A key is what the feed's node_key holds for the node's signals: the node's
    unique id, its relation (anomalies and schema changes: relation|...) and the unique ids of its tests
    (_icecore_alert_node_keys), so a model's meta.alerts routes its tests and its relation. A meta.alerts.channel that
    is not a configured channel is a compile error naming the node. -#}
{% macro _icecore_alert_routing_config(channel, entries, all_channels, nodes=none) %}
  {%- set names = [] -%}
  {%- for c in all_channels -%}{%- do names.append(c['name']) -%}{%- endfor -%}
  {%- if nodes is none -%}
    {%- set nodes = graph.nodes if execute else {} -%}
  {%- endif -%}
  {%- set out = {'channel': channel['name'], 'default': channel['default'] is sameas true, 'any': [], 'error': [], 'claimed': []} -%}
  {%- for e in entries if e.get('channel') is not none -%}
    {%- if e['channel'] not in names -%}
      {{ exceptions.raise_compiler_error('icecore: ' ~ (e.get('resource_type') or 'node') ~ " '" ~ e.get('name') ~ "' (" ~ e.get('unique_id') ~ "): meta.alerts.channel '" ~ (e['channel'] | string | truncate(60)) ~ "' is not a configured alert channel (configured: " ~ (names | join(', ') or 'none') ~ '); channel names match [A-Za-z][A-Za-z0-9_]{0,39}') }}
    {%- endif -%}
    {%- set keys = icecore._icecore_alert_node_keys(e, nodes) -%}
    {%- do out['claimed'].extend(keys) -%}
    {%- if e['channel'] == channel['name'] -%}
      {%- do out['error' if e.get('severity') == 'error' else 'any'].extend(keys) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- The feed keys of a node declaring meta.alerts: its unique id; for a model, seed or snapshot also its relation
    ("DB"."SCHEMA"."NAME", as the metric and schema-change records carry it) and the unique id of every test attached
    to it (the test's attached_node, else its first model dependency). `nodes` maps unique_id -> node. -#}
{% macro _icecore_alert_node_keys(entry, nodes) %}
  {%- set uid = entry['unique_id'] -%}
  {%- set keys = [uid] -%}
  {%- set node = nodes.get(uid) if nodes else none -%}
  {%- if node is not none and node.get('resource_type') in ['model', 'seed', 'snapshot'] -%}
    {%- set relation = icecore._icecore_node_relation(node) -%}
    {%- if relation -%}{%- do keys.append(relation | string) -%}{%- endif -%}
    {%- for t in nodes.values() if t.get('resource_type') == 'test' -%}
      {%- set deps = [] -%}
      {%- for d in ((t.get('depends_on') or {}).get('nodes') or []) | sort -%}
        {%- if d is string and (d.startswith('model.') or d.startswith('seed.') or d.startswith('snapshot.')) -%}{%- do deps.append(d) -%}{%- endif -%}
      {%- endfor -%}
      {%- set attached = t.get('attached_node') or (deps[0] if deps else none) -%}
      {%- if attached == uid -%}{%- do keys.append(t.get('unique_id')) -%}{%- endif -%}
    {%- endfor -%}
  {%- endif -%}
  {{ return(keys) }}
{% endmacro %}


{#- The WHERE predicate of the condition on pending (the dedup rows) and cfg.c (the routing config). Constant text. -#}
{% macro _icecore_alert_routing_sql() %}
  {%- set k = "array_contains(pending.node_key::variant, cfg.c:{a}::array) or array_contains(split_part(pending.node_key, '|', 1)::variant, cfg.c:{a}::array)" -%}
  {{ return("(" ~ k.replace('{a}', 'any') ~ "\n  or (pending.severity = 'error' and (" ~ k.replace('{a}', 'error') ~ "))\n  or (cfg.c:default::boolean and not (" ~ k.replace('{a}', 'claimed') ~ ")))") }}
{% endmacro %}


{#- The action: a constant block. Its one config literal is PARSE_JSON of {channel, integration, recipients}. For each
    incident of the condition's result (RESULT_SCAN of the condition query; at most 50, oldest first) it first CALLs
    ICECORE_NOTIFIED(incident_key, channel) and checks the returned status. Only incidents recorded ok are sent, in one
    notification; one whose recording failed is NOT sent (it would repeat on every run) and is logged instead as an
    `icecore.alert_record_failed` event through ICECORE_EMIT. The send is last, so a failed send leaves the incidents
    recorded and not re-sent (at most once; the failure shows in ALERT_HISTORY). send=false leaves the send out, for
    tests with no integration. qid is the SQL yielding the condition query's id. -#}
{% macro _icecore_alert_action_sql(channel, schema=none, send=true, qid="SNOWFLAKE.ALERT.GET_CONDITION_QUERY_UUID()") %}
  {%- set source = 'table(result_scan(:qid))' -%}
  {%- set config = {'channel': channel['name'], 'integration': channel['integration'], 'recipients': channel['recipients']} -%}
  {%- set sql -%}
  BEGIN
    LET cfg VARIANT := PARSE_JSON({{ icecore._icecore_block_literal(tojson(config)) }});
    LET qid STRING := {{ qid }};
    LET ch STRING := cfg:channel::STRING;
    LET k STRING := '';
    LET st STRING := '';
    LET msg STRING := '';
    LET recorded ARRAY := ARRAY_CONSTRUCT();
    LET rs RESULTSET := (select incident_key from {{ source }} order by first_detected_at, incident_key limit 50);
    LET c CURSOR FOR rs;
    FOR r IN c DO
      k := r.incident_key;
      CALL {{ icecore._icecore_procedure('ICECORE_NOTIFIED', schema) }}(:k, :ch) INTO :st;
      IF (STARTSWITH(st, 'ok')) THEN
        recorded := ARRAY_APPEND(recorded, :k);
      ELSE
        CALL {{ icecore._icecore_procedure('ICECORE_EMIT', schema) }}(ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('name', 'icecore.alert_record_failed', 'attrs', OBJECT_CONSTRUCT('icecore.alert.channel', :ch, 'icecore.alert.incident_key', :k, 'icecore.alert.status', LEFT(:st, 500)))));
      END IF;
    END FOR;
    IF (ARRAY_SIZE(recorded) > 0) THEN
      msg := ({{ icecore.alert_message_sql(channel['type'], '(select * from ' ~ source ~ ' where array_contains(incident_key::variant, :recorded))') }});
{%- if send %}
      CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION({{ icecore._icecore_alert_payload_sql(channel) }}, {{ icecore._icecore_alert_config_sql(channel) }});
{%- endif %}
    END IF;
  END
  {%- endset -%}
  {{ return(sql | trim) }}
{% endmacro %}


{#- SELECT of the notification text from `source` (a FROM item with the feed's columns): constant SQL, the rows only
    ever read as columns. At most 50 incidents, oldest first; the text is cut at 3000 characters. For email the fields
    are plain text and lines are separated by newlines. For Slack and Teams every field is cleaned (a webhook body
    template is JSON and how Snowflake substitutes SNOWFLAKE_WEBHOOK_MESSAGE is not documented, so no quote, backslash
    or control character may reach it; SANITIZE_WEBHOOK_CONTENT only redacts the secret token and escapes none of
    them) and Slack's & < > are escaped; lines are joined with ' | ' on one line. -#}
{% macro alert_message_sql(type, source) %}
  {%- set kind = icecore._icecore_alert_field('kind', type, '?') -%}
  {%- set title = icecore._icecore_alert_field('title', type, '') -%}
  {%- set node = icecore._icecore_alert_field('node_key', type, '?') -%}
  {%- set rcu = icecore._icecore_alert_field('root_cause_unique_id', type, '?') -%}
  {%- set rck = icecore._icecore_alert_field('root_cause_kind', type, '?') -%}
  {%- set rc = "iff(root_cause_unique_id is not null or root_cause_kind is not null, " -%}
  {%- if type == 'slack' -%}
    {%- set line = "'• *' || " ~ kind ~ " || '* ' || " ~ title ~ " || ' (node `' || " ~ node ~ " || '`)' || " ~ rc ~ "', root cause: `' || " ~ rcu ~ " || '` (' || " ~ rck ~ " || ')', '')" -%}
    {%- set sep = "' | '" -%}
  {%- elif type == 'teams' -%}
    {%- set line = "'**' || " ~ kind ~ " || '** ' || " ~ title ~ " || ' (node ' || " ~ node ~ " || ')' || " ~ rc ~ "', root cause: ' || " ~ rcu ~ " || ' (' || " ~ rck ~ " || ')', '')" -%}
    {%- set sep = "' | '" -%}
  {%- else -%}
    {%- set line = "'[' || " ~ kind ~ " || '] ' || " ~ title ~ " || ' - node ' || " ~ node ~ " || " ~ rc ~ "' - root cause: ' || " ~ rcu ~ " || ' (' || " ~ rck ~ " || ')', '')" -%}
    {%- set sep = "'\\n'" -%}
  {%- endif -%}
  {{ return("select left('icecore: ' || count(*) || ' new incident(s)' || " ~ sep ~ " || listagg(" ~ line ~ ", " ~ sep ~ ") within group (order by first_detected_at, incident_key), 3000) from (select * from " ~ source ~ " order by first_detected_at, incident_key limit 50)") }}
{% endmacro %}


{#- One column as text for the message: coalesced, and for the webhook types cleaned (quotes, backslashes and control
    characters replaced; Slack: & < > escaped, & first). -#}
{% macro _icecore_alert_field(column, type, default) %}
  {%- set e = "coalesce(" ~ column ~ ", '" ~ default ~ "')" -%}
  {%- if type == 'email' -%}{{ return(e) }}{%- endif -%}
  {%- if type == 'slack' -%}
    {%- set e = "replace(replace(replace(" ~ e ~ ", '&', '&amp;'), '<', '&lt;'), '>', '&gt;')" -%}
  {%- endif -%}
  {{ return("regexp_replace(replace(replace(" ~ e ~ ", '\\\\', '/'), '\"', ''''), '[[:cntrl:]]', ' ')") }}
{% endmacro %}


{#- The notification body argument. -#}
{% macro _icecore_alert_payload_sql(channel) %}
  {%- if channel['type'] == 'email' -%}
    {{ return('SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(:msg)') }}
  {%- endif -%}
  {{ return('SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(SNOWFLAKE.NOTIFICATION.SANITIZE_WEBHOOK_CONTENT(:msg))') }}
{% endmacro %}


{#- The notification integration argument, read from the action's config variable cfg. EMAIL_INTEGRATION_CONFIG's
    3-argument form is (integration, subject, to ARRAY); sandbox-checked that it evaluates with variables. -#}
{% macro _icecore_alert_config_sql(channel) %}
  {%- if channel['type'] == 'email' -%}
    {{ return("SNOWFLAKE.NOTIFICATION.EMAIL_INTEGRATION_CONFIG(:cfg:integration::STRING, 'icecore: new incidents', :cfg:recipients::ARRAY)") }}
  {%- endif -%}
  {{ return('SNOWFLAKE.NOTIFICATION.INTEGRATION(:cfg:integration::STRING)') }}
{% endmacro %}
