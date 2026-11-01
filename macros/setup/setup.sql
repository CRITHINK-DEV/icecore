{#- UC5 setup (spec section 5; spikes/GATE1.md, "What setup creates and the grants it needs").

    dbt run-operation icecore.setup --args '{schema: ICECORE_OBS, compat: none, app: false, runs_per_day: 24}'

    One ordered list of steps (_icecore_setup_steps), run in order:
      1. detect_capabilities      reads only; recorded in step 5, once the procedures exist;
      2. create_schema            CREATE SCHEMA IF NOT EXISTS <target db>.<obs schema> (create_emit_procedures needs it);
      3. create_emit_procedures   ICECORE_EMIT and ICECORE_EMIT_RUN (CREATE OR REPLACE ... COPY GRANTS);
      4. set_telemetry_levels     the database's levels; prints what the role cannot run;
      5. record_capabilities      the icecore.capabilities span event, through the new ICECORE_EMIT;
      6. estimate_telemetry_cost  rows and credits per month from the graph and runs_per_day (reads nothing);
      7. create_views            CREATE OR REPLACE VIEW ... COPY GRANTS for every view of icecore.create_views;
      8. create_compat_views (compat: elementary): the Elementary-compatible views in the compat schema, over the core
         views of step 7 (macros/compat/create_compat_views.sql);
         create_alerts, create_tasks, deploy_app (app): planned. They keep
         their place here and are reported as planned until their macros exist; a story that adds one marks it
         ready and adds its branch to _icecore_setup_run_step.

    Every step is idempotent: a second run leaves the schema, the procedures' DDL and their grants as they were, and
    changes no level that is already sufficient. setup creates no tables. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns {database, schema, compat, app, capabilities, procedures, levels, recorded, estimate, steps: [{name,
    status, detail}]}; status is done or planned. Nothing at parse time. -#}
{% macro setup(schema=none, compat='none', app=false, runs_per_day=none) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- if (compat or 'none') | string | lower not in ['none', 'elementary'] -%}
    {{ exceptions.raise_compiler_error("icecore: setup compat must be 'none' or 'elementary', got " ~ compat ~ "; no step was run") }}
  {%- endif -%}
  {%- set state = {
      'database': target.database | string | upper,
      'schema': icecore.obs_schema_name(schema),
      'compat': (compat or 'none') | string | lower,
      'app': app is sameas true,
      'runs_per_day': runs_per_day,
      'capabilities': none, 'procedures': [], 'levels': none, 'recorded': none, 'estimate': none, 'steps': [],
  } -%}
  {%- set steps = icecore._icecore_setup_steps(state['compat'], state['app']) -%}
  {% do log('icecore: setup in ' ~ icecore.obs_schema(state['schema']) ~ ' (' ~ steps | length ~ ' steps)', info=True) %}
  {%- for step in steps -%}
    {%- if step['ready'] -%}
      {%- set detail = icecore._icecore_setup_run_step(step['name'], state) -%}
      {%- do state['steps'].append({'name': step['name'], 'status': 'done', 'detail': detail}) -%}
      {% do log('icecore: setup step ' ~ loop.index ~ '/' ~ loop.length ~ ' ' ~ step['name'] ~ ': ' ~ detail, info=True) %}
    {%- else -%}
      {%- do state['steps'].append({'name': step['name'], 'status': 'planned', 'detail': step['detail']}) -%}
      {% do log('icecore: setup step ' ~ loop.index ~ '/' ~ loop.length ~ ' ' ~ step['name'] ~ ': planned, not available yet (' ~ step['detail'] ~ ')', info=True) %}
    {%- endif -%}
  {%- endfor -%}
  {%- if state['schema'] != icecore.obs_schema_name() -%}
    {% do log('icecore: warning: setup used schema ' ~ state['schema'] ~ ' but the obs_schema var is ' ~ icecore.obs_schema_name()
        ~ '; set vars: icecore: obs_schema: ' ~ state['schema'] ~ ' in dbt_project.yml so that dbt runs write through these procedures', info=True) %}
  {%- endif -%}
  {{ return(state) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The ordered steps: [{name, ready, detail}]. `ready` steps run; the others are reported as planned. -#}
{% macro _icecore_setup_steps(compat='none', app=false) %}
  {%- set steps = [
      {'name': 'detect_capabilities', 'ready': true, 'detail': 'edition, event table, levels, Cortex'},
      {'name': 'create_schema', 'ready': true, 'detail': 'the obs schema'},
      {'name': 'create_emit_procedures', 'ready': true, 'detail': 'ICECORE_EMIT and ICECORE_EMIT_RUN'},
      {'name': 'set_telemetry_levels', 'ready': true, 'detail': "the database's LOG_LEVEL and TRACE_LEVEL"},
      {'name': 'record_capabilities', 'ready': true, 'detail': 'the icecore.capabilities span event'},
      {'name': 'estimate_telemetry_cost', 'ready': true, 'detail': 'rows and credits per month'},
      {'name': 'create_views', 'ready': true, 'detail': 'the core views over base_events'},
  ] -%}
  {%- if (compat or 'none') | string | lower != 'none' -%}
    {%- do steps.append({'name': 'create_compat_views', 'ready': true, 'detail': 'the ' ~ compat ~ ' compat views'}) -%}
  {%- endif -%}
  {%- do steps.append({'name': 'create_alerts', 'ready': false, 'detail': 'Snowflake alerts'}) -%}
  {%- do steps.append({'name': 'create_tasks', 'ready': false, 'detail': 'Snowflake tasks'}) -%}
  {%- if app is sameas true -%}
    {%- do steps.append({'name': 'deploy_app', 'ready': false, 'detail': 'the Streamlit app'}) -%}
  {%- endif -%}
  {{ return(steps) }}
{% endmacro %}


{#- Runs one ready step, updates state, returns a one-line detail (the same on every run of an unchanged setup). -#}
{% macro _icecore_setup_run_step(name, state) %}
  {%- if name == 'detect_capabilities' -%}
    {%- set c = icecore.detect_capabilities(record=false, database=state['database']) -%}
    {%- do state.update({'capabilities': c}) -%}
    {{ return('edition ' ~ c['edition'] ~ ', event table ' ~ (c['event_table'] or 'none') ~ ', LOG_LEVEL '
        ~ (c['log_level'] or 'unknown') ~ ', TRACE_LEVEL ' ~ (c['trace_level'] or 'unknown') ~ ', Cortex ' ~ ('yes' if c['cortex'] else 'no')) }}
  {%- elif name == 'create_schema' -%}
    {%- set statement = 'CREATE SCHEMA IF NOT EXISTS ' ~ icecore.obs_schema(state['schema']) -%}
    {%- set status = icecore._icecore_guarded_ddl(statement) -%}
    {%- if status != 'ok' -%}
      {% do log('icecore: warning: ' ~ statement ~ ' failed (' ~ status ~ '); the role needs CREATE SCHEMA on ' ~ state['database'] ~ ', or an admin must create the schema', info=True) %}
    {%- endif -%}
    {{ return(statement ~ ': ' ~ status) }}
  {%- elif name == 'create_emit_procedures' -%}
    {%- do state.update({'procedures': icecore.create_emit_procedures(state['schema'])}) -%}
    {{ return('CREATE OR REPLACE ... COPY GRANTS ' ~ (state['procedures'] | join(', '))) }}
  {%- elif name == 'set_telemetry_levels' -%}
    {%- set levels = icecore.set_telemetry_levels(state['capabilities'], database=state['database']) -%}
    {%- do state.update({'levels': levels}) -%}
    {{ return(('levels sufficient' if levels['levels_ok'] else 'levels insufficient') ~ ', ran ' ~ (levels['executed'] | length)
        ~ ', printed ' ~ (levels['printed'] | length) ~ ' for an admin') }}
  {%- elif name == 'record_capabilities' -%}
    {%- set status = icecore._icecore_emit_in_schema(state['schema'], 'capabilities', 'capabilities detected',
        icecore._icecore_capability_attributes(state['capabilities'])) -%}
    {%- do state.update({'recorded': status}) -%}
    {{ return('icecore.capabilities: ' ~ status) }}
  {%- elif name == 'estimate_telemetry_cost' -%}
    {%- set e = icecore.estimate_telemetry_cost(runs_per_day=state['runs_per_day']) -%}
    {%- do state.update({'estimate': e}) -%}
    {%- if e['status'] == 'ok' -%}
      {{ return('{:,}'.format(e['rows_per_month']) ~ ' rows per month, ' ~ '%.4f' | format(e['credits_per_month'][0]) ~ ' to '
          ~ '%.4f' | format(e['credits_per_month'][1]) ~ ' credits per month') }}
    {%- endif -%}
    {{ return('{:,}'.format(e['rows_per_run']) ~ ' rows per run; give runs_per_day for the monthly estimate') }}
  {%- elif name == 'create_views' -%}
    {{ return('CREATE OR REPLACE VIEW ... COPY GRANTS ' ~ (icecore.create_views(state['schema']) | join(', '))) }}
  {%- elif name == 'create_compat_views' -%}
    {%- set compat_schema = icecore._icecore_compat_schema(state['schema']) -%}
    {%- do state.update({'compat_schema': compat_schema}) -%}
    {{ return('CREATE OR REPLACE VIEW ... COPY GRANTS ' ~ (icecore.create_compat_views(state['schema'], state['compat']) | join(', '))) }}
  {%- endif -%}
  {{ exceptions.raise_compiler_error('icecore: setup step ' ~ name ~ ' is not ready') }}
{% endmacro %}


{#- One record written through ICECORE_EMIT of `schema` (setup's and doctor's schema, which may differ from the
    obs_schema var that icecore.emit uses): emit's own record building, one guarded CALL. Returns the status. -#}
{% macro _icecore_emit_in_schema(schema, kind, message, attributes) %}
  {%- set records = icecore._icecore_emit_records([{'kind': kind, 'message': message, 'attributes': attributes}]) -%}
  {{ return(icecore._icecore_guarded_call(icecore._icecore_procedure('ICECORE_EMIT', schema), records)) }}
{% endmacro %}
