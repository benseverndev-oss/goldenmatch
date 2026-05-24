{#-
  goldencheck_health_gate -- dbt test that fails when a model's
  GoldenCheck health score falls below `min_score`.

  GoldenCheck's `health_score()` returns a number 0-100. Common gates:
    90+  -> "A" -- production-ready
    80-89 -> "B" -- minor issues, allow with monitoring
    70-79 -> "C" -- needs attention before downstream consumption
    <70  -> "D"/"F" -- block

  Usage:

      models:
        - name: orders_daily
          tests:
            - dbt_goldensuite.goldencheck_health_gate:
                min_score: 85

  Returns rows for the violating model when score < min_score (one row
  with the score), zero rows on pass.
-#}

{# `{% macro test_<name> %}` is equivalent to dbt's `{% test %}` sugar
   but parses under plain Jinja2 for the unit-test harness. #}
{% macro test_goldencheck_health_gate(model, min_score=80) %}
    {{ return(adapter.dispatch(
        'goldencheck_health_gate_impl',
        'dbt_goldensuite'
    )(model, min_score)) }}
{% endmacro %}


{% macro default__goldencheck_health_gate_impl(model, min_score) %}
    {{ exceptions.raise_compiler_error(
        "goldencheck_health_gate is only supported on postgres and "
        "duckdb today; got adapter=" ~ target.type
    ) }}
{% endmacro %}


{% macro postgres__goldencheck_health_gate_impl(model, min_score) %}
    SELECT
        score,
        {{ min_score }} AS min_score,
        score < {{ min_score }} AS failed
    FROM (
        SELECT goldencheck.health_score(
            {{ dbt.string_literal(model.identifier) }}
        ) AS score
    ) AS h
    WHERE score < {{ min_score }}
{% endmacro %}


{% macro duckdb__goldencheck_health_gate_impl(model, min_score) %}
    SELECT
        score,
        {{ min_score }} AS min_score,
        score < {{ min_score }} AS failed
    FROM (
        SELECT goldencheck_health_score(
            {{ dbt.string_literal(model.identifier) }}
        ) AS score
    ) AS h
    WHERE score < {{ min_score }}
{% endmacro %}
