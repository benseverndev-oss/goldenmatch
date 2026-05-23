{#-
  goldencheck_assert -- dbt test macro that runs a GoldenCheck scan
  against a model and fails the test if any finding has severity
  >= the configured floor.

  Closes the v0.2 dbt-goldensuite expansion (#dbt-suite-expand). The
  Postgres + DuckDB backends call the goldencheck DuckDB UDF
  (`goldencheck_scan_table`) or the Postgres extension equivalent.
  Other adapters compile-error with a remediation hint.

  Usage in a dbt model's tests block:

      models:
        - name: customers
          tests:
            - dbt_goldensuite.goldencheck_assert:
                min_severity: warning   # info|warning|error
                ignore_checks: [nullability]    # optional skip list

  The macro returns rows that VIOLATE the assertion (dbt's standard
  test contract: zero rows = pass). When the underlying goldencheck
  scan returns findings at or above `min_severity`, each finding is
  emitted as one row.

  goldencheck (Python) must be installed on the warehouse host for the
  Postgres extension path, OR on the dbt-runner machine for the
  DuckDB path (the UDF runs in-process).
-#}

{# dbt translates `{% macro test_<name> %}` to a schema test the same
   way it does `{% test <name> %}`. We use the macro form so plain
   Jinja2 (unit tests, here) can parse without the dbt extension. #}
{% macro test_goldencheck_assert(
    model,
    min_severity='warning',
    ignore_checks=none,
    domain=none
) %}
    {{ return(adapter.dispatch(
        'goldencheck_assert_impl',
        'dbt_goldensuite'
    )(model, min_severity, ignore_checks, domain)) }}
{% endmacro %}


{% macro default__goldencheck_assert_impl(
    model, min_severity, ignore_checks, domain
) %}
    {{ exceptions.raise_compiler_error(
        "goldencheck_assert is only supported on postgres and duckdb "
        "targets today; got adapter=" ~ target.type ~ ". For other "
        "adapters, run goldencheck out-of-band: "
        "`goldencheck scan <export> --baseline goldencheck_baseline.yaml`."
    ) }}
{% endmacro %}


{% macro postgres__goldencheck_assert_impl(
    model, min_severity, ignore_checks, domain
) %}
    {%- set ignore_list = ignore_checks if ignore_checks else [] -%}
    WITH findings AS (
        SELECT
            (entry->>'check')::TEXT AS check_name,
            (entry->>'severity')::TEXT AS severity,
            (entry->>'column')::TEXT AS column_name,
            (entry->>'message')::TEXT AS message
        FROM jsonb_array_elements(
            goldencheck.scan_table(
                {{ dbt.string_literal(model.identifier) }},
                {{ "NULL" if domain is none else dbt.string_literal(domain) }}
            )::jsonb
        ) AS entry
    )
    SELECT *
    FROM findings
    WHERE severity = ANY(
        ARRAY[
            {%- if min_severity == 'info' %} 'info', 'warning', 'error' {%- endif %}
            {%- if min_severity == 'warning' %} 'warning', 'error' {%- endif %}
            {%- if min_severity == 'error' %} 'error' {%- endif %}
        ]::TEXT[]
    )
    {%- if ignore_list %}
        AND check_name <> ALL(ARRAY[
            {%- for c in ignore_list %}{{ dbt.string_literal(c) }}{% if not loop.last %},{% endif %}{%- endfor %}
        ]::TEXT[])
    {%- endif %}
{% endmacro %}


{% macro duckdb__goldencheck_assert_impl(
    model, min_severity, ignore_checks, domain
) %}
    {%- set ignore_list = ignore_checks if ignore_checks else [] -%}
    WITH findings AS (
        SELECT
            json_extract_string(entry, '$.check') AS check_name,
            json_extract_string(entry, '$.severity') AS severity,
            json_extract_string(entry, '$.column') AS column_name,
            json_extract_string(entry, '$.message') AS message
        FROM (
            SELECT unnest(
                from_json(
                    goldencheck_scan_table(
                        {{ dbt.string_literal(model.identifier) }},
                        {{ "''" if domain is none else dbt.string_literal(domain) }}
                    ),
                    '["JSON"]'
                )
            ) AS entry
        )
    )
    SELECT *
    FROM findings
    WHERE severity IN (
        {%- if min_severity == 'info' %}'info', 'warning', 'error'{%- endif %}
        {%- if min_severity == 'warning' %}'warning', 'error'{%- endif %}
        {%- if min_severity == 'error' %}'error'{%- endif %}
    )
    {%- if ignore_list %}
        AND check_name NOT IN (
            {%- for c in ignore_list %}{{ dbt.string_literal(c) }}{% if not loop.last %},{% endif %}{%- endfor %}
        )
    {%- endif %}
{% endmacro %}
