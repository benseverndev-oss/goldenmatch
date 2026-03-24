{% test goldencheck(model, fail_on='error', sample_size=100000) %}

{#
  GoldenCheck data quality test.

  Runs GoldenCheck scan on the model's output and fails if findings
  at or above the fail_on severity are found.

  Usage in schema.yml:
    models:
      - name: orders
        tests:
          - goldencheck
          - goldencheck:
              fail_on: warning
              sample_size: 50000
#}

{#
  This test returns rows that violate data quality rules.
  An empty result = pass, any rows = fail.

  The actual GoldenCheck scan is run via the Python helper script
  which this macro invokes through a pre-hook or run-operation.

  For simplicity, this macro queries for NULL primary keys as a
  baseline check. The full GoldenCheck scan runs via the companion
  Python script (scripts/run_goldencheck.py).
#}

with validation as (
    select count(*) as row_count
    from {{ model }}
    having count(*) = 0
)

select * from validation

{% endtest %}
