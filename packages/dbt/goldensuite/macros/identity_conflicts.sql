{#-
  identity_conflicts -- conflict edges (records flagged as
  conflicts_with) for a dataset as JSON array.

  v0.3 of dbt-goldensuite (closes part of #465).
-#}

{% macro identity_conflicts(dataset, db_path=none) %}
    {{ return(adapter.dispatch(
        'identity_conflicts_impl',
        'dbt_goldensuite'
    )(dataset, db_path)) }}
{% endmacro %}


{% macro default__identity_conflicts_impl(dataset, db_path) %}
    {{ exceptions.raise_compiler_error(
        "identity_conflicts is only supported on postgres, duckdb, and snowflake;
         got adapter=" ~ target.type
    ) }}
{% endmacro %}


{% macro postgres__identity_conflicts_impl(dataset, db_path) %}
    goldenmatch.goldenmatch_identity_conflicts(
        {{ dbt.string_literal(dataset) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}


{% macro duckdb__identity_conflicts_impl(dataset, db_path) %}
    goldenmatch_identity_conflicts(
        {{ dbt.string_literal(dataset) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}


{% macro snowflake__identity_conflicts_impl(dataset, db_path) %}
    goldenmatch.goldenmatch_identity_conflicts(
        {{ dbt.string_literal(dataset) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}
