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
