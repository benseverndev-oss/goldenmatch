{#-
  identity_view -- full IdentityView JSON for an entity_id.
  Returns: {entity_id, status, members, edges, events, aliases}.

  v0.3 of dbt-goldensuite (closes part of #465).
-#}

{% macro identity_view(entity_id, db_path=none) %}
    {{ return(adapter.dispatch(
        'identity_view_impl',
        'dbt_goldensuite'
    )(entity_id, db_path)) }}
{% endmacro %}


{% macro default__identity_view_impl(entity_id, db_path) %}
    {{ exceptions.raise_compiler_error(
        "identity_view is only supported on postgres, duckdb, and snowflake;
         got adapter=" ~ target.type
    ) }}
{% endmacro %}


{% macro postgres__identity_view_impl(entity_id, db_path) %}
    goldenmatch.goldenmatch_identity_view(
        {{ dbt.string_literal(entity_id) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}


{% macro duckdb__identity_view_impl(entity_id, db_path) %}
    goldenmatch_identity_view(
        {{ dbt.string_literal(entity_id) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}


{% macro snowflake__identity_view_impl(entity_id, db_path) %}
    goldenmatch.goldenmatch_identity_view(
        {{ dbt.string_literal(entity_id) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}
