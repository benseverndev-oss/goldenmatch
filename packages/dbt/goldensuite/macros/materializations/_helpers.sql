{#-
  Helper macros for the goldenmatch_dedupe materialization. Lives in
  a separate file so the materialization itself (which contains
  dbt-specific `{% statement %}` / `{% materialization %}` tags that
  plain Jinja2 can't parse) doesn't block unit-testing the helpers
  in isolation.

  Loaded by dbt automatically alongside `goldenmatch_dedupe.sql`.
-#}


{#--- Serialize match_config to a JSON string literal. ---#}
{% macro goldenmatch_dedupe_config_json(match_config) %}
    {%- if match_config is string -%}
        {%- set raw = load_file_contents(match_config) -%}
        {%- if raw is none -%}
            {{ exceptions.raise_compiler_error(
                "match_config file not found: " ~ match_config
            ) }}
        {%- endif -%}
        {{ return(dbt.string_literal(raw)) }}
    {%- elif match_config is mapping -%}
        {{ return(dbt.string_literal(tojson(match_config))) }}
    {%- else -%}
        {{ exceptions.raise_compiler_error(
            "match_config must be a string (file path) or dict (inline config); got "
            ~ match_config | string
        ) }}
    {%- endif -%}
{% endmacro %}


{#--- Pick the warehouse SQL function name for the requested output. ---#}
{% macro goldenmatch_dedupe_fn_name(output_kind, adapter_type) %}
    {%- if adapter_type not in ('postgres', 'duckdb', 'snowflake') -%}
        {{ exceptions.raise_compiler_error(
            "goldenmatch_dedupe materialization is only supported on "
            "postgres, duckdb, and snowflake; got adapter=" ~ adapter_type
        ) }}
    {%- endif -%}
    {%- if output_kind == 'golden' -%}
        {%- if adapter_type == 'postgres' -%}
            {{ return('goldenmatch.goldenmatch_dedupe_full') }}
        {%- elif adapter_type == 'snowflake' -%}
            {{ return('goldenmatch.goldenmatch_dedupe_full') }}
        {%- else -%}
            {{ return('goldenmatch_dedupe_full') }}
        {%- endif -%}
    {%- elif output_kind == 'clusters' -%}
        {%- if adapter_type == 'postgres' -%}
            {{ return('goldenmatch.goldenmatch_dedupe_clusters') }}
        {%- elif adapter_type == 'snowflake' -%}
            {{ return('goldenmatch.goldenmatch_dedupe_clusters') }}
        {%- else -%}
            {{ exceptions.raise_compiler_error(
                "output='clusters' is not yet implemented on DuckDB. "
                "v0.4.0 ships clusters/pairs on Postgres only; DuckDB "
                "UDFs land in v0.4.1. Use output='golden' or switch to "
                "Postgres target."
            ) }}
        {%- endif -%}
    {%- elif output_kind == 'pairs' -%}
        {%- if adapter_type == 'postgres' -%}
            {{ return('goldenmatch.goldenmatch_dedupe_pairs') }}
        {%- elif adapter_type == 'snowflake' -%}
            {{ return('goldenmatch.goldenmatch_dedupe_pairs') }}
        {%- else -%}
            {{ exceptions.raise_compiler_error(
                "output='pairs' is not yet implemented on DuckDB."
            ) }}
        {%- endif -%}
    {%- else -%}
        {{ exceptions.raise_compiler_error(
            "output must be one of: golden, clusters, pairs; got " ~ output_kind
        ) }}
    {%- endif -%}
{% endmacro %}


{#--- Adapter-aware name for the autoconfig (plan) UDF. Snowflake intentionally
       raises: the goldenmatch_autoconfig(table, mode) overload is DuckDB/Postgres
       only in this release, so zero-config (which always emits the 2-arg mode
       call) is unsupported there -- explicit match_config still works on Snowflake
       because it never calls this macro. ---#}
{% macro goldenmatch_autoconfig_fn_name(adapter_type) %}
    {%- if adapter_type == 'postgres' -%}
        {{ return('goldenmatch.goldenmatch_autoconfig') }}
    {%- elif adapter_type == 'duckdb' -%}
        {{ return('goldenmatch_autoconfig') }}
    {%- elif adapter_type == 'snowflake' -%}
        {{ exceptions.raise_compiler_error(
            "Zero-config dedupe (probabilistic=true, or omitting match_config) is "
            "not yet supported on Snowflake -- the goldenmatch_autoconfig(table, mode) "
            "UDF is DuckDB/Postgres only in this release. Pass an explicit "
            "match_config on Snowflake, or use the Python helper."
        ) }}
    {%- else -%}
        {{ exceptions.raise_compiler_error(
            "goldenmatch_dedupe is only supported on postgres, duckdb, snowflake; got " ~ adapter_type
        ) }}
    {%- endif -%}
{% endmacro %}


{#--- The SQL expression for the dedupe config argument:
       explicit match_config -> a JSON string literal (today's path);
       no match_config       -> a scalar-subquery plan call (zero-config). ---#}
{% macro goldenmatch_dedupe_config_expr(match_config, probabilistic, staging_literal, adapter_type) %}
    {%- if match_config is not none and probabilistic -%}
        {{ exceptions.raise_compiler_error(
            "Set either match_config (explicit config) or probabilistic=true "
            "(zero-config Fellegi-Sunter), not both. To use FS with an explicit "
            "config, declare type: probabilistic matchkeys in match_config and drop "
            "the probabilistic flag."
        ) }}
    {%- elif match_config is not none -%}
        {{ return(goldenmatch_dedupe_config_json(match_config)) }}
    {%- else -%}
        {%- set mode = 'probabilistic' if probabilistic else 'standard' -%}
        {%- set fn = goldenmatch_autoconfig_fn_name(adapter_type) -%}
        {{ return('(SELECT ' ~ fn ~ '(' ~ staging_literal ~ ", '" ~ mode ~ "'))") }}
    {%- endif -%}
{% endmacro %}
