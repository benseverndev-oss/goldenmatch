# goldenflow/llm/corrector.py
"""LLM-enhanced categorical correction."""
from __future__ import annotations

import json
from collections import Counter

import polars as pl

from goldenflow.transforms import register_transform


def _get_value_summary(series: pl.Series, max_values: int = 30) -> dict[str, int]:
    """Get value frequency summary for a column."""
    counts = Counter(v for v in series.to_list() if v is not None)
    return dict(counts.most_common(max_values))


def _ask_llm_for_corrections(
    column_name: str,
    value_summary: dict[str, int],
    provider: str = "anthropic",
) -> dict[str, str]:
    """Ask an LLM to identify and correct categorical issues.

    Returns: {incorrect_value: corrected_value}
    """
    prompt = f"""You are a data quality expert. Analyze this column and identify values that appear to be misspellings, abbreviations, or variants of other values in the same column.

Column name: {column_name}
Value frequencies (value: count):
{json.dumps(value_summary, indent=2)}

For each incorrect value, provide the corrected canonical form. Only include values that need correction. Return JSON object mapping incorrect values to their corrections.

Example response:
{{"actve": "active", "ACTIVE": "active", "pendng": "pending"}}

Return ONLY the JSON object, no other text."""

    if provider == "anthropic":
        try:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-5-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return json.loads(response.content[0].text)
        except (ImportError, Exception):
            return {}
    elif provider == "openai":
        try:
            import openai
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except (ImportError, Exception):
            return {}

    return {}


@register_transform(
    name="category_llm_correct",
    input_types=["string"],
    auto_apply=False,
    priority=34,
    mode="series",
)
def category_llm_correct(
    series: pl.Series,
    column_name: str | None = None,
    provider: str = "anthropic",
) -> pl.Series:
    """LLM-enhanced categorical correction.

    Sends column name and value frequency summary to an LLM for correction suggestions.
    Falls back silently if no LLM provider is available.
    """
    col_name = column_name or series.name or "unknown"
    summary = _get_value_summary(series)

    if len(summary) <= 1:
        return series

    corrections = _ask_llm_for_corrections(col_name, summary, provider)

    if not corrections:
        return series

    def _correct(val: str | None) -> str | None:
        if val is None:
            return None
        return corrections.get(val.strip(), val.strip())

    return series.map_elements(_correct, return_dtype=pl.Utf8)
