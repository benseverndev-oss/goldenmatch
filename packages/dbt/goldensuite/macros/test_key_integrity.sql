{#
  goldenmatch_key_integrity -- advisory, metric-aware key-integrity test.

  A semantic layer joins on entity-key equality, so a measure is only correct if
  the declared key uniquely identifies one real-world entity. This test asserts
  the STRUCTURAL half of that (pure SQL, no UDF): is the declared `key` unique at
  grain, and does a duplicated key fan out a `SUM` beyond `max_fan_out`? It
  returns the offending metrics as rows, so the dbt test FAILS when the key isn't
  trustworthy.

  The entity-resolution half (would DISTINCT declared keys collapse onto one real
  entity -> undercount) needs GoldenMatch ER and lives in the Python capability
  `goldenmatch.semantic.certify_key_integrity(resolve=True)` -- not this SQL test.

  Args:
    model         relation under test.
    key           entity key column, or a list of columns (composite key).
    measures      numeric columns to check for fan-out (default: none).
    grain         ADVISORY in v1 -- recorded, not folded into the uniqueness
                  group, to stay identical to the Python capability's semantics.
    max_fan_out   fail if any key group has more rows than this, or any measure
                  SUM inflates beyond it (default 1.0 = a true, non-fanning key).
    min_uniqueness fail if the fraction of unique-at-grain key groups drops below
                  this (default 1.0 = every key group is unique).
#}

{% macro goldenmatch_key_integrity_sql(model, key, measures=[], grain=none,
        max_fan_out=1.0, min_uniqueness=1.0) %}
    {%- set keys = [key] if key is string else key -%}
    {%- if keys | length == 0 -%}
        {{ exceptions.raise_compiler_error("goldenmatch_key_integrity: `key` must name at least one column") }}
    {%- endif -%}
    {%- set keyexpr = keys | join(", ") -%}

    {%- set rep_cols = [] -%}      {# MAX(measure) per group = de-duplicated representative #}
    {%- set dedup_sums = [] -%}    {# SUM of per-group representatives #}
    {%- set total_sums = [] -%}    {# SUM over all rows #}
    {%- set fanout_exprs = [] -%}  {# per-measure fan-out ratio #}
    {%- set fanout_conds = [] -%}
    {%- for m in measures -%}
        {%- do rep_cols.append("MAX(" ~ m ~ ") AS " ~ m ~ "__rep") -%}
        {%- do dedup_sums.append("SUM(" ~ m ~ "__rep) AS " ~ m ~ "__dedup") -%}
        {%- do total_sums.append("SUM(" ~ m ~ ") AS " ~ m ~ "__total") -%}
        {%- do fanout_exprs.append("1.0 * tot." ~ m ~ "__total / NULLIF(agg." ~ m ~ "__dedup, 0) AS " ~ m ~ "__fan_out") -%}
        {%- do fanout_conds.append("(" ~ m ~ "__fan_out > " ~ max_fan_out ~ ")") -%}
    {%- endfor -%}

    {%- set rep_select = (", " ~ (rep_cols | join(", "))) if rep_cols else "" -%}
    {%- set dedup_select = (", " ~ (dedup_sums | join(", "))) if dedup_sums else "" -%}
    {%- set total_select = (", " ~ (total_sums | join(", "))) if total_sums else "" -%}
    {%- set fanout_select = (", " ~ (fanout_exprs | join(", "))) if fanout_exprs else "" -%}

    {%- set conds = ["(uniqueness IS NULL OR uniqueness < " ~ min_uniqueness ~ ")",
                     "(max_fan_out > " ~ max_fan_out ~ ")"] + fanout_conds -%}

    {{ return(
"WITH groups AS (
  SELECT " ~ keyexpr ~ ", COUNT(*) AS grp_rows" ~ rep_select ~ "
  FROM " ~ model ~ "
  GROUP BY " ~ keyexpr ~ "
),
agg AS (
  SELECT
    COUNT(*) AS n_groups,
    COALESCE(SUM(CASE WHEN grp_rows > 1 THEN 1 ELSE 0 END), 0) AS dup_groups,
    COALESCE(MAX(grp_rows), 0) AS max_fan_out" ~ dedup_select ~ "
  FROM groups
),
tot AS (
  SELECT COUNT(*) AS n_rows" ~ total_select ~ " FROM " ~ model ~ "
),
scored AS (
  SELECT
    1.0 - (1.0 * agg.dup_groups / NULLIF(agg.n_groups, 0)) AS uniqueness,
    agg.max_fan_out AS max_fan_out" ~ fanout_select ~ "
  FROM agg CROSS JOIN tot
)
SELECT * FROM scored WHERE " ~ (conds | join(" OR "))) }}
{% endmacro %}

{% test goldenmatch_key_integrity(model, key, measures=[], grain=none,
        max_fan_out=1.0, min_uniqueness=1.0) %}
{{ dbt_goldensuite.goldenmatch_key_integrity_sql(model, key, measures, grain, max_fan_out, min_uniqueness) }}
{% endtest %}
