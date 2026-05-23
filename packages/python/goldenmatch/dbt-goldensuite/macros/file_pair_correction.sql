{#-
  file_pair_correction -- dbt macro that emits the SQL to file a
  pair-level Correction (approve/reject) into the goldenmatch
  MemoryStore.

  Companion to `file_field_correction`. Same adapter dispatch shape:
  Postgres uses named args via `goldenmatch.correction_add(...)`;
  DuckDB uses positional args + an `args_json` blob.

  Usage in a dbt model:

      {{ goldenmatch_file_pair_correction(
          id_a=42, id_b=99, decision='approve',
          dataset='customers', reason='same person, manual review',
      ) }}
-#}

{% macro goldenmatch_file_pair_correction(
    id_a,
    id_b,
    decision,
    dataset,
    reason=none,
    matchkey_name=none,
    memory_path=none
) %}
    {%- if decision not in ('approve', 'reject') -%}
        {{ exceptions.raise_compiler_error(
            "decision must be 'approve' or 'reject'; got " ~ decision
        ) }}
    {%- endif -%}
    {{ return(adapter.dispatch(
        'goldenmatch_file_pair_correction',
        'dbt_goldensuite'
    )(id_a, id_b, decision, dataset, reason, matchkey_name, memory_path)) }}
{% endmacro %}


{% macro default__goldenmatch_file_pair_correction(
    id_a, id_b, decision, dataset, reason, matchkey_name, memory_path
) %}
    {{ exceptions.raise_compiler_error(
        "goldenmatch_file_pair_correction is only supported on postgres and "
        "duckdb targets today; got adapter=" ~ target.type
    ) }}
{% endmacro %}


{% macro postgres__goldenmatch_file_pair_correction(
    id_a, id_b, decision, dataset, reason, matchkey_name, memory_path
) %}
    SELECT goldenmatch.correction_add(
        decision      => {{ dbt.string_literal(decision) }},
        dataset       => {{ dbt.string_literal(dataset) }},
        id_a          => {{ id_a }},
        id_b          => {{ id_b }},
        reason        => {{ "NULL" if reason is none else dbt.string_literal(reason) }},
        matchkey_name => {{ "NULL" if matchkey_name is none else dbt.string_literal(matchkey_name) }},
        memory_path   => {{ "NULL" if memory_path is none else dbt.string_literal(memory_path) }}
    )
{% endmacro %}


{% macro duckdb__goldenmatch_file_pair_correction(
    id_a, id_b, decision, dataset, reason, matchkey_name, memory_path
) %}
    {%- set args_json -%}
        {
          "id_a": {{ id_a }},
          "id_b": {{ id_b }}
          {%- if reason is not none -%}, "reason": {{ tojson(reason) }}{%- endif -%}
          {%- if matchkey_name is not none -%}, "matchkey_name": {{ tojson(matchkey_name) }}{%- endif -%}
        }
    {%- endset -%}
    SELECT goldenmatch_correction_add(
        {{ dbt.string_literal(decision) }},
        {{ dbt.string_literal(dataset) }},
        {{ dbt.string_literal(memory_path) if memory_path is not none else "''" }},
        {{ dbt.string_literal(args_json) }}
    )
{% endmacro %}
