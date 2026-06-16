{% macro goldenmatch_predicted_pairs_sql(model, input, pairs_a, pairs_b, record_id, cluster_id) %}
    {%- if input == 'pairs' -%}
        {{ return("SELECT DISTINCT LEAST(" ~ pairs_a ~ ", " ~ pairs_b ~ ") AS a, GREATEST("
            ~ pairs_a ~ ", " ~ pairs_b ~ ") AS b FROM " ~ model ~ " WHERE " ~ pairs_a ~ " <> " ~ pairs_b) }}
    {%- elif input == 'clusters' -%}
        {{ return("SELECT DISTINCT LEAST(x." ~ record_id ~ ", y." ~ record_id ~ ") AS a, GREATEST(x."
            ~ record_id ~ ", y." ~ record_id ~ ") AS b FROM " ~ model ~ " x JOIN " ~ model ~ " y ON x."
            ~ cluster_id ~ " = y." ~ cluster_id ~ " AND x." ~ record_id ~ " < y." ~ record_id) }}
    {%- else -%}
        {{ exceptions.raise_compiler_error("goldenmatch_match_quality: input must be 'pairs' or 'clusters'; got " ~ input) }}
    {%- endif -%}
{% endmacro %}

{% macro goldenmatch_match_quality_sql(model, ground_truth, input, pairs_a, pairs_b,
        record_id, cluster_id, gt_a, gt_b, min_f1, min_precision, min_recall) %}
    {%- if min_f1 is none and min_precision is none and min_recall is none -%}
        {{ exceptions.raise_compiler_error("goldenmatch_match_quality: set at least one of min_f1 / min_precision / min_recall") }}
    {%- endif -%}
    {%- set pred = goldenmatch_predicted_pairs_sql(model, input, pairs_a, pairs_b, record_id, cluster_id) -%}
    {%- set truth = "SELECT DISTINCT LEAST(" ~ gt_a ~ ", " ~ gt_b ~ ") AS a, GREATEST("
        ~ gt_a ~ ", " ~ gt_b ~ ") AS b FROM " ~ ground_truth ~ " WHERE " ~ gt_a ~ " <> " ~ gt_b -%}
    {%- set conds = [] -%}
    {%- if min_f1 is not none -%}{%- do conds.append("(f1 IS NULL OR f1 < " ~ min_f1 ~ ")") -%}{%- endif -%}
    {%- if min_precision is not none -%}{%- do conds.append("(precision IS NULL OR precision < " ~ min_precision ~ ")") -%}{%- endif -%}
    {%- if min_recall is not none -%}{%- do conds.append("(recall IS NULL OR recall < " ~ min_recall ~ ")") -%}{%- endif -%}
    {{ return(
"WITH pred AS (" ~ pred ~ "),
truth AS (" ~ truth ~ "),
tp_fp AS (
  SELECT
    COALESCE(SUM(CASE WHEN t.a IS NOT NULL THEN 1 ELSE 0 END), 0) AS tp,
    COALESCE(SUM(CASE WHEN t.a IS NULL THEN 1 ELSE 0 END), 0) AS fp
  FROM pred p LEFT JOIN truth t ON p.a = t.a AND p.b = t.b
),
fn_c AS (
  SELECT COALESCE(SUM(CASE WHEN p.a IS NULL THEN 1 ELSE 0 END), 0) AS fn
  FROM truth t LEFT JOIN pred p ON p.a = t.a AND p.b = t.b
),
m AS (
  SELECT tp_fp.tp, tp_fp.fp, fn_c.fn,
    1.0 * tp_fp.tp / NULLIF(tp_fp.tp + tp_fp.fp, 0) AS precision,
    1.0 * tp_fp.tp / NULLIF(tp_fp.tp + fn_c.fn, 0) AS recall
  FROM tp_fp CROSS JOIN fn_c
),
scored AS (
  SELECT tp, fp, fn, precision, recall,
    2.0 * precision * recall / NULLIF(precision + recall, 0) AS f1
  FROM m
)
SELECT tp, fp, fn, precision, recall, f1 FROM scored WHERE " ~ (conds | join(" OR "))) }}
{% endmacro %}

{% test goldenmatch_match_quality(model, ground_truth, input='clusters',
        pairs_a='id_a', pairs_b='id_b', record_id='record_id', cluster_id='cluster_id',
        gt_a='id_a', gt_b='id_b', min_f1=none, min_precision=none, min_recall=none) %}
{{ dbt_goldensuite.goldenmatch_match_quality_sql(model, ground_truth, input, pairs_a, pairs_b,
    record_id, cluster_id, gt_a, gt_b, min_f1, min_precision, min_recall) }}
{% endtest %}
