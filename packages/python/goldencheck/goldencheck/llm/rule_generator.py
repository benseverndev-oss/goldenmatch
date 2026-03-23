"""LLM-powered rule generation — analyzes data samples and generates validation rules."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import polars as pl
from pydantic import BaseModel

from goldencheck.models.finding import Finding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

RULE_GENERATION_PROMPT = """You are a data quality analyst generating specific validation rules.

You will receive a dataset summary with representative samples for each column, along with findings from automated profilers. Your job is to generate SPECIFIC, TESTABLE validation rules that the profilers missed.

Focus on:
1. **Value validity** — specific values that are invalid for this column's domain (e.g., "XX" is not a valid country code, negative quantities are impossible)
2. **Format constraints** — expected string lengths, regex patterns (e.g., auth numbers should be exactly 10 digits)
3. **Cross-column logic** — relationships between columns (e.g., age should match date_of_birth, state should be consistent with zip prefix)
4. **Domain standards** — mixed coding standards (e.g., ICD-9 vs ICD-10), inconsistent units

For each rule, specify:
- The column(s) it applies to
- The rule type: "regex", "length", "value_list", "range", "cross_column", "custom"
- A clear description of what makes a value invalid
- The check name to use (one of: invalid_values, format_detection, cross_column, type_inference, logic_violation)

Respond with valid JSON:
{
  "rules": [
    {
      "column": "<column_name>",
      "rule_type": "regex|length|value_list|range|cross_column|custom",
      "check": "<check_name>",
      "description": "<what this rule checks>",
      "params": {
        "pattern": "<regex for regex type>",
        "min_length": <int>,
        "max_length": <int>,
        "valid_values": ["<val1>", "<val2>"],
        "invalid_values": ["<val1>"],
        "min": <number>,
        "max": <number>,
        "related_column": "<col>",
        "relationship": "<description>"
      }
    }
  ]
}

Only include rules where you are confident there is a real issue. Be specific — don't generate generic rules that the profilers already cover (null checks, uniqueness, basic range, basic format). Focus on DOMAIN-SPECIFIC rules that require semantic understanding."""


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class RuleParams(BaseModel):
    model_config = {"coerce_numbers_to_str": True}

    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    valid_values: list[str] | None = None
    invalid_values: list[str] | None = None
    min: float | None = None
    max: float | None = None
    related_column: str | None = None
    relationship: str | None = None


class GeneratedRule(BaseModel):
    column: str
    rule_type: str
    check: str
    description: str
    params: RuleParams = RuleParams()


class RuleGenerationResponse(BaseModel):
    rules: list[GeneratedRule] = []


# ---------------------------------------------------------------------------
# Rule generation
# ---------------------------------------------------------------------------

def generate_rules(
    df: pl.DataFrame,
    findings: list[Finding],
    provider: str = "anthropic",
) -> list[GeneratedRule]:
    """Send data sample to LLM and generate validation rules."""
    from goldencheck.llm.sample_block import build_sample_blocks
    from goldencheck.llm.providers import check_llm_available, DEFAULT_MODELS
    from goldencheck.llm.budget import CostReport, estimate_cost, check_budget
    import re

    check_llm_available(provider)

    blocks = build_sample_blocks(df, findings)
    user_prompt = "Here is the dataset summary:\n\n" + json.dumps(blocks, indent=2, default=str)

    model = os.environ.get("GOLDENCHECK_LLM_MODEL", DEFAULT_MODELS.get(provider, ""))
    estimated_cost = estimate_cost(3000, 1000, model)
    if not check_budget(estimated_cost):
        logger.warning("Skipping LLM rule generation due to budget constraint.")
        return []

    cost_report = CostReport()
    try:
        text, input_tok, output_tok = _call_llm_for_rules(provider, user_prompt)
        cost_report.record(input_tok, output_tok, model)
        logger.info(
            "LLM rule generation cost: $%.4f (input: %d, output: %d)",
            cost_report.cost_usd, input_tok, output_tok,
        )

        # Parse response — strip markdown fences
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', text.strip())
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)
        data = json.loads(cleaned)
        response = RuleGenerationResponse(**data)
        logger.info("LLM generated %d rules", len(response.rules))
        return response.rules

    except Exception as e:
        logger.warning("LLM rule generation failed: %s", e)
        return []


