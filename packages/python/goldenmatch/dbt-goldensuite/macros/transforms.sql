{#-
  GoldenFlow transform macros (v0.5 of dbt-goldensuite, #465 Tier 1.1).

  Wraps the 8 most-used goldenflow standardizers as in-database
  dbt-callable SQL expressions. Each macro returns an SQL expression
  (NOT a SELECT) so it composes inside larger queries:

      SELECT
          customer_id,
          {{ dbt_goldensuite.normalize_email('email_raw') }} AS email,
          {{ dbt_goldensuite.normalize_phone('phone_raw') }} AS phone,
          {{ dbt_goldensuite.normalize_date('signup_date') }} AS signup_date
      FROM {{ ref('raw_customers') }}

  ## Adapter coverage

  | Adapter   | Status |
  |-----------|--------|
  | Postgres  | Compile error in v0.5 -- pgrx functions not yet shipped. Tracked as a follow-up issue under #465. |
  | DuckDB    | Live -- uses Python UDFs registered by `goldenmatch_duckdb.goldenflow.register_goldenflow_functions`. Requires `pip install goldenflow` on the dbt-runner host. |
  | Others    | Compile error with a remediation hint pointing to the Python helper `goldenflow.transform_df()`. |

  ## Why v0.5 ships DuckDB-only

  Postgres requires shipping new `#[pg_extern]` wrappers in
  `goldenmatch_pg` (pgrx 0.12.9) that call into goldenflow via the
  existing bridge crate. Build/test cycle is Linux-CI-only per the
  monorepo `packages/rust/extensions/CLAUDE.md`. Splitting the PR
  keeps the dbt-side surface shippable while the pgrx layer follows
  up on its own timeline. Macro stubs render Postgres-error today
  so consumers see the right shape + know what's coming.
-#}


{#--- normalize_email ----------------------------------------------------#}
{% macro normalize_email(column) %}
    {{ return(adapter.dispatch('normalize_email_impl', 'dbt_goldensuite')(column)) }}
{% endmacro %}

{% macro default__normalize_email_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "normalize_email is supported on duckdb today; postgres pgrx
         wrappers ship in a follow-up. For other adapters, run
         `goldenflow.transform_df(df)` out-of-band before dbt."
    ) }}
{% endmacro %}

{% macro duckdb__normalize_email_impl(column) %}
    goldenflow_normalize_email({{ column }})
{% endmacro %}

{% macro postgres__normalize_email_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "normalize_email Postgres support requires goldenmatch_pg pgrx
         wrappers (v0.5.x follow-up to #465). DuckDB target works today;
         out-of-band: SELECT * FROM goldenflow.transform_df(...) via Python."
    ) }}
{% endmacro %}


