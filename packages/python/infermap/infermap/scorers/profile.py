"""Profile scorer — compares statistical profiles of two fields."""
from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


def _avg_value_length(samples: list[str]) -> float:
    """Return the average string length of non-null sample values."""
    clean = [s for s in samples if s is not None and str(s).strip() != ""]
    if not clean:
        return 0.0
    return sum(len(str(s)) for s in clean) / len(clean)


def _similarity(a: float, b: float) -> float:
    """Return 1 - |a - b| clamped to [0, 1]."""
    return max(0.0, 1.0 - abs(a - b))


class ProfileScorer:
    """Scores two fields by comparing their statistical profiles.

    Comparison dimensions and weights:
      - dtype match        : 0.4
      - null rate          : 0.2
      - uniqueness rate    : 0.2
      - value length       : 0.1
      - cardinality ratio  : 0.1
    """

    name: str = "ProfileScorer"
    weight: float = 0.5

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        # Abstain if either side has zero rows
        if source.value_count == 0 or target.value_count == 0:
            return None

        parts: list[str] = []
        total_score = 0.0

        # dtype match (0.4)
        dtype_match = 1.0 if source.dtype == target.dtype else 0.0
        total_score += 0.4 * dtype_match
        parts.append(f"dtype={'match' if dtype_match else 'mismatch'}")

        # null rate similarity (0.2)
        null_sim = _similarity(source.null_rate, target.null_rate)
        total_score += 0.2 * null_sim
        parts.append(f"null_sim={null_sim:.2f}")

        # uniqueness similarity (0.2)
        uniq_sim = _similarity(source.unique_rate, target.unique_rate)
        total_score += 0.2 * uniq_sim
        parts.append(f"uniq_sim={uniq_sim:.2f}")

        # value length similarity (0.1)
        src_len = _avg_value_length(source.sample_values)
        tgt_len = _avg_value_length(target.sample_values)
        max_len = max(src_len, tgt_len, 1.0)
        len_sim = 1.0 - abs(src_len - tgt_len) / max_len
        total_score += 0.1 * len_sim
        parts.append(f"len_sim={len_sim:.2f}")

        # cardinality ratio similarity (0.1)
        src_card = source.unique_rate * source.value_count
        tgt_card = target.unique_rate * target.value_count
        max_card = max(src_card, tgt_card, 1.0)
        card_sim = 1.0 - abs(src_card - tgt_card) / max_card
        total_score += 0.1 * card_sim
        parts.append(f"card_sim={card_sim:.2f}")

        return ScorerResult(
            score=total_score,
            reasoning="Profile comparison: " + ", ".join(parts),
        )