def _call_llm_for_rules(provider: str, user_prompt: str) -> tuple[str, int, int]:
    """Call LLM with rule generation prompt."""
    model = os.environ.get("GOLDENCHECK_LLM_MODEL", "")

    if provider == "anthropic":
        import anthropic
        if not model:
            model = "claude-haiku-4-5-20251001"
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=RULE_GENERATION_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens

    elif provider == "openai":
        import openai
        if not model:
            model = "gpt-4o-mini"
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": RULE_GENERATION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return (
            response.choices[0].message.content,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )

    raise ValueError(f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Rule application
# ---------------------------------------------------------------------------

def apply_rules(
    df: pl.DataFrame,
    rules: list[GeneratedRule],
) -> list[Finding]:
    """Apply generated rules to a DataFrame and return findings."""
    findings: list[Finding] = []

    for rule in rules:
        try:
            new_findings = _apply_single_rule(df, rule)
            findings.extend(new_findings)
        except Exception as e:
            logger.warning("Failed to apply rule on %s: %s", rule.column, e)

    return findings


def _apply_single_rule(df: pl.DataFrame, rule: GeneratedRule) -> list[Finding]:
    """Apply a single rule and return findings."""
    from goldencheck.models.finding import Severity

    if rule.column not in df.columns:
        return []

    col = df[rule.column]
    params = rule.params
    findings: list[Finding] = []

    if rule.rule_type == "regex" and params.pattern:
        if col.dtype in (pl.Utf8, pl.String):
            non_null = col.drop_nulls()
            if len(non_null) > 0:
                matches = non_null.str.contains(params.pattern)
                non_match_count = int((~matches).sum())
                if 0 < non_match_count < len(non_null) * 0.5:
                    sample = non_null.filter(~matches).head(5).to_list()
                    findings.append(Finding(
                        severity=Severity.WARNING,
                        column=rule.column,
                        check=rule.check,
                        message=f"{non_match_count} row(s) have invalid format — {rule.description}",
                        affected_rows=non_match_count,
                        sample_values=[str(v) for v in sample],
                        source="llm",
                        confidence=0.8,
                    ))

    elif rule.rule_type == "length":
        if col.dtype in (pl.Utf8, pl.String):
            non_null = col.drop_nulls()
            if len(non_null) > 0:
                str_lens = non_null.str.len_chars()
                mask = pl.lit(False)
                if params.min_length is not None:
                    mask = mask | (str_lens < params.min_length)
                if params.max_length is not None:
                    mask = mask | (str_lens > params.max_length)
                violations = int(mask.sum())
                if 0 < violations < len(non_null) * 0.5:
                    sample = non_null.filter(mask).head(5).to_list()
                    findings.append(Finding(
                        severity=Severity.WARNING,
                        column=rule.column,
                        check=rule.check,
                        message=(
                            f"{violations} row(s) have invalid length — {rule.description}"
                        ),
                        affected_rows=violations,
                        sample_values=[str(v) for v in sample],
                        source="llm",
                        confidence=0.8,
                    ))

    elif rule.rule_type == "value_list" and params.invalid_values:
        non_null = col.drop_nulls().cast(pl.String)
        if len(non_null) > 0:
            invalid_set = set(params.invalid_values)
            mask = non_null.is_in(list(invalid_set))
            violations = int(mask.sum())
            if violations > 0:
                sample = non_null.filter(mask).head(5).to_list()
                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=rule.column,
                    check=rule.check,
                    message=f"{violations} row(s) contain invalid values — {rule.description}",
                    affected_rows=violations,
                    sample_values=[str(v) for v in sample],
                    source="llm",
                    confidence=0.8,
                ))

    elif rule.rule_type == "cross_column" and params.related_column:
        if params.related_column in df.columns:
            findings.append(Finding(
                severity=Severity.WARNING,
                column=rule.column,
                check=rule.check,
                message=f"Cross-column inconsistency — {rule.description}",
                affected_rows=0,
                source="llm",
                confidence=0.7,
            ))

    return findings


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def save_rules(rules: list[GeneratedRule], path: Path) -> None:
    """Save generated rules to a JSON file."""
    data = [r.model_dump() for r in rules]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Saved %d rules to %s", len(rules), path)


def load_rules(path: Path) -> list[GeneratedRule]:
    """Load rules from a JSON file."""
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    return [GeneratedRule(**r) for r in data]
