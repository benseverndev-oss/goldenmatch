{#-
  goldenmatch_match -- custom dbt materialization for two-table record
  linkage. Runs the goldenmatch matcher (`match_df`) between a TARGET
  (the model body SQL) and a REFERENCE table, materializing the
  best-match linkage as a (target_id, reference_id, score) table.

  Backed by the table-returning `goldenmatch_match_pairs(target_table,
  reference_table, config_json)` UDF (Postgres-first; see Task 2).

  Usage:

      {{ config(
          materialized = 'goldenmatch_match',
          reference    = ref('master_customers'),  -- Relation: ref() or source()
          match_config = 'configs/match.yaml',      -- file path or inline dict; OMIT for zero-config (default)
      ) }}

      SELECT * FROM {{ ref('incoming_customers') }}

  Output columns: target_id, reference_id, score. target_id /
  reference_id are 0-based row indices into the target / reference
  inputs respectively. Join back via `ROW_NUMBER() OVER (...) - 1`.

  ## Flow

  1. Body SQL (the TARGET) -> CREATE TEMP TABLE (dbt's standard
     staging-of-input).
  2. Resolve the config argument: no match_config -> '{}' (zero-config,
     the reliable default -- per a Task 1 finding, a minimal explicit
     fuzzy config yields zero candidate pairs); an explicit match_config
     -> a JSON string literal (file path read + JSON-stringify, OR dict
     JSON-stringify, via the shared goldenmatch_dedupe_config_json helper).
  3. CREATE TABLE <model> AS SELECT * FROM
     goldenmatch_match_pairs(<staging>, <reference>, <config>).
  4. DROP TEMP staging table.

  Out of scope:
  - DuckDB two-table match materialization -- this is Postgres-first.
    On DuckDB call the goldenmatch_match_tables JSON UDF directly.
  - match_mode='all' / full-record output / probabilistic match.
-#}


{#- Helper (`goldenmatch_match_fn_name`) lives in `_helpers.sql` -- split
    so it unit-tests under plain Jinja2 without the materialization block.
    Config JSON-stringification reuses `goldenmatch_dedupe_config_json`. -#}


{% materialization goldenmatch_match, default %}
    {#- Config -#}
    {%- set reference = config.require('reference') -%}
    {%- set match_config = config.get('match_config', none) -%}

    {%- if target.type not in ('postgres', 'snowflake') -%}
        {{ exceptions.raise_compiler_error(
            "goldenmatch_match materialization is Postgres-first -- only "
            "postgres and snowflake are supported today; got adapter=" ~ target.type ~
            ". On DuckDB call the goldenmatch_match_tables JSON UDF directly."
        ) }}
    {%- endif -%}

    {%- set target_relation = this -%}
    {%- set staging_relation = make_temp_relation(target_relation, '__gm_stage') -%}
    {%- set fn_name = dbt_goldensuite.goldenmatch_match_fn_name(target.type) -%}
    {#- Config expression: zero-config '{}' default (reliable; an explicit
        minimal fuzzy config yields zero candidates per a Task 1 finding),
        else JSON-stringify the dict/file via the shared dedupe helper. -#}
    {%- if match_config is none -%}
        {%- set config_expr = dbt.string_literal('{}') -%}
    {%- else -%}
        {%- set config_expr = dbt_goldensuite.goldenmatch_dedupe_config_json(match_config) -%}
    {%- endif -%}

    {#- Step 1: stash the body SQL (the TARGET) into a TEMP table so the
        match function has a real table to scan. -#}
    {%- call statement('create_staging', auto_begin=True) -%}
        CREATE TEMPORARY TABLE {{ staging_relation }} AS (
            {{ sql }}
        );
    {%- endcall -%}

    {#- Step 2: drop any prior incarnation of the target relation. -#}
    {%- call statement('drop_target', auto_begin=True) -%}
        DROP TABLE IF EXISTS {{ target_relation }} CASCADE;
    {%- endcall -%}

    {#- Step 3: invoke the match function (target, reference, config) +
        materialize the matched-pairs table. -#}
    {%- call statement('main', auto_begin=True) -%}
        CREATE TABLE {{ target_relation }} AS
        SELECT * FROM {{ fn_name }}(
            {{ dbt.string_literal(staging_relation.identifier) }},
            {{ dbt.string_literal(reference.identifier) }},
            {{ config_expr }}
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
