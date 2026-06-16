{#-
  infermap_apply -- apply a saved InferMap column mapping to a relation.

  InferMap computes a source->target column mapping in Python (the `infermap`
  package / CLI / MCP server). This macro APPLIES a pre-computed mapping inside
  a dbt model as a plain SELECT. Unlike the goldenmatch_*/goldenflow_* dispatch
  macros it needs NO SQL extension function, so it works on every adapter --
  the mapping itself is the (Python-computed) intelligence; applying it is just
  column projection + aliasing.

  Args:
    relation   -- the source relation, e.g. ref('raw_customers')
    column_map -- a dict { target_column: source_column }, typically the
                  `mappings` output of `infermap map` (load it from a dbt var
                  or a seeded JSON in your project).

  Usage in a model:

      {{ dbt_goldensuite.infermap_apply(
            ref('raw_customers'),
            {'customer_id': 'cust_no', 'email': 'email_addr'}
      ) }}
-#}

{% macro infermap_apply(relation, column_map) %}
    {%- if column_map is not mapping -%}
        {{ exceptions.raise_compiler_error(
            "infermap_apply: `column_map` must be a dict { target: source }") }}
    {%- endif -%}
    {%- if column_map | length == 0 -%}
        {{ exceptions.raise_compiler_error(
            "infermap_apply: `column_map` must not be empty") }}
    {%- endif -%}
    SELECT
    {% for target, source in column_map.items() -%}
        {{ adapter.quote(source) }} AS {{ adapter.quote(target) }}{{ "," if not loop.last }}
    {% endfor -%}
    FROM {{ relation }}
{% endmacro %}
