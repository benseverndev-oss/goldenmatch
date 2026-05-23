{#-
  file_field_correction -- dbt macro that emits the SQL to file a
  field-level Correction into the goldenmatch MemoryStore.

  Closes Phase 6C of the #437 surface sync. Calls the Postgres
  extension's `goldenmatch.correction_add(...)` (PR #461) or the
  DuckDB UDF's `goldenmatch_correction_add(...)` (PR #449) based
  on the dbt target adapter type.

  Usage in a dbt model:

      {{ goldenmatch_file_field_correction(
          cluster_id=42,
          field_name='address1',
          original='1 Elm St',
          corrected='1 Elm Street, Apt 4B',
          dataset='customers',
          reason='USPS lookup',
      ) }}

  Other adapters (Snowflake, BigQuery, Redshift, etc.) raise a
  compile error -- there is no MemoryStore-write surface there yet.
-#}

{% macro goldenmatch_file_field_correction(
    cluster_id,
    field_name,
    original,
    corrected,
    dataset,
    reason=none,
    memory_path=none
) %}
    {{ return(adapter.dispatch(
        'goldenmatch_file_field_correction',
        'dbt_goldensuite'
    )(cluster_id, field_name, original, corrected, dataset, reason, memory_path)) }}
{% endmacro %}


{# Default fallback: any adapter we haven't implemented yet. #}
{% macro default__goldenmatch_file_field_correction(
    cluster_id, field_name, original, corrected, dataset, reason, memory_path
) %}
    {{ exceptions.raise_compiler_error(
        "goldenmatch_file_field_correction is only supported on postgres and "
        "duckdb targets today; got adapter=" ~ target.type ~ ". File the "
        "correction via the REST API (POST /api/v1/memory/corrections) or "
        "Python (goldenmatch.add_correction) instead."
    ) }}
{% endmacro %}


{# Postgres: invokes the pgrx-defined goldenmatch.correction_add. The
   caller's role must have goldenmatch_correction_writer granted -- the
   function is REVOKEd from PUBLIC by default. #}
{% macro postgres__goldenmatch_file_field_correction(
    cluster_id, field_name, original, corrected, dataset, reason, memory_path
) %}
    SELECT goldenmatch.correction_add(
        decision        => 'field_correct',
        dataset         => {{ dbt.string_literal(dataset) }},
        cluster_id      => {{ cluster_id }},
        field_name      => {{ dbt.string_literal(field_name) }},
        original_value  => {{ "NULL" if original is none else dbt.string_literal(original) }},
        corrected_value => {{ dbt.string_literal(corrected) }},
        reason          => {{ "NULL" if reason is none else dbt.string_literal(reason) }},
        memory_path     => {{ "NULL" if memory_path is none else dbt.string_literal(memory_path) }}
    )
{% endmacro %}


{# DuckDB: invokes the Python UDF goldenmatch_correction_add. The
   DuckDB UDF signature is positional with an args_json blob for the
   shape-specific kwargs (designed for dbt + DuckDB's tighter
   positional-args constraint). #}
{% macro duckdb__goldenmatch_file_field_correction(
    cluster_id, field_name, original, corrected, dataset, reason, memory_path
) %}
    {%- set args_json -%}
        {
          "cluster_id": {{ cluster_id }},
          "field_name": {{ tojson(field_name) }},
          "corrected_value": {{ tojson(corrected) }}
          {%- if original is not none -%}, "original_value": {{ tojson(original) }}{%- endif -%}
          {%- if reason is not none -%}, "reason": {{ tojson(reason) }}{%- endif -%}
        }
    {%- endset -%}
    SELECT goldenmatch_correction_add(
        'field_correct',
        {{ dbt.string_literal(dataset) }},
        {{ dbt.string_literal(memory_path) if memory_path is not none else "''" }},
        {{ dbt.string_literal(args_json) }}
    )
{% endmacro %}
