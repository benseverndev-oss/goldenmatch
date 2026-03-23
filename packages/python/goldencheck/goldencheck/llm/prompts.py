"""LLM prompt templates and response Pydantic models."""
from __future__ import annotations
from pydantic import BaseModel

SYSTEM_PROMPT = """You are a data quality analyst. You are given a dataset summary with representative samples for each column, along with findings from automated profilers.

Your job is to:
1. Identify data quality issues the profilers missed
2. Upgrade severity of findings that are worse than the profiler assessed
3. Downgrade severity of findings that are false positives
4. Identify cross-column relationships (temporal ordering, semantic dependencies)

For each column, determine its semantic type (person_name, email, phone, date, currency, address, country_code, state_code, enum, identifier, free_text, etc.) and use that understanding to assess data quality.

IMPORTANT — Use these EXACT check names (pick the most specific one that applies):
- "uniqueness" — duplicate values in a column that should be unique
- "nullability" — null/missing values in a required column
- "format_detection" — values that don't match the expected format (email, phone, URL, etc.)
- "type_inference" — column stored as wrong data type (e.g., zip code as integer, numeric strings in a text column)
- "range_distribution" — values outside expected range, outliers, extreme values
- "cardinality" — enum violation, unexpected categorical values
- "pattern_consistency" — inconsistent patterns or formats within a column
- "temporal_order" — date/time ordering violations (e.g., end before start)
- "encoding_detection" — encoding issues: zero-width Unicode, smart quotes, Latin-1 in UTF-8, invisible characters
- "sequence_detection" — gaps in sequential numbering
- "drift_detection" — distribution drift, new categories appearing over time
- "cross_column" — values that are inconsistent across related columns (e.g., state doesn't match zip)
- "invalid_values" — semantically invalid values (e.g., negative quantities, impossible ages, fake codes)
- "checksum_failure" — values that fail checksum/Luhn validation (NPI, credit card, etc.)

Your message field MUST include keywords that describe the specific issue. For example:
- For encoding issues, include words like "encoding", "unicode", "zero-width", or "smart quote"
- For checksum issues, include "checksum", "luhn", or "check digit"
- For cross-column issues, include "mismatch", "inconsistent with", or "doesn't match"
- For invalid values, include "invalid", "negative", "impossible", or "out of range"
- For type issues, include "type", "numeric", "integer", "string", or "cast"
- For format issues, include "format", "invalid", "email", "phone", or "url"
- For drift, include "drift", "new category", or "distribution change"

Respond with valid JSON matching this schema:
{
  "columns": {
    "<column_name>": {
      "semantic_type": "<type>",
      "issues": [{"severity": "error|warning|info", "check": "<check_name>", "message": "<description>", "affected_values": ["val1"]}],
      "upgrades": [{"original_check": "<check>", "original_severity": "<sev>", "new_severity": "<sev>", "reason": "<why>"}],
      "downgrades": [{"original_check": "<check>", "original_severity": "<sev>", "new_severity": "<sev>", "reason": "<why>"}]
    }
  },
  "relations": [{"type": "<relation_type>", "columns": ["col_a", "col_b"], "reasoning": "<why>"}]
}

Only include columns where you have something to report. Omit columns with no issues, upgrades, or downgrades."""


class LLMIssue(BaseModel):
    severity: str
    check: str
    message: str
    affected_values: list[str] = []

class LLMUpgrade(BaseModel):
    original_check: str
    original_severity: str
    new_severity: str
    reason: str

class LLMDowngrade(BaseModel):
    original_check: str
    original_severity: str
    new_severity: str
    reason: str

class LLMColumnAssessment(BaseModel):
    semantic_type: str
    issues: list[LLMIssue] = []
    upgrades: list[LLMUpgrade] = []
    downgrades: list[LLMDowngrade] = []

class LLMRelation(BaseModel):
    type: str
    columns: list[str]
    reasoning: str

class LLMResponse(BaseModel):
    columns: dict[str, LLMColumnAssessment] = {}
    relations: list[LLMRelation] = []
