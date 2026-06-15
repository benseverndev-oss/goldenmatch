{#-
  identity_resolve -- look up a record_id's current identity.
  Returns a JSON object: {entity_id, members, recent_events, ...}.

  v0.3 of dbt-goldensuite (closes part of #465). Wraps the existing
  Identity Graph SQL function from packages/rust/extensions on:
    - Postgres   -- `goldenmatch.goldenmatch_identity_resolve` (pgrx)
    - DuckDB     -- `goldenmatch_identity_resolve` (Python UDF)
    - Snowflake  -- `goldenmatch.goldenmatch_identity_resolve` (Snowpark
                    Python UDF; see docs/snowflake-setup.md)

  Args:
    record_id -- the `{source}:{source_pk}` (or hash-fallback) identifier
    db_path   -- optional explicit IdentityStore path. NULL/none defaults
                 to `.goldenmatch/identity.db` on the warehouse host
                 (Postgres/DuckDB) or the bundled wheel resource path
                 (Snowflake).

  Usage in a model:

      SELECT
          customer_id,
          {{ dbt_goldensuite.identity_resolve('customer_id') }}::JSONB AS identity
      FROM {{ ref('raw_customers') }}
-#}

{% macro identity_resolve(record_id, db_path=none) %}
    {{ return(adapter.dispatch(
        'identity_resolve_impl',
        'dbt_goldensuite'
    )(record_id, db_path)) }}
{% endmacro %}


{% macro default__identity_resolve_impl(record_id, db_path) %}
    {{ exceptions.raise_compiler_error(
        "identity_resolve is only supported on postgres, duckdb, and
         snowflake; got adapter=" ~ target.type ~ ".
         For other adapters, query the identity store via REST
         (GET /identities/resolve/<record_id>) or Python."
    ) }}
{% endmacro %}


{% macro postgres__identity_resolve_impl(record_id, db_path) %}
    goldenmatch.goldenmatch_identity_resolve(
        {{ dbt.string_literal(record_id) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}


{% macro duckdb__identity_resolve_impl(record_id, db_path) %}
    goldenmatch_identity_resolve(
        {{ dbt.string_literal(record_id) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}


{% macro snowflake__identity_resolve_impl(record_id, db_path) %}
    goldenmatch.goldenmatch_identity_resolve(
        {{ dbt.string_literal(record_id) }},
        {{ "''" if db_path is none else dbt.string_literal(db_path) }}
    )
{% endmacro %}