{#--- normalize_phone ----------------------------------------------------#}
{% macro normalize_phone(column, region='US') %}
    {{ return(adapter.dispatch('normalize_phone_impl', 'dbt_goldensuite')(column, region)) }}
{% endmacro %}

{% macro default__normalize_phone_impl(column, region) %}
    {{ exceptions.raise_compiler_error(
        "normalize_phone is supported on duckdb today; other adapters: run
         goldenflow out-of-band."
    ) }}
{% endmacro %}

{% macro duckdb__normalize_phone_impl(column, region) %}
    {#- region kwarg is reserved for future phonenumbers tuning; today
        the UDF uses goldenflow.phone_e164 which defaults to E.164. -#}
    goldenflow_normalize_phone({{ column }})
{% endmacro %}

{% macro postgres__normalize_phone_impl(column, region) %}
    {{ exceptions.raise_compiler_error(
        "normalize_phone Postgres support requires pgrx wrappers
         (v0.5.x follow-up to #465)."
    ) }}
{% endmacro %}


{#--- normalize_date -----------------------------------------------------#}
{% macro normalize_date(column, target_format='iso8601') %}
    {{ return(adapter.dispatch('normalize_date_impl', 'dbt_goldensuite')(column, target_format)) }}
{% endmacro %}

{% macro default__normalize_date_impl(column, target_format) %}
    {{ exceptions.raise_compiler_error(
        "normalize_date is supported on duckdb today; other adapters: run
         goldenflow out-of-band."
    ) }}
{% endmacro %}

{% macro duckdb__normalize_date_impl(column, target_format) %}
    goldenflow_normalize_date({{ column }})
{% endmacro %}

{% macro postgres__normalize_date_impl(column, target_format) %}
    {{ exceptions.raise_compiler_error(
        "normalize_date Postgres support requires pgrx wrappers
         (v0.5.x follow-up to #465)."
    ) }}
{% endmacro %}


{#--- normalize_name -----------------------------------------------------#}
{% macro normalize_name(column, mode='proper') %}
    {%- if mode not in ('proper', 'upper', 'lower') -%}
        {{ exceptions.raise_compiler_error(
            "normalize_name mode must be one of: proper, upper, lower; got " ~ mode
        ) }}
    {%- endif -%}
    {{ return(adapter.dispatch('normalize_name_impl', 'dbt_goldensuite')(column, mode)) }}
{% endmacro %}

{% macro default__normalize_name_impl(column, mode) %}
    {{ exceptions.raise_compiler_error(
        "normalize_name is supported on duckdb today; other adapters: run
         goldenflow out-of-band."
    ) }}
{% endmacro %}

{% macro duckdb__normalize_name_impl(column, mode) %}
    {%- if mode == 'proper' -%}
        goldenflow_normalize_name_proper({{ column }})
    {%- elif mode == 'upper' -%}
        UPPER({{ column }})
    {%- elif mode == 'lower' -%}
        LOWER({{ column }})
    {%- endif -%}
{% endmacro %}

{% macro postgres__normalize_name_impl(column, mode) %}
    {{ exceptions.raise_compiler_error(
        "normalize_name Postgres support requires pgrx wrappers
         (v0.5.x follow-up to #465)."
    ) }}
{% endmacro %}


{#--- canonicalize_url ---------------------------------------------------#}
{% macro canonicalize_url(column) %}
    {{ return(adapter.dispatch('canonicalize_url_impl', 'dbt_goldensuite')(column)) }}
{% endmacro %}

{% macro default__canonicalize_url_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "canonicalize_url is supported on duckdb today; other adapters: run
         goldenflow out-of-band."
    ) }}
{% endmacro %}

{% macro duckdb__canonicalize_url_impl(column) %}
    goldenflow_canonicalize_url({{ column }})
{% endmacro %}

{% macro postgres__canonicalize_url_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "canonicalize_url Postgres support requires pgrx wrappers
         (v0.5.x follow-up to #465)."
    ) }}
{% endmacro %}


{#--- canonicalize_address -----------------------------------------------#}
{% macro canonicalize_address(column) %}
    {{ return(adapter.dispatch('canonicalize_address_impl', 'dbt_goldensuite')(column)) }}
{% endmacro %}

{% macro default__canonicalize_address_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "canonicalize_address is supported on duckdb today; other adapters:
         run goldenflow out-of-band."
    ) }}
{% endmacro %}

{% macro duckdb__canonicalize_address_impl(column) %}
    goldenflow_canonicalize_address({{ column }})
{% endmacro %}

{% macro postgres__canonicalize_address_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "canonicalize_address Postgres support requires pgrx wrappers
         (v0.5.x follow-up to #465)."
    ) }}
{% endmacro %}


{#--- strip_whitespace + whitespace_normalize ----------------------------#}
{% macro strip_whitespace(column) %}
    {{ return(adapter.dispatch('strip_whitespace_impl', 'dbt_goldensuite')(column)) }}
{% endmacro %}

{% macro default__strip_whitespace_impl(column) %}
    TRIM({{ column }})
{% endmacro %}

{% macro duckdb__strip_whitespace_impl(column) %}
    goldenflow_strip({{ column }})
{% endmacro %}

{% macro postgres__strip_whitespace_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "strip_whitespace Postgres support requires pgrx wrappers
         (v0.5.x follow-up to #465). Note: standard SQL TRIM works on
         all adapters via the default__ branch."
    ) }}
{% endmacro %}


{% macro whitespace_normalize(column) %}
    {{ return(adapter.dispatch('whitespace_normalize_impl', 'dbt_goldensuite')(column)) }}
{% endmacro %}

{% macro default__whitespace_normalize_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "whitespace_normalize is supported on duckdb today; other adapters:
         run goldenflow out-of-band."
    ) }}
{% endmacro %}

{% macro duckdb__whitespace_normalize_impl(column) %}
    goldenflow_whitespace_normalize({{ column }})
{% endmacro %}

{% macro postgres__whitespace_normalize_impl(column) %}
    {{ exceptions.raise_compiler_error(
        "whitespace_normalize Postgres support requires pgrx wrappers
         (v0.5.x follow-up to #465)."
    ) }}
{% endmacro %}
