"""Example 5: Write your own scorer plugin.

Custom scorers let you add domain-specific matching logic.
Use the @infermap.scorer decorator to register a function
that receives two FieldInfo objects and returns a ScorerResult.
"""

import polars as pl
import infermap
from infermap.types import FieldInfo, ScorerResult


# --- Custom scorer: match fields that share a common prefix ---
@infermap.scorer(name="PrefixScorer", weight=0.6)
def prefix_scorer(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
    """Score based on shared column name prefix (e.g., 'ship_' matches 'shipping_')."""
    src = source.name.lower().replace("_", "").replace("-", "")
    tgt = target.name.lower().replace("_", "").replace("-", "")

    # Find longest common prefix
    common = 0
    for a, b in zip(src, tgt):
        if a == b:
            common += 1
        else:
            break

    if common < 3:
        return ScorerResult(score=0.0, reasoning="no shared prefix")

    # Score based on how much of the shorter name is covered
    coverage = common / min(len(src), len(tgt))
    return ScorerResult(
        score=round(coverage, 3),
        reasoning=f"shared prefix '{src[:common]}' covers {coverage:.0%} of shorter name",
    )


# --- Custom scorer: boost when both fields have similar value patterns ---
@infermap.scorer(name="CurrencyDetector", weight=0.7)
def currency_detector(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
    """Boost score when both fields contain currency-like values."""
    if not source.sample_values or not target.sample_values:
        return None

    def looks_like_money(values: list[str]) -> bool:
        money_count = sum(
            1 for v in values
            if any(c in v for c in "$€£") or (v.replace(".", "").replace(",", "").isdigit() and "." in v)
        )
        return money_count / len(values) > 0.5 if values else False

    src_money = looks_like_money(source.sample_values)
    tgt_money = looks_like_money(target.sample_values)

    if src_money and tgt_money:
        return ScorerResult(score=0.9, reasoning="both fields contain currency values")
    if src_money != tgt_money:
        return ScorerResult(score=0.0, reasoning="currency mismatch")
    return None  # abstain — neither looks like money


# --- Use the custom scorers ---
print("=== E-commerce -> Warehouse Mapping (with custom scorers) ===\n")

# Create engine with default scorers + our custom ones
engine = infermap.MapEngine(
    scorers=infermap.default_scorers() + [prefix_scorer, currency_detector],
)

result = engine.map("data/ecommerce_orders.csv", "data/warehouse_schema.csv")

for m in result.mappings:
    # Show which scorers contributed
    custom_hits = [
        f"{name}={sr.score:.2f}"
        for name, sr in m.breakdown.items()
        if name in ("PrefixScorer", "CurrencyDetector") and sr.score > 0
    ]
    custom_info = f"  [custom: {', '.join(custom_hits)}]" if custom_hits else ""
    print(f"  {m.source:20s}  ->  {m.target:20s}  ({m.confidence:.3f}){custom_info}")
