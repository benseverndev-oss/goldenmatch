"""Parse and validate LLM JSON responses."""
from __future__ import annotations
import json
import logging
import re
from goldencheck.llm.prompts import LLMResponse

logger = logging.getLogger(__name__)


def parse_llm_response(raw: str) -> LLMResponse | None:
    """Parse raw LLM text into validated LLMResponse. Returns None on failure."""
    # Strip markdown code fences if present
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', raw.strip())
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("LLM response is not valid JSON: %s", e)
        return None

    try:
        return LLMResponse(**data)
    except Exception as e:
        logger.warning("LLM response failed schema validation: %s", e)
        return None
