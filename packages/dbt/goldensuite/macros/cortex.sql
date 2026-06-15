{#-
  Snowflake Cortex macros -- pure-SQL wrappers around the in-warehouse
  embedding + vector-similarity functions. Pairs with the
  ``snowflake_cortex`` embedding provider in ``goldenmatch.embeddings``
  so the same model + dim is used whether the embedder runs from
  Python or directly in a dbt model.

  ## Why this exists

  The Snowpark Python UDF path (docs/snowflake-setup.md) ships
  pure-Python embeddings. Snowflake Cortex runs embeddings inside
  the customer's Snowflake account -- no data egress, no separate
  API key, billed against the warehouse instead of an external
  vendor. The macros below expose Cortex as a first-class dbt
  citizen so users can pre-materialize VECTOR columns and use them
  with the dedupe materialization's ``cortex_cosine`` matchkey
  scorer.

  ## Adapter coverage

  All macros are Snowflake-only. The default branch raises a
  compiler error pointing at ``goldenmatch.embeddings`` for
  out-of-band Python embedding. There is no Postgres / DuckDB
  equivalent because Cortex is a Snowflake-platform feature.

  ## Usage

      {{ config(materialized='table') }}
      select
          customer_id,
          {{ dbt_goldensuite.cortex_embed_768(
              "name || ' ' || trim(address)",
              model='snowflake-arctic-embed-m-v1.5'
          ) }} as identity_vec
      from {{ ref('raw_customers') }}

  Then score pairs of vectors in a downstream model:

      select
          a.customer_id as id_a,
          b.customer_id as id_b,
          {{ dbt_goldensuite.cortex_cosine_similarity(
              'a.identity_vec', 'b.identity_vec'
          ) }} as similarity
      from {{ ref('customers_embedded') }} a
      cross join {{ ref('customers_embedded') }} b
      where a.customer_id < b.customer_id
        and {{ dbt_goldensuite.cortex_cosine_similarity(
                'a.identity_vec', 'b.identity_vec'
            ) }} >= 0.85
-#}


{#--- cortex_embed_768 ---------------------------------------------------#}
{% macro cortex_embed_768(column, model='snowflake-arctic-embed-m-v1.5') %}
    {{ return(adapter.dispatch(
        'cortex_embed_768_impl', 'dbt_goldensuite'
    )(column, model)) }}
{% endmacro %}

{% macro default__cortex_embed_768_impl(column, model) %}
    {{ exceptions.raise_compiler_error(
        "cortex_embed_768 is Snowflake-only -- Cortex is a Snowflake
         platform feature with no equivalent on other adapters.
         For out-of-band embedding, use
         `goldenmatch.embeddings.embed_records(texts, provider='snowflake_cortex')`
         or any other provider ('vertex' / 'openai' / 'local' / 'inhouse')."
    ) }}
{% endmacro %}

{% macro snowflake__cortex_embed_768_impl(column, model) %}
    SNOWFLAKE.CORTEX.EMBED_TEXT_768({{ dbt.string_literal(model) }}, {{ column }})
{% endmacro %}


{#--- cortex_embed_1024 --------------------------------------------------#}
{% macro cortex_embed_1024(column, model='snowflake-arctic-embed-l-v2.0') %}
    {{ return(adapter.dispatch(
        'cortex_embed_1024_impl', 'dbt_goldensuite'
    )(column, model)) }}
{% endmacro %}

{% macro default__cortex_embed_1024_impl(column, model) %}
    {{ exceptions.raise_compiler_error(
        "cortex_embed_1024 is Snowflake-only. See `cortex_embed_768`
         docstring for out-of-band alternatives."
    ) }}
{% endmacro %}

{% macro snowflake__cortex_embed_1024_impl(column, model) %}
    SNOWFLAKE.CORTEX.EMBED_TEXT_1024({{ dbt.string_literal(model) }}, {{ column }})
{% endmacro %}


{#--- cortex_embed (dim-dispatched) --------------------------------------#}
{#- Convenience wrapper that picks the right EMBED_TEXT_<dim> based on
    the requested dim. Keeps user-facing config terse:
        {{ cortex_embed('name', model='e5-base-v2', dim=768) }} -#}
{% macro cortex_embed(column, model, dim=768) %}
    {%- if dim == 768 -%}
        {{ return(cortex_embed_768(column, model)) }}
    {%- elif dim == 1024 -%}
        {{ return(cortex_embed_1024(column, model)) }}
    {%- else -%}
        {{ exceptions.raise_compiler_error(
            "cortex_embed dim must be 768 or 1024; got " ~ dim ~ ".
             Snowflake Cortex ships EMBED_TEXT_768 and EMBED_TEXT_1024;
             other widths require a different provider."
        ) }}
    {%- endif -%}
{% endmacro %}


{#--- cortex_cosine_similarity ------------------------------------------#}
{% macro cortex_cosine_similarity(vec_a, vec_b) %}
    {{ return(adapter.dispatch(
        'cortex_cosine_similarity_impl', 'dbt_goldensuite'
    )(vec_a, vec_b)) }}
{% endmacro %}

{% macro default__cortex_cosine_similarity_impl(vec_a, vec_b) %}
    {{ exceptions.raise_compiler_error(
        "cortex_cosine_similarity is Snowflake-only. Cosine similarity
         on numpy/polars vectors is available via
         `goldenmatch.core.scorer` for non-Snowflake targets."
    ) }}
{% endmacro %}

{% macro snowflake__cortex_cosine_similarity_impl(vec_a, vec_b) %}
    VECTOR_COSINE_SIMILARITY({{ vec_a }}, {{ vec_b }})
{% endmacro %}


{#--- cortex_l2_distance -----------------------------------------------#}
{% macro cortex_l2_distance(vec_a, vec_b) %}
    {{ return(adapter.dispatch(
        'cortex_l2_distance_impl', 'dbt_goldensuite'
    )(vec_a, vec_b)) }}
{% endmacro %}

{% macro default__cortex_l2_distance_impl(vec_a, vec_b) %}
    {{ exceptions.raise_compiler_error(
        "cortex_l2_distance is Snowflake-only."
    ) }}
{% endmacro %}

{% macro snowflake__cortex_l2_distance_impl(vec_a, vec_b) %}
    VECTOR_L2_DISTANCE({{ vec_a }}, {{ vec_b }})
{% endmacro %}


{#--- cortex_inner_product ---------------------------------------------#}
{% macro cortex_inner_product(vec_a, vec_b) %}
    {{ return(adapter.dispatch(
        'cortex_inner_product_impl', 'dbt_goldensuite'
    )(vec_a, vec_b)) }}
{% endmacro %}

{% macro default__cortex_inner_product_impl(vec_a, vec_b) %}
    {{ exceptions.raise_compiler_error(
        "cortex_inner_product is Snowflake-only."
    ) }}
{% endmacro %}

{% macro snowflake__cortex_inner_product_impl(vec_a, vec_b) %}
    VECTOR_INNER_PRODUCT({{ vec_a }}, {{ vec_b }})
{% endmacro %}


{#--- cortex_complete --------------------------------------------------#}
{#- LLM completion via Cortex. Useful for the LLM-boost path on
    borderline pairs without an external API key. -#}
{% macro cortex_complete(prompt, model='llama3.1-8b') %}
    {{ return(adapter.dispatch(
        'cortex_complete_impl', 'dbt_goldensuite'
    )(prompt, model)) }}
{% endmacro %}

{% macro default__cortex_complete_impl(prompt, model) %}
    {{ exceptions.raise_compiler_error(
        "cortex_complete is Snowflake-only. For out-of-band LLM scoring
         use `goldenmatch.core.llm_scorer` with an OpenAI / Anthropic
         key instead."
    ) }}
{% endmacro %}

{% macro snowflake__cortex_complete_impl(prompt, model) %}
    SNOWFLAKE.CORTEX.COMPLETE({{ dbt.string_literal(model) }}, {{ prompt }})
{% endmacro %}
