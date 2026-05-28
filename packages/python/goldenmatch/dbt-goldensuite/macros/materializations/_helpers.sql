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
            {{ exceptions.raise_compiler_error(
                "output='clusters' is not yet implemented on Snowflake. "
                "v0.6 ships golden-only on Snowflake (matching the "
                "DuckDB v0.4.0 posture); clusters + pairs land in a "
                "follow-up. Use output='golden' or switch to Postgres."
            ) }}
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
            {{ exceptions.raise_compiler_error(
                "output='pairs' is not yet implemented on Snowflake."
            ) }}
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
