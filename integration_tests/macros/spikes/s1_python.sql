{#-
  Spike S1, encoding E2: a Python procedure with logging extra= attributes and OpenTelemetry custom spans.
  'anonymous'       WITH S1_PY AS PROCEDURE(...) ... CALL S1_PY(...)
  'temp_procedure'  CREATE TEMPORARY PROCEDURE <target db>.<target schema>.S1_PY_<run_id>(...), CALL, DROP
  The handler is static; values are passed as call arguments through s1_sql_literal.
-#}

{% macro _s1_python_handler() -%}
import json
import logging

from opentelemetry import trace


def run(session, run_id, n_logs, n_spans, attrs_json, message, span_start_ns, span_duration_ns):
    attrs = json.loads(attrs_json)
    log = logging.getLogger("icecore")
    for i in range(1, n_logs + 1):
        log.info(f"{message} {run_id} {i}", extra=attrs)
    tracer = trace.get_tracer("icecore")
    for i in range(1, n_spans + 1):
        if span_start_ns:
            # Explicit timings, as dbt node timings would be passed after the fact.
            start = span_start_ns + (i - 1) * span_duration_ns
            span = tracer.start_span("icecore.node", start_time=start, attributes=attrs)
            span.set_attribute("icecore.span_index", i)
            span.add_event("icecore.probe", attrs, timestamp=start)
            span.end(end_time=start + span_duration_ns)
            continue
        with tracer.start_as_current_span("icecore.node") as span:
            span.set_attributes(attrs)
            span.set_attribute("icecore.span_index", i)
            span.add_event("icecore.probe", attrs)
    return "ok"
{%- endmacro %}


{% macro _s1_python_plan(run_id, n_logs, n_spans, attrs, message, via, span_start_ns=0, span_duration_ns=0,
    packages=['snowflake-snowpark-python', 'snowflake-telemetry-python', 'opentelemetry-api']) %}
  {% set sig = 'run_id STRING, n_logs INTEGER, n_spans INTEGER, attrs_json STRING, message STRING, span_start_ns NUMBER(38, 0), span_duration_ns NUMBER(38, 0)' %}
  {% set types = 'STRING, INTEGER, INTEGER, STRING, STRING, NUMBER, NUMBER' %}
  {% set args = [s1_sql_literal(run_id), n_logs | int, n_spans | int,
                 s1_sql_literal(_s1_attrs_json(run_id, attrs)), s1_sql_literal(message),
                 span_start_ns | int, span_duration_ns | int] | join(', ') %}
  {% set spec %}
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ({% for p in packages %}'{{ p }}'{{ ', ' if not loop.last }}{% endfor %})
HANDLER = 'run'
AS $$
{{ _s1_python_handler() }}
$$
  {% endset %}
  {% if via == 'anonymous' %}
    {{ return({'setup': [], 'call': 'WITH S1_PY AS PROCEDURE(' ~ sig ~ ')' ~ spec ~ ' CALL S1_PY(' ~ args ~ ')', 'teardown': []}) }}
  {% elif via == 'temp_procedure' %}
    {% set name = _s1_proc_name('py', run_id) %}
    {{ return({'setup': ['CREATE OR REPLACE TEMPORARY PROCEDURE ' ~ name ~ '(' ~ sig ~ ')' ~ spec],
               'call': 'CALL ' ~ name ~ '(' ~ args ~ ')',
               'teardown': ['DROP PROCEDURE IF EXISTS ' ~ name ~ '(' ~ types ~ ')']}) }}
  {% else %}
    {{ exceptions.raise_compiler_error("s1: unknown via '" ~ via ~ "'") }}
  {% endif %}
{% endmacro %}


{#- span_start_ns > 0 gives every span explicit start/end times (span i starts at
    span_start_ns + (i - 1) * span_duration_ns), as dbt node timings would be passed after the fact. -#}
{% macro s1_python(run_id, n_logs, n_spans, attrs, message='probe', via='anonymous', span_start_ns=0, span_duration_ns=0) %}
  {% do _s1_run(_s1_python_plan(run_id, n_logs, n_spans, attrs, message, via, span_start_ns, span_duration_ns)) %}
{% endmacro %}
