{#- UC5 telemetry cost estimate (spec section 10, "Telemetry cost surprises").

    dbt run-operation icecore.estimate_telemetry_cost --args '{runs_per_day: 24}'

    Reads only the dbt graph; runs no query. Rows per run, from the write-path design (spec section 4;
    spikes/GATE1.md, "Consequences for the build"):
      - one start record (ICECORE_EMIT, on-run-start), the invocation span and one end LOG (ICECORE_EMIT_RUN, on-run-end);
      - one span per node (models, seeds, snapshots, tests), one node-result LOG per node (var node_results) and
        query_events_per_node query events per node (var query_events); these counts come from
        _icecore_end_run_row_counts, next to end_run's emitter list, and follow the vars;
      - lineage (var lineage): the records of the graph's edges split under the payload cap, as emit_lineage splits them;
      - samples (var samples, off by default): one LOG per failing test with store_failures (failing_tests_per_run);
        lineage and samples counts also come from _icecore_end_run_row_counts and follow their vars;
      - measured rows per CALL: one procedure SPAN per CALL (spikes/S3.md, finding 4) and about 3 untagged Snowpark
        LOG rows per ICECORE_EMIT_RUN CALL (spikes/S1.md, "Python noise"). ICECORE_EMIT_RUN takes at most 512 KB per
        CALL (GATE1.md), so its CALLs are the end-run bytes over 512 KB.
    S3 measured ingestion lag, not volume: no count here comes from it.

    Monitors (own entry `monitors` and own line in the message): the records the icecore generic tests write while the
    tests run, counted from the graph's test nodes and their arguments, with no query. Per run: one icecore.metric span
    event per computed metric (freshness and volume 1 each; column_anomaly one per requested metric; all_columns_anomaly
    one per metric per column, the columns declared on the tested node or `assumed_columns` when none are;
    dimension_anomaly one window_rows per value, unknown at compile time, so max_values plus the `other` group is
    assumed); icecore.anomaly warn events only as `anomalies_per_run` (default 0; no test emits them on its own); one
    icecore.schema_signature per schema_changes test (one record, split when its columns exceed the payload cap, counted
    from the declared columns at about 100 bytes each); one procedure SPAN per ICECORE_EMIT CALL (one CALL per test,
    100 events each). Cost records are in the `cost` entry (var cost). Silence verdicts come from the silence task, not
    from a run: 0 in a healthy project. A project without icecore monitor tests gets no `monitors` entry and the same
    numbers as before.

    Credits: Snowflake Service Consumption Table (effective October 29, 2026), Table 5, Telemetry Data Ingest:
    0.0212 Platform Credits per GB. The range takes bytes_per_record (default 1,500: icecore rows measured in the
    sandbox, 31 Oct 2026, had a median of 1.1 to 1.5 KB serialized) as the low end and the payload cap as the high end.
    GB is taken as 10^9 bytes, which errs high. Until Telemetry Data Ingest replaces Logging on an account, Logging
    is billed (1.25 x serverless compute plus 0.28 credits per 1000 file batches), which a row count cannot predict. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns {status, nodes, runs_per_day, days, records_per_run: {...}, rows_per_run, emit_run_calls, rows_per_month,
    gb_per_month: [low, high], credits_per_month: [low, high], credits_per_gb, message}; status is ok or
    needs_runs_per_day. Logs the message. Never fails. -#}
{% macro estimate_telemetry_cost(runs_per_day=none, nodes=none, query_events_per_node=1,
                                 failing_tests_per_run=0, days=30, bytes_per_record=1500, assumed_columns=10, monitors=true, anomalies_per_run=0) %}
  {%- set counted = icecore._icecore_graph_counts() -%}
  {%- set n = counted['nodes'] if nodes is none else nodes | int -%}
  {%- set cap = icecore._icecore_payload_cap_bytes() -%}
  {%- set rate = icecore._icecore_telemetry_ingest_rate() -%}

  {#- what end_run writes per node, from the macro that sits next to its emitter list, honouring the vars -#}
  {%- set rows = icecore._icecore_end_run_row_counts(n, query_events_per_node, failing_tests_per_run) -%}
  {%- set r = {'start': 1, 'invocation_span': rows['fixed']['invocation_span'], 'end': rows['fixed']['end'],
               'node_spans': rows['fixed']['node_spans'], 'node_results': rows['emitters'].get('node_results', 0),
               'query_events': rows['emitters'].get('query_events', 0),
               'lineage': rows['emitters'].get('lineage', 0), 'samples': rows['emitters'].get('samples', 0)} -%}
  {#- every other emitter that counts rows per node (cost, ...): each non-none entry of rows['emitters'] -#}
  {%- set extra_emitters = {} -%}
  {%- set extra_text = [] -%}
  {%- for name, count in rows['emitters'].items() -%}
    {%- if count is not none and name not in r -%}
      {%- do r.update({name: count}) -%}
      {%- do extra_emitters.update({name: count}) -%}
      {%- if count -%}{%- do extra_text.append(count ~ ' ' ~ name) -%}{%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set end_run_records = r['end'] + r['invocation_span'] + r['node_spans'] + r['node_results'] + r['query_events']
      + r['lineage'] + r['samples'] + (extra_emitters.values() | sum) -%}
  {%- set emit_run_calls = [1, ((end_run_records * bytes_per_record / 524288) | round(0, 'ceil') | int)] | max -%}
  {#- cost sends its metric events live through ICECORE_EMIT, 100 events per CALL, one procedure span per CALL -#}
  {%- set live_calls = ((n / 100) | round(0, 'ceil') | int) if extra_emitters.get('cost') else 0 -%}
  {#- icecore monitor tests write live through ICECORE_EMIT while the tests run: their events, and one procedure SPAN per CALL -#}
  {%- set mon = icecore._icecore_monitor_records(graph.nodes.values() if monitors and graph is defined and graph.nodes is defined else [],
      graph.sources if graph is defined and graph.sources is defined else {}, assumed_columns, anomalies_per_run) -%}
  {%- if mon['tests'] -%}{%- do r.update({'monitors': mon['records']}) -%}{%- endif -%}
  {%- do r.update({'call_overhead': 1 + emit_run_calls * 4 + live_calls + mon['calls']}) -%}
  {%- set rows_per_run = r.values() | sum -%}

  {%- set runs = icecore._icecore_positive_number(runs_per_day) -%}
  {%- set out = {'nodes': n, 'runs_per_day': runs, 'days': days, 'records_per_run': r, 'rows_per_run': rows_per_run,
                 'emit_run_calls': emit_run_calls, 'credits_per_gb': rate['credits_per_gb'],
                 'rows_per_month': none, 'gb_per_month': none, 'credits_per_month': none} -%}
  {%- set per_run = '{:,}'.format(rows_per_run) ~ ' rows per run for ' ~ '{:,}'.format(n) ~ ' nodes (' ~ r['start'] ~ ' start, '
      ~ r['invocation_span'] ~ ' invocation span, ' ~ r['end'] ~ ' end, ' ~ r['node_spans'] ~ ' node spans, '
      ~ r['node_results'] ~ ' node results, ' ~ r['query_events'] ~ ' query events, ' ~ r['lineage']
      ~ ' lineage, ' ~ r['samples'] ~ ' samples, ' ~ ((extra_text | join(', ')) ~ ', ' if extra_text else '') ~ r['call_overhead'] ~ ' rows written by the procedures themselves over '
      ~ (1 + emit_run_calls + live_calls + mon['calls']) ~ ' CALLs)' -%}

  {%- if runs is none -%}
    {%- set lines = [per_run ~ '. Give runs_per_day for the monthly estimate: dbt run-operation icecore.estimate_telemetry_cost --args \'{runs_per_day: <n>}\''] -%}
    {%- do out.update({'status': 'needs_runs_per_day'}) -%}
  {%- else -%}
    {%- set rows_per_month = (rows_per_run * runs * days) | int -%}
    {%- set gb = [rows_per_month * bytes_per_record / 1e9, rows_per_month * cap / 1e9] -%}
    {%- set credits = [gb[0] * rate['credits_per_gb'], gb[1] * rate['credits_per_gb']] -%}
    {%- do out.update({'status': 'ok', 'rows_per_month': rows_per_month, 'gb_per_month': gb, 'credits_per_month': credits}) -%}
    {%- set lines = [
        '{:,}'.format(rows_per_month) ~ ' rows per month (' ~ per_run ~ ' x ' ~ runs ~ ' runs per day x ' ~ days ~ ' days)',
        '%.2f' | format(gb[0]) ~ ' to ' ~ '%.2f' | format(gb[1]) ~ ' GB, ' ~ '%.4f' | format(credits[0]) ~ ' to ' ~ '%.4f' | format(credits[1])
            ~ ' credits per month at ' ~ rate['credits_per_gb'] ~ ' Platform Credits per GB (' ~ bytes_per_record ~ ' bytes per row to the '
            ~ (cap // 1024) ~ ' KB payload cap)',
    ] -%}
  {%- endif -%}
  {%- if mon['tests'] -%}
    {%- do lines.insert(1, 'of the rows per run, ' ~ '{:,}'.format(mon['records']) ~ ' are icecore monitor records from ' ~ mon['tests'] ~ ' monitor tests: '
        ~ mon['metrics'] ~ ' metric events, ' ~ mon['anomalies'] ~ ' anomaly warn events (only what anomalies_per_run says), '
        ~ mon['schema_signatures'] ~ ' schema signatures, 0 silence verdicts (the silence task writes them, 0 in a healthy project), plus '
        ~ mon['calls'] ~ ' procedure spans over ' ~ mon['calls'] ~ ' CALLs (' ~ mon['dimension_tests'] ~ ' dimension tests assume ' ~ mon['dimension_values']
        ~ ' values each at most, ' ~ mon['assumed_column_tests'] ~ " all_columns tests assume " ~ assumed_columns ~ ' columns); cost records are in the cost entry') -%}
  {%- endif -%}
  {%- do lines.append('Sources: rows per run from the write-path design (spec section 4; spikes/GATE1.md, "Consequences for the build"); '
      ~ 'one procedure SPAN per CALL from spikes/S3.md (finding 4), which measured ingestion lag, not volume; 3 Snowpark LOG rows per ICECORE_EMIT_RUN CALL '
      ~ 'from spikes/S1.md ("Python noise"); bytes per row measured on icecore rows in the sandbox (31 Oct 2026); rate from ' ~ rate['source'] ~ '. '
      ~ 'Until Telemetry Data Ingest replaces Logging on your account, Logging is billed instead (1.25 x serverless compute plus 0.28 credits '
      ~ 'per 1000 file batches), which a row count cannot predict: check SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY for the service types '
      ~ 'TELEMETRY_DATA_INGEST and LOGGING. Monitor records are counted from the icecore tests of the graph and their arguments, with the assumptions named in their line') -%}
  {%- set text = 'icecore: telemetry cost: ' ~ (lines | join('.\nicecore: telemetry cost: ')) ~ '.' -%}
  {% do log(text, info=True) %}
  {%- do out.update({'message': text}) -%}
  {{ return(out) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The records of the icecore generic tests, from the graph's test nodes (`nodes`: an iterable of node dicts) and their
    attached nodes' declared columns (`sources`: the graph's sources, a dict). Returns {tests, metrics, anomalies,
    schema_signatures, records, calls, dimension_tests, dimension_values, assumed_column_tests}. Only tests of the
    package icecore count, found by the macro the test depends on (macro.icecore.test_<name>), so a test written without
    the `icecore.` prefix counts too. Anomaly warn events are counted only as `anomalies_per_run` (no test emits them
    on its own). -#}
{% macro _icecore_monitor_records(nodes, sources, assumed_columns=10, anomalies_per_run=0) %}
  {%- set by_id = {} -%}
  {%- for n in nodes -%}{%- do by_id.update({n.get('unique_id'): n}) -%}{%- endfor -%}
  {%- set out = {'tests': 0, 'metrics': 0, 'anomalies': 0, 'schema_signatures': 0, 'records': 0, 'calls': 0,
                 'dimension_tests': 0, 'dimension_values': 0, 'assumed_column_tests': 0} -%}
  {%- set supported = icecore._icecore_column_metric_exprs('c') -%}
  {%- set cap = icecore._icecore_payload_cap_bytes() -%}
  {%- set kinds = ['freshness', 'volume', 'column_anomaly', 'all_columns_anomaly', 'dimension_anomaly', 'schema_changes'] -%}
  {%- for n in by_id.values() if n.get('resource_type') == 'test' and (n.get('test_metadata') or {}).get('name') in kinds
        and ('macro.icecore.test_' ~ n['test_metadata']['name']) in ((n.get('depends_on') or {}).get('macros') or []) -%}
    {%- set kind = n['test_metadata']['name'] -%}
    {%- set kwargs = n['test_metadata'].get('kwargs') or {} -%}
    {%- set attached = by_id.get(n.get('attached_node')) or sources.get(n.get('attached_node')) or {} -%}
    {%- set declared = (attached.get('columns') or {}) | length -%}
    {%- set metrics = 0 -%}
    {%- set signatures = 0 -%}
    {%- if kind in ['freshness', 'volume'] -%}
      {%- set metrics = 1 -%}
    {%- elif kind in ['column_anomaly', 'all_columns_anomaly'] -%}
      {%- set asked = kwargs.get('metrics') -%}
      {%- set asked = icecore._icecore_column_metric_defaults() if asked is none else ([asked] if asked is string else asked) -%}
      {%- set per_column = [] -%}
      {%- for m in asked if m in supported and m not in per_column -%}{%- do per_column.append(m) -%}{%- endfor -%}
      {%- if kind == 'column_anomaly' -%}
        {%- set metrics = per_column | length -%}
      {%- else -%}
        {%- if not declared -%}{%- do out.update({'assumed_column_tests': out['assumed_column_tests'] + 1}) -%}{%- endif -%}
        {%- set excluded = kwargs.get('exclude_columns') -%}
        {%- set excluded = 0 if excluded is none else (1 if excluded is string else excluded | length) -%}
        {%- set columns = [(declared if declared else assumed_columns | int) - excluded, 0] | max -%}
        {%- set metrics = per_column | length * columns -%}
      {%- endif -%}
    {%- elif kind == 'dimension_anomaly' -%}
      {#- one window_rows per value: the most frequent max_values and the rest as `other` -#}
      {%- set values = icecore._icecore_dimension_max_values(kwargs.get('max_values')) + 1 -%}
      {%- set metrics = values -%}
      {%- do out.update({'dimension_tests': out['dimension_tests'] + 1, 'dimension_values': [out['dimension_values'], values] | max}) -%}
    {%- else -%}
      {#- schema_changes: one signature record, split when the JSON of its columns (about 100 bytes each) passes the cap -#}
      {%- set signatures = [1, ((declared * 100 / cap) | round(0, 'ceil') | int)] | max -%}
    {%- endif -%}
    {%- set events = metrics + signatures -%}
    {%- do out.update({'tests': out['tests'] + 1, 'metrics': out['metrics'] + metrics,
        'schema_signatures': out['schema_signatures'] + signatures, 'records': out['records'] + events,
        'calls': out['calls'] + [1, ((events / 100) | round(0, 'ceil') | int)] | max}) -%}
  {%- endfor -%}
  {%- if out['tests'] and anomalies_per_run | int > 0 -%}
    {%- do out.update({'anomalies': anomalies_per_run | int, 'records': out['records'] + anomalies_per_run | int,
        'calls': out['calls'] + ((anomalies_per_run | int / 100) | round(0, 'ceil') | int)}) -%}
  {%- endif -%}
  {{ return(out) }}
{% endmacro %}


{#- Snowflake's published rate for event-table ingestion, with its source. -#}
{% macro _icecore_telemetry_ingest_rate() %}
  {{ return({'credits_per_gb': 0.0212,
             'source': 'the Snowflake Service Consumption Table, effective October 29, 2026, Table 5 (Serverless Feature Table): Telemetry Data Ingest, 0.0212 Platform Credits per GB (https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf)'}) }}
{% endmacro %}


{#- Nodes that get a span (models, seeds, snapshots, tests). -#}
{% macro _icecore_graph_counts() %}
  {%- set ns = namespace(nodes=0) -%}
  {%- if graph is defined and graph.nodes is defined -%}
    {%- for node in graph.nodes.values() if node.resource_type in ('model', 'seed', 'snapshot', 'test') -%}
      {%- set ns.nodes = ns.nodes + 1 -%}
    {%- endfor -%}
  {%- endif -%}
  {{ return({'nodes': ns.nodes}) }}
{% endmacro %}


{#- A number above zero, or none. -#}
{% macro _icecore_positive_number(value) %}
  {%- if value is none or value is boolean -%}{{ return(none) }}{%- endif -%}
  {%- if value is number -%}{{ return(value if value > 0 else none) }}{%- endif -%}
  {%- if modules.re.match('^[0-9]+([.][0-9]+)?$', value | string | trim) -%}
    {%- set v = value | string | trim | float -%}
    {{ return(v if v > 0 else none) }}
  {%- endif -%}
  {{ return(none) }}
{% endmacro %}
