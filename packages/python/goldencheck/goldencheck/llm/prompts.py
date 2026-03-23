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
