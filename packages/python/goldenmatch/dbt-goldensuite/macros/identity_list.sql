{#-
  identity_list -- list identities in a dataset, optionally filtered
  by status. Returns JSON array of identity summaries.

  v0.3 of dbt-goldensuite (closes part of #465).

  Args:
    dataset -- dataset key (NULL/none = all datasets)
    status  -- optional filter: "active", "merged", "split", "deleted".
               NULL/none = no filter.
-#}

{% macro identity_list(dataset=none, status=none, db_path=none) %}
    {{ return(adapter.dispatch(
        'identity_list_impl',
        'dbt_goldensuite'
    )(dataset, status, db_path)) }}
{% endmacro %}


{% macro default__identity_list_impl(dataset, status, db_path) %}
    {{ exceptions.raise_compiler_error(
        "identity_list is only supported on postgres, duckdb, and snowflake;
         got adapter=" ~ target.type
    ) }}
{% endmacro %}


{% macro postgres__identity_list_impl(dataset, status, db_path) %}
    goldenmatch.goldenmatch_identity_list(
        {{ "''" if dataset is none else dbt.string_literal(dataset) }},
        {{ "''" if status is none else dbt.string_literal(status) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}


{% macro duckdb__identity_list_impl(dataset, status, db_path) %}
    goldenmatch_identity_list(
        {{ "''" if dataset is none else dbt.string_literal(dataset) }},
        {{ "''" if status is none else dbt.string_literal(status) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}


{% macro snowflake__identity_list_impl(dataset, status, db_path) %}
    goldenmatch.goldenmatch_identity_list(
        {{ "''" if dataset is none else dbt.string_literal(dataset) }},
        {{ "''" if status is none else dbt.string_literal(status) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}
