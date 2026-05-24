{#-
  test_goldencheck_not_empty -- sanity test: assert the model has at
  least one row. Folded into dbt-goldensuite from the standalone
  `dbt-goldencheck` package (closed in PR #464).

  Cross-adapter -- no extension required. Use this as the cheapest
  possible quality gate; pair with `test_goldencheck_assert` /
  `test_goldencheck_health_gate` for richer scans on Postgres /
  DuckDB targets.

  Usage in schema.yml:

      models:
        - name: orders
          tests:
            - dbt_goldensuite.goldencheck_not_empty

  For the full GoldenCheck scan (encoding detection, anomaly
  detection, format-ID validation -- all Python-tooling-only checks)
  use `scripts/run_goldencheck.py` out-of-band, OR
  `test_goldencheck_assert` on Postgres / DuckDB where the extension
  is installed.
-#}

{% macro test_goldencheck_not_empty(model) %}
    select 1
    from {{ model }}
    having count(*) = 0
{% endmacro %}
