{#-
  collect_sla_config(nodes=none) -> list of dicts, one per node that declares meta.sla or meta.alerts:

    {unique_id, name, resource_type,
     expected_every_minutes, grace_minutes, grace_defaulted,   -- none / none / false without meta.sla
     channel, severity}                                         -- none / 'warn' without meta.alerts

  Reads the meta of every node in `graph.nodes`, or of `nodes` when given (tests and callers that
  already hold the nodes). Users declare it under `config: meta:` (top-level `meta:` is deprecated
  on dbt 1.12). dbt merges config() > YAML > dbt_project `+meta` into both `node.meta` and
  `node.config.meta`, so for parsed nodes the two agree; reading `meta` over `config.meta` only
  matters for nodes built by hand. Sources live in `graph.sources` and are not read. Validates as
  it reads: a bad value or an unknown key raises a compile error that names the node, so a typo
  never silently disables an alert. Returns values only: it runs no query and renders no SQL; render_alert_sql and
  render_silence_task_sql consume the result.
-#}
{% macro collect_sla_config(nodes=none) %}
  {% if nodes is none %}
    {% if not execute %}
      {{ return([]) }}
    {% endif %}
    {% set nodes = graph.nodes.values() | list %}
  {% endif %}

  {% set entries = [] %}
  {% for node in nodes %}
    {% set entry = icecore._sla_entry(node) %}
    {% if entry is not none %}
      {% do entries.append(entry) %}
    {% endif %}
  {% endfor %}
  {{ return(entries | sort(attribute='unique_id')) }}
{% endmacro %}


{#-
  Default grace, in minutes, when meta.sla has no `grace`.
  Rule (spec section 10): twice the p95 ingestion lag measured by spike S3, rounded up to a whole minute.
  spikes/S3.md: p95 7.73 s (dbt 1.8.10) and 6.12 s (dbt 1.12.6) over two 10,000-record bursts, so
  2 x 7.73 s = 15.5 s, rounded up to 1 minute. Re-measure and change it here if S3 is re-run.
-#}
{% macro _sla_default_grace_minutes() %}
  {{ return(1) }}
{% endmacro %}


{#- One node -> its entry, or none when it declares neither meta.sla nor meta.alerts. -#}
{% macro _sla_entry(node) %}
  {% set meta = {} %}
  {% do meta.update((node.get('config') or {}).get('meta') or {}) %}
  {% do meta.update(node.get('meta') or {}) %}
  {% set sla = meta.get('sla') %}
  {% set alerts = meta.get('alerts') %}
  {% if sla is none and alerts is none %}
    {{ return(none) }}
  {% endif %}

  {% set entry = {
    'unique_id': node.get('unique_id'),
    'name': node.get('name'),
    'resource_type': node.get('resource_type'),
    'expected_every_minutes': none,
    'grace_minutes': none,
    'grace_defaulted': false,
    'channel': none,
    'severity': 'warn',
  } %}

  {% if sla is not none %}
    {% if sla is not mapping %}
      {% do icecore._sla_fail(node, "meta.sla must be a mapping like {expected_every: 1h, grace: 15m}") %}
    {% endif %}
    {% set unknown = sla.keys() | reject('in', ['expected_every', 'grace']) | list %}
    {% if unknown %}
      {% do icecore._sla_fail(node, "meta.sla has unknown key(s) " ~ (unknown | join(', ')) ~ "; allowed: expected_every, grace") %}
    {% endif %}
    {% if sla.get('expected_every') is none %}
      {% do icecore._sla_fail(node, "meta.sla needs expected_every (e.g. 1h)") %}
    {% endif %}
    {% set every = icecore._sla_minutes(node, 'meta.sla.expected_every', sla.get('expected_every')) %}
    {% do entry.update({'expected_every_minutes': every}) %}
    {% if sla.get('grace') is none %}
      {#- The default never exceeds the schedule itself (a 5-minute schedule gets 5 minutes of grace). -#}
      {% do entry.update({'grace_minutes': [icecore._sla_default_grace_minutes(), every] | min, 'grace_defaulted': true}) %}
    {% else %}
      {% set grace = icecore._sla_minutes(node, 'meta.sla.grace', sla.get('grace')) %}
      {% if grace > every %}
        {% do icecore._sla_fail(node, "meta.sla.grace '" ~ sla.get('grace') ~ "' is longer than expected_every '" ~ sla.get('expected_every') ~ "'; a missed run would go unnoticed for more than a whole period") %}
      {% endif %}
      {% do entry.update({'grace_minutes': grace}) %}
    {% endif %}
  {% endif %}

  {% if alerts is not none %}
    {% if alerts is not mapping %}
      {% do icecore._sla_fail(node, "meta.alerts must be a mapping like {channel: data-platform, severity: warn}") %}
    {% endif %}
    {% set unknown = alerts.keys() | reject('in', ['channel', 'severity']) | list %}
    {% if unknown %}
      {% do icecore._sla_fail(node, "meta.alerts has unknown key(s) " ~ (unknown | join(', ')) ~ "; allowed: channel, severity") %}
    {% endif %}
    {% set channel = alerts.get('channel') %}
    {% if channel is not none and (channel is not string or (channel | trim) == '') %}
      {% do icecore._sla_fail(node, "meta.alerts.channel must be a non-empty string") %}
    {% endif %}
    {% set severity = alerts.get('severity', 'warn') %}
    {% if severity is not string or (severity | trim | lower) not in ['warn', 'error'] %}
      {% do icecore._sla_fail(node, "meta.alerts.severity '" ~ severity ~ "' must be warn or error") %}
    {% endif %}
    {% do entry.update({'channel': channel, 'severity': severity | trim | lower}) %}
  {% endif %}

  {{ return(entry) }}
{% endmacro %}


{#- A duration value -> whole minutes, or a compile error naming the node and the key. -#}
{% macro _sla_minutes(node, key, value) %}
  {% set minutes = icecore._sla_parse_duration(value) %}
  {% if minutes is none %}
    {% do icecore._sla_fail(node, key ~ " '" ~ value ~ "' is not a valid duration; use whole numbers with a unit m, h, d or w, e.g. 15m, 1h, 1h30m, 1d") %}
  {% endif %}
  {{ return(minutes) }}
{% endmacro %}


{#-
  '15m' | '1h' | '1h30m' | '2d' | '1w' -> minutes (int > 0); anything else -> none.
  Case-insensitive, surrounding spaces ignored. No regex, so it runs on dbt Core and Fusion alike.
-#}
{% macro _sla_parse_duration(value) %}
  {% if value is not string %}
    {{ return(none) }}
  {% endif %}
  {% set units = {'m': 1, 'h': 60, 'd': 1440, 'w': 10080} %}
  {% set text = value | trim | lower %}
  {% set ns = namespace(digits='', total=0, ok=(text | length) > 0) %}
  {% for ch in text %}
    {% if ch in '0123456789' %}
      {% set ns.digits = ns.digits ~ ch %}
    {% elif ch in units and ns.digits != '' %}
      {% set ns.total = ns.total + (ns.digits | int) * units[ch] %}
      {% set ns.digits = '' %}
    {% else %}
      {% set ns.ok = false %}
    {% endif %}
  {% endfor %}
  {% if not ns.ok or ns.digits != '' or ns.total <= 0 %}
    {{ return(none) }}
  {% endif %}
  {{ return(ns.total) }}
{% endmacro %}


{% macro _sla_fail(node, problem) %}
  {% do exceptions.raise_compiler_error(
    "icecore: " ~ (node.get('resource_type') or 'node') ~ " '" ~ node.get('name') ~ "' ("
    ~ node.get('unique_id') ~ "): " ~ problem
  ) %}
{% endmacro %}
