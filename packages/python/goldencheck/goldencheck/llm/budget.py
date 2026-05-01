"""LLM cost tracking and budget enforcement."""
from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)

# (input_per_1k_tokens, output_per_1k_tokens)
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.0008, 0.004),
    "claude-sonnet-4-20250514": (0.003, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
}
_DEFAULT_COST = (0.001, 0.004)


def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Estimate cost in USD for a given token count and model."""
    input_rate, output_rate = _MODEL_COSTS.get(model, _DEFAULT_COST)
    return (input_tokens / 1000) * input_rate + (output_tokens / 1000) * output_rate


def get_budget_limit() -> float | None:
    """Get budget limit from env var. Returns None if uncapped."""
    val = os.environ.get("GOLDENCHECK_LLM_BUDGET")
    if val is None:
        return None
    try:
        return float(val)
    except ValueError:
        logger.warning("Invalid GOLDENCHECK_LLM_BUDGET value: %s. Ignoring.", val)
        return None


def check_budget(estimated_cost: float) -> bool:
    """Check if estimated cost is within budget. Returns True if OK to proceed."""
    limit = get_budget_limit()
    if limit is None:
        return True
    if estimated_cost > limit:
        logger.warning(
            "Estimated LLM cost ($%.4f) exceeds budget ($%.2f). Skipping LLM boost.",
            estimated_cost, limit,
        )
        return False
    return True


class CostReport:
    """Tracks actual cost from a single LLM call."""
    def __init__(self):
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.model: str = ""
        self.cost_usd: float = 0.0

    def record(self, input_tokens: int, output_tokens: int, model: str) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = model
        self.cost_usd = estimate_cost(input_tokens, output_tokens, model)

    def summary(self) -> dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }
