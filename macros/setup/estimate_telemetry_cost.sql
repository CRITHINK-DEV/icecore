{#- UC5 telemetry cost estimate (spec section 10, "Telemetry cost surprises").

    dbt run-operation icecore.estimate_telemetry_cost --args '{runs_per_day: 24}'

    Reads only the dbt graph; runs no query. Rows per run, from the write-path design (spec section 4;
    spikes/GATE1.md, "Consequences for the build"):
      - one start record (ICECORE_EMIT, on-run-start) and one end LOG (ICECORE_EMIT_RUN, on-run-end);
      - one span per node (models, seeds, snapshots, tests) and query_events_per_node query events per node;
      - lineage: one record per run, one more per payload cap (64 KB by default) of edges, sized from the graph;
      - samples: one LOG per failing test (failing_tests_per_run; off by default for warn tests);
      - measured rows per CALL: one procedure SPAN per CALL (spikes/S3.md, finding 4) and about 3 untagged Snowpark
        LOG rows per ICECORE_EMIT_RUN CALL (spikes/S1.md, "Python noise"). ICECORE_EMIT_RUN takes at most 512 KB per
        CALL (GATE1.md), so its CALLs are the end-run bytes over 512 KB.
    S3 measured ingestion lag, not volume: no count here comes from it.

    Credits: Snowflake Service Consumption Table (effective October 29, 2026), Table 5, Telemetry Data Ingest:
    0.0212 Platform Credits per GB. The range takes bytes_per_record (default 1,500: icecore rows measured in the
    sandbox, 31 Oct 2026, had a median of 1.1 to 1.5 KB serialized) as the low end and the payload cap as the high end.
    GB is taken as 10^9 bytes, which errs high. Until Telemetry Data Ingest replaces Logging on an account, Logging
    is billed (1.25 x serverless compute plus 0.28 credits per 1000 file batches), which a row count cannot predict. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns {status, nodes, runs_per_day, days, records_per_run: {...}, rows_per_run, emit_run_calls, rows_per_month,
    gb_per_month: [low, high], credits_per_month: [low, high], credits_per_gb, message}; status is ok or
    needs_runs_per_day. Logs the message. Never fails. -#}
{% macro estimate_telemetry_cost(runs_per_day=none, nodes=none, query_events_per_node=1, lineage=true,
                                 failing_tests_per_run=0, days=30, bytes_per_record=1500) %}
  {%- set counted = icecore._icecore_graph_counts() -%}
  {%- set n = counted['nodes'] if nodes is none else nodes | int -%}
  {%- set cap = icecore._icecore_payload_cap_bytes() -%}
  {%- set rate = icecore._icecore_telemetry_ingest_rate() -%}

  {%- set lineage_records = ([1, ((counted['edge_bytes'] / cap) | round(0, 'ceil') | int)] | max) if lineage else 0 -%}
  {%- set r = {'start': 1, 'end': 1, 'node_spans': n, 'query_events': n * (query_events_per_node | int),
               'lineage': lineage_records, 'samples': failing_tests_per_run | int} -%}
  {%- set end_run_records = r['end'] + r['node_spans'] + r['query_events'] + r['lineage'] + r['samples'] -%}
  {%- set emit_run_calls = [1, ((end_run_records * bytes_per_record / 524288) | round(0, 'ceil') | int)] | max -%}
  {%- do r.update({'call_overhead': 1 + emit_run_calls * 4}) -%}
  {%- set rows_per_run = r.values() | sum -%}

  {%- set runs = icecore._icecore_positive_number(runs_per_day) -%}
  {%- set out = {'nodes': n, 'runs_per_day': runs, 'days': days, 'records_per_run': r, 'rows_per_run': rows_per_run,
                 'emit_run_calls': emit_run_calls, 'credits_per_gb': rate['credits_per_gb'],
                 'rows_per_month': none, 'gb_per_month': none, 'credits_per_month': none} -%}
  {%- set per_run = '{:,}'.format(rows_per_run) ~ ' rows per run for ' ~ '{:,}'.format(n) ~ ' nodes (' ~ r['start'] ~ ' start, '
      ~ r['end'] ~ ' end, ' ~ r['node_spans'] ~ ' node spans, ' ~ r['query_events'] ~ ' query events, ' ~ r['lineage']
      ~ ' lineage, ' ~ r['samples'] ~ ' samples, ' ~ r['call_overhead'] ~ ' rows written by the procedures themselves over '
      ~ (1 + emit_run_calls) ~ ' CALLs)' -%}

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
  {%- do lines.append('Sources: rows per run from the write-path design (spec section 4; spikes/GATE1.md, "Consequences for the build"); '
      ~ 'one procedure SPAN per CALL from spikes/S3.md (finding 4), which measured ingestion lag, not volume; 3 Snowpark LOG rows per ICECORE_EMIT_RUN CALL '
      ~ 'from spikes/S1.md ("Python noise"); bytes per row measured on icecore rows in the sandbox (31 Oct 2026); rate from ' ~ rate['source'] ~ '. '
      ~ 'Until Telemetry Data Ingest replaces Logging on your account, Logging is billed instead (1.25 x serverless compute plus 0.28 credits '
      ~ 'per 1000 file batches), which a row count cannot predict: check SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY for the service types '
      ~ 'TELEMETRY_DATA_INGEST and LOGGING. Not included: icecore monitor metrics (one span event per metric per run)') -%}
  {%- set text = 'icecore: telemetry cost: ' ~ (lines | join('.\nicecore: telemetry cost: ')) ~ '.' -%}
  {% do log(text, info=True) %}
  {%- do out.update({'message': text}) -%}
  {{ return(out) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- Snowflake's published rate for event-table ingestion, with its source. -#}
{% macro _icecore_telemetry_ingest_rate() %}
  {{ return({'credits_per_gb': 0.0212,
             'source': 'the Snowflake Service Consumption Table, effective October 29, 2026, Table 5 (Serverless Feature Table): Telemetry Data Ingest, 0.0212 Platform Credits per GB (https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf)'}) }}
{% endmacro %}


{#- Nodes that get a span (models, seeds, snapshots, tests) and the bytes of their lineage edges as emitted. -#}
{% macro _icecore_graph_counts() %}
  {%- set ns = namespace(nodes=0, edge_bytes=0) -%}
  {%- if graph is defined and graph.nodes is defined -%}
    {%- for node in graph.nodes.values() if node.resource_type in ('model', 'seed', 'snapshot', 'test') -%}
      {%- set ns.nodes = ns.nodes + 1 -%}
      {%- for parent in (node.depends_on or {}).get('nodes') or [] -%}
        {%- set ns.edge_bytes = ns.edge_bytes + (tojson([parent, node.unique_id]) | length) + 1 -%}
      {%- endfor -%}
    {%- endfor -%}
  {%- endif -%}
  {{ return({'nodes': ns.nodes, 'edge_bytes': ns.edge_bytes}) }}
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
