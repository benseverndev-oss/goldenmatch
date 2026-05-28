{#-
  goldenmatch_dedupe -- custom dbt materialization that runs the
  goldenmatch entity-resolution pipeline against a model's body SQL
  and materializes one of three output shapes (golden / clusters /
  pairs).

  v0.4 of dbt-goldensuite (closes part of #465 Tier 1.2).

  Usage:

      {{ config(
          materialized = 'goldenmatch_dedupe',
          match_config = 'configs/customers.yaml',  -- file path or inline dict
          output = 'golden',                         -- golden|clusters|pairs
          memory_db_path = none,                     -- optional MemoryStore path
      ) }}

      SELECT * FROM {{ ref('stg_customers_clean') }}

  ## Flow

  1. Body SQL -> CREATE TEMP TABLE (dbt's standard staging-of-input).
  2. Resolve match_config: file path (read + JSON-stringify) OR dict
     literal (JSON-stringify directly).
  3. Pick the right warehouse function for the requested output:
     - golden   -> goldenmatch_dedupe_full (Postgres + DuckDB + Snowflake)
     - clusters -> goldenmatch_dedupe_clusters (Postgres only in v0.6)
     - pairs    -> goldenmatch_dedupe_pairs   (Postgres only in v0.6)
  4. CREATE TABLE <model> AS SELECT * FROM <function>(...)
  5. DROP TEMP staging table.

  Out of scope for v0.6:
  - DuckDB + Snowflake UDFs for dedupe_clusters / dedupe_pairs --
    these exist on the Postgres extension but not on the
    DuckDB / Snowflake Python UDF surface. Both ship golden-only;
    clusters + pairs are a follow-up.
  - BigQuery / Redshift -- no goldenmatch extension; use
    `goldenmatch.dedupe_df()` Python helper out-of-band instead.
-#}


{#- Helpers (`goldenmatch_dedupe_config_json`, `goldenmatch_dedupe_fn_name`)
    live in `_helpers.sql` -- split so they unit-test under plain Jinja2
    without the materialization block. -#}


{% materialization goldenmatch_dedupe, default %}
    {#- Required config -#}
    {%- set match_config = config.require('match_config') -%}
    {%- set output_kind = config.get('output', 'golden') -%}
    {%- set memory_db_path = config.get('memory_db_path', none) -%}

    {%- if target.type not in ('postgres', 'duckdb', 'snowflake') -%}
        {{ exceptions.raise_compiler_error(
            "goldenmatch_dedupe materialization is only supported on "
            "postgres, duckdb, and snowflake today; got adapter=" ~ target.type ~
            ". For other adapters, use the Python helper "
            "`dbt_goldensuite.materialize.run_goldenmatch_dedupe`."
        ) }}
    {%- endif -%}

    {%- set target_relation = this -%}
    {%- set staging_relation = make_temp_relation(target_relation, '__gm_stage') -%}
    {%- set config_json_literal = dbt_goldensuite.goldenmatch_dedupe_config_json(match_config) -%}
    {%- set fn_name = dbt_goldensuite.goldenmatch_dedupe_fn_name(output_kind, target.type) -%}

    {#- Step 1: stash body SQL into a TEMP table so the goldenmatch
        function has a real table to scan. -#}
    {%- call statement('create_staging', auto_begin=True) -%}
        CREATE TEMPORARY TABLE {{ staging_relation }} AS (
            {{ sql }}
        );
    {%- endcall -%}

    {#- Step 2: drop any prior incarnation of the target relation. -#}
    {%- call statement('drop_target', auto_begin=True) -%}
        DROP TABLE IF EXISTS {{ target_relation }} CASCADE;
    {%- endcall -%}

    {#- Step 3: invoke the dedupe function + materialize. -#}
    {%- call statement('main', auto_begin=True) -%}
        CREATE TABLE {{ target_relation }} AS
        SELECT * FROM {{ fn_name }}(
            {{ dbt.string_literal(staging_relation.identifier) }},
            {{ config_json_literal }}
        );
    {%- endcall -%}

    {#- Step 4: drop staging. Inside a transaction so a failure leaves
        the temp table for diagnosis. -#}
    {%- call statement('drop_staging', auto_begin=True) -%}
        DROP TABLE IF EXISTS {{ staging_relation }};
    {%- endcall -%}

    {{ adapter.commit() }}
    {{ return({'relations': [target_relation]}) }}
{% endmaterialization %}
