{% test goldencheck_not_empty(model) %}

{#
  Sanity test: assert the model has at least one row.

  Earlier versions of this package shipped a `goldencheck` test that claimed
  to run the full GoldenCheck scan but actually only checked for emptiness.
  Renamed to be honest. The full GoldenCheck scan is a Python-side workflow
  (encoding detection, anomaly detection, format-ID validation, etc. all
  require Python tooling) and lives in `scripts/run_goldencheck.py` —
  invoke it from CI / Airflow alongside `dbt test`. See
  `examples/airflow/golden_suite_quality_gate.py` in the goldenmatch
  monorepo for a production wiring.

  Usage in schema.yml:
    models:
      - name: orders
        tests:
          - goldencheck_not_empty
#}

select 1
from {{ model }}
having count(*) = 0

{% endtest %}
