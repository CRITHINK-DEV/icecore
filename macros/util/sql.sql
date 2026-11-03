{#-
  The one place icecore builds SQL literals and identifiers (security rule: no value reaches a `$$` body or an
  EXECUTE IMMEDIATE string any other way; spikes/GATE1.md, "Every CALL is guarded").

  _sql(value, 'string')  a single-quoted literal safe everywhere: in a '...' string, in a statement and inside a
                         `$$` body. Backslash and quote are escaped, `$` is written \x24 (so `$$` can never close
                         a body), NUL is written \x00 (a raw NUL cuts a Scripting block short), CR and LF are
                         escaped (the statement stays on one line).
  _sql(value, 'ident')   a double-quoted identifier, from one part or a list of parts (joined with `.`). Every part
                         must match [A-Za-z0-9_$]+ (anchored with \Z: a trailing newline is not accepted); anything
                         else (a `$$`, a quote, a backslash, a space, a control character, an empty part) raises a
                         compiler error naming the value. Use it for names the package or its settings own: the obs
                         schema, procedure and view names, the schema and database arguments of setup and doctor.
                         The quoted form is case sensitive: pass the upper case name for an unquoted Snowflake one.
  _sql(value, 'name')    like 'ident' for the name of an object that already exists in the user's project (a relation
                         or column dbt hands over: it may hold a space, a quote, any case). Parts are double-quoted
                         with embedded quotes doubled; control characters and `$$` raise.

  The old helper names (_icecore_sql_literal, _icecore_schema_literal, _icecore_block_literal, _sql_timestamp,
  _icecore_identifier, _icecore_quoted_name, _icecore_schema_quoted_name) stay for one release as thin wrappers.
-#}

{% macro _sql(value, context='string') %}
  {%- if context == 'string' -%}
    {%- set s = (value | string).replace('\\', '\\\\').replace("'", "\\'").replace('$', '\\x24').replace('\x00', '\\x00') -%}
    {{ return("'" ~ s.replace('\n', '\\n').replace('\r', '\\r') ~ "'") }}
  {%- elif context == 'ident' or context == 'name' -%}
    {%- set error = icecore._sql_error(value, context) -%}
    {%- if error is not none -%}
      {{ exceptions.raise_compiler_error(error) }}
    {%- endif -%}
    {%- set parts = value if (value is iterable and value is not string and value is not mapping) else [value] -%}
    {%- set quoted = [] -%}
    {%- for part in parts -%}
      {%- do quoted.append('"' ~ (part | string).replace('"', '""') ~ '"') -%}
    {%- endfor -%}
    {{ return(quoted | join('.')) }}
  {%- endif -%}
  {{ exceptions.raise_compiler_error("icecore: _sql context must be 'string', 'ident' or 'name', got " ~ (context | string | tojson)) }}
{% endmacro %}


{#- Why _sql(value, context) would raise for an identifier, or none when the value is accepted. Never raises itself. -#}
{% macro _sql_error(value, context='ident') %}
  {%- set parts = value if (value is iterable and value is not string and value is not mapping) else [value] -%}
  {%- if not parts -%}
    {{ return('icecore: invalid SQL identifier ' ~ (value | string | tojson) ~ ': an SQL identifier cannot be empty') }}
  {%- endif -%}
  {%- for part in parts -%}
    {%- set text = part | string -%}
    {%- if part is none or not text -%}
      {{ return('icecore: invalid SQL identifier ' ~ (value | string | tojson) ~ ': an SQL identifier cannot be empty') }}
    {%- endif -%}
    {%- if context == 'ident' and (not modules.re.match('^[A-Za-z0-9_$]+\\Z', text) or '$$' in text) -%}
      {{ return('icecore: invalid SQL identifier ' ~ (text[:60] | tojson) ~ ': only letters, digits, underscore and a single dollar sign are allowed') }}
    {%- elif context == 'name' and (modules.re.search('[\\x00-\\x1f\\x7f]', text) or '$$' in text) -%}
      {{ return('icecore: invalid SQL name ' ~ (text[:60] | tojson) ~ ': no control character and no double dollar sign is allowed') }}
    {%- endif -%}
  {%- endfor -%}
  {{ return(none) }}
{% endmacro %}
