{#- UC6 render_alert_sql and create_alerts: one Snowflake ALERT per notification channel (spec sections 5 and 8; setup
    step `create_alerts`).

    Channels are configured in var icecore.alert_channels, a mapping name -> settings:
        alert_channels:
          data_platform: {type: email, integration: ICECORE_EMAIL, recipients: [data@example.com], default: true}
          chat: {type: slack, integration: ICECORE_SLACK}
          teams: {type: teams, integration: ICECORE_TEAMS}
    type is email, slack or teams; integration is the name of the notification integration (an admin creates it);
    recipients (email only) are the allowed and default recipients. Names, types, integrations and recipients are
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
    Alerts are created SUSPENDED (Snowflake's default) and stay so unless var icecore.alert_resume is true: nothing
    sends or costs anything without consent. Re-running setup replaces an alert and so suspends it again.
    Notification integrations need an admin: create_alerts prints the CREATE NOTIFICATION INTEGRATION statements and
    the grants, and never runs them. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Creates every channel's alert in <target db>.<schema>; returns [{channel, alert, status, resumed}]. Prints the
    integration statements and grants. With no channel configured it creates nothing and says how to configure. -#}
{% macro create_alerts(schema=none) %}
  {%- set created = [] -%}
  {%- if execute -%}
    {%- set channels = icecore.alert_channels() -%}
    {%- if not channels -%}
      {%- do log('icecore: create_alerts: no alert channel configured; set vars: icecore: alert_channels: {<name>: {type: email|slack|teams, integration: <notification integration>}}', info=True) -%}
    {%- endif -%}
    {%- set entries = icecore.collect_sla_config() -%}
    {%- for channel in channels -%}
      {%- set status = icecore._icecore_guarded_ddl(icecore.render_alert_sql(channel, entries, schema)) -%}
      {%- set resumed = false -%}
      {%- if status == 'ok' and icecore._icecore_flag('alert_resume', false) -%}
        {%- set resume_status = icecore._icecore_guarded_ddl('ALTER ALERT ' ~ icecore.alert_name(channel, schema) ~ ' RESUME') -%}
        {%- set resumed = resume_status == 'ok' -%}
        {%- if not resumed -%}
          {%- do log('icecore: warning: alert ' ~ channel['name'] ~ ' not resumed (' ~ resume_status ~ '); the role needs EXECUTE ALERT on the account', info=True) -%}
        {%- endif -%}
      {%- endif -%}
      {%- if status != 'ok' -%}
        {%- do log('icecore: warning: alert ' ~ channel['name'] ~ ' not created (' ~ status ~ '); the role needs CREATE ALERT on the schema, USAGE on the warehouse and on the notification integration', info=True) -%}
      {%- endif -%}
      {%- do created.append({'channel': channel['name'], 'alert': icecore.alert_name(channel, schema), 'status': status, 'resumed': resumed}) -%}
      {%- do log('icecore: alert ' ~ icecore.alert_name(channel, schema) ~ ': ' ~ status ~ (', resumed' if resumed else ', suspended'), info=True) -%}
    {%- endfor -%}
    {%- if channels -%}
      {%- do log('icecore: an admin runs these once (icecore never creates integrations):\n' ~ (icecore.alert_integration_sql(channels, schema) | join('\n')), info=True) -%}
    {%- endif -%}
  {%- endif -%}
  {{ return(created) }}
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
    {%- do channels.append({'name': name | string, 'type': type, 'integration': integration, 'recipients': recipients | list,
        'default': (cfg.get('default') is sameas true) or (raw | length == 1)}) -%}
  {%- endfor -%}
  {{ return(channels) }}
{% endmacro %}


{#- The alert's name, qualified: <obs schema>."ICECORE_ALERT_<NAME>". -#}
{% macro alert_name(channel, schema=none) %}
  {{ return(icecore.obs_schema(schema) ~ '."ICECORE_ALERT_' ~ (channel['name'] | upper) ~ '"') }}
{% endmacro %}


{#- SQL text of one channel's CREATE OR REPLACE ALERT. `channel` is an entry of alert_channels(); `entries` the
    collect_sla_config() result (default: read from the graph); `all_channels` every configured channel, for the
    routing (default: the configured ones). -#}
{% macro render_alert_sql(channel, entries=none, schema=none, event_table=none, warehouse=none, schedule_minutes=none, all_channels=none) %}
  {%- set entries = entries if entries is not none else icecore.collect_sla_config() -%}
  {%- set all_channels = all_channels if all_channels is not none else icecore.alert_channels() -%}
  {%- set warehouse = warehouse or icecore._icecore_setting('alert_warehouse', none) or target.warehouse -%}
  {%- if not warehouse and execute -%}
    {{ exceptions.raise_compiler_error('icecore: alerts run on a warehouse; set var icecore.alert_warehouse or the target warehouse') }}
  {%- endif -%}
  {%- set minutes = schedule_minutes if schedule_minutes is not none else icecore._icecore_alert_schedule_minutes() -%}
  {%- set condition = 'select * from (\n' ~ icecore.dedup_window_sql(channel['name'], schema, event_table) ~ '\n) as pending\nwhere ' ~ icecore._icecore_alert_routing_sql(channel, entries, all_channels) -%}
  {%- set sql -%}
CREATE OR REPLACE ALERT {{ icecore.alert_name(channel, schema) }}
  WAREHOUSE = {{ icecore._icecore_identifier(warehouse) }}
  SCHEDULE = '{{ minutes }} MINUTE'
  COMMENT = {{ icecore._icecore_sql_literal('icecore: new unacknowledged incidents of ALERTS_FEED to channel ' ~ channel['name']) }}
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


{#- The WHERE predicate on the feed's columns that keeps the channel's incidents. Literals come from config only
    (node unique ids, through _icecore_sql_literal). -#}
{% macro _icecore_alert_routing_sql(channel, entries, all_channels) %}
  {%- set names = [] -%}
  {%- for c in all_channels -%}{%- do names.append(c['name']) -%}{%- endfor -%}
  {%- set mine = [] -%}
  {%- set claimed = [] -%}
  {%- for e in entries if e.get('channel') in names -%}
    {%- do claimed.append(icecore._icecore_sql_literal(e['unique_id'])) -%}
    {%- if e['channel'] == channel['name'] -%}
      {%- do mine.append('(node_key = ' ~ icecore._icecore_sql_literal(e['unique_id']) ~ (" and severity = 'error'" if e.get('severity') == 'error' else '') ~ ')') -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set parts = [] -%}
  {%- do parts.extend(mine) -%}
  {%- if channel['default'] -%}
    {%- do parts.append('node_key not in (' ~ claimed | join(', ') ~ ')' if claimed else 'true') -%}
  {%- endif -%}
  {{ return('(' ~ (parts | join(' or ') if parts else 'false') ~ ')') }}
{% endmacro %}


{#- The action: a block that builds the message and the notified events from the condition's result in SQL, sends the
    notification (send=false leaves that CALL out, for tests that must not send) and records the notified events.
    qid is the SQL that yields the condition query's id. -#}
{% macro _icecore_alert_action_sql(channel, schema=none, send=true, qid="SNOWFLAKE.ALERT.GET_CONDITION_QUERY_UUID()") %}
  {%- set source = 'table(result_scan(:qid))' -%}
  {%- set sql -%}
  BEGIN
    LET qid STRING := {{ qid }};
    LET msg STRING := ({{ icecore.alert_message_sql(channel['type'], source) }});
    {% if send -%}
    CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION({{ icecore._icecore_alert_payload_sql(channel) }}, {{ icecore._icecore_alert_config_sql(channel) }});
    {% endif -%}
    {{ icecore.alert_record_sql(channel['name'], source, schema) }}
  END
  {%- endset -%}
  {{ return(sql | trim) }}
{% endmacro %}


{#- SELECT of the notification text from `source` (a FROM item with the feed's columns): constant SQL, the rows only
    ever read as columns. At most 50 incidents, oldest first; the text is cut at 3000 characters. -#}
{% macro alert_message_sql(type, source) %}
  {%- set rc = "iff(root_cause_unique_id is not null or root_cause_kind is not null, " -%}
  {%- if type == 'slack' -%}
    {%- set line = "'• *' || coalesce(kind, '?') || '* ' || coalesce(title, '') || ' (node `' || coalesce(node_key, '?') || '`)' || " ~ rc ~ "', root cause: `' || coalesce(root_cause_unique_id, '?') || '` (' || coalesce(root_cause_kind, '?') || ')', '')" -%}
    {%- set sep = "'\\n'" -%}
  {%- elif type == 'teams' -%}
    {%- set line = "'- **' || coalesce(kind, '?') || '** ' || coalesce(title, '') || ' (node ' || coalesce(node_key, '?') || ')' || " ~ rc ~ "', root cause: ' || coalesce(root_cause_unique_id, '?') || ' (' || coalesce(root_cause_kind, '?') || ')', '')" -%}
    {%- set sep = "'\\n\\n'" -%}
  {%- else -%}
    {%- set line = "'[' || coalesce(kind, '?') || '] ' || coalesce(title, '') || ' - node ' || coalesce(node_key, '?') || " ~ rc ~ "' - root cause: ' || coalesce(root_cause_unique_id, '?') || ' (' || coalesce(root_cause_kind, '?') || ')', '')" -%}
    {%- set sep = "'\\n'" -%}
  {%- endif -%}
  {{ return("select left('icecore: ' || count(*) || ' new incident(s)' || " ~ sep ~ " || listagg(" ~ line ~ ", " ~ sep ~ ") within group (order by first_detected_at, incident_key), 3000) from (select * from " ~ source ~ " order by first_detected_at, incident_key limit 50)") }}
{% endmacro %}


{#- The loop that records each notified incident of `source` (the same 50 as the message) by CALLing ICECORE_NOTIFIED:
    the incident key is a bound column of the loop row, the channel a validated name rendered as a literal. -#}
{% macro alert_record_sql(channel_name, source, schema=none) %}
  {{ return("LET k STRING := '';\n    LET rs RESULTSET := (select incident_key from " ~ source ~ " order by first_detected_at, incident_key limit 50);\n    LET c CURSOR FOR rs;\n    FOR r IN c DO\n      k := r.incident_key;\n      CALL "
      ~ icecore._icecore_procedure('ICECORE_NOTIFIED', schema) ~ "(:k, " ~ icecore._icecore_sql_literal(channel_name) ~ ");\n    END FOR;") }}
{% endmacro %}


{#- The notification body argument. Webhook text is sanitized by Snowflake's own function. -#}
{% macro _icecore_alert_payload_sql(channel) %}
  {%- if channel['type'] == 'email' -%}
    {{ return('SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(:msg)') }}
  {%- endif -%}
  {{ return('SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(SNOWFLAKE.NOTIFICATION.SANITIZE_WEBHOOK_CONTENT(:msg))') }}
{% endmacro %}


{#- The notification integration argument. -#}
{% macro _icecore_alert_config_sql(channel) %}
  {%- set integration = icecore._icecore_sql_literal(channel['integration']) -%}
  {%- if channel['type'] == 'email' -%}
    {%- set rec = [] -%}
    {%- for r in channel['recipients'] -%}{%- do rec.append(icecore._icecore_sql_literal(r)) -%}{%- endfor -%}
    {%- set recipients = 'ARRAY_CONSTRUCT(' ~ (rec | join(', ')) ~ ')' -%}
    {#- The signature Snowflake lists: EMAIL_INTEGRATION_CONFIG(integration, subject, to ARRAY) (and a 5-argument form with cc and bcc arrays). -#}
    {{ return('SNOWFLAKE.NOTIFICATION.EMAIL_INTEGRATION_CONFIG(' ~ integration ~ ", 'icecore: new incidents', " ~ recipients ~ ')') }}
  {%- endif -%}
  {{ return('SNOWFLAKE.NOTIFICATION.INTEGRATION(' ~ integration ~ ')') }}
{% endmacro %}
