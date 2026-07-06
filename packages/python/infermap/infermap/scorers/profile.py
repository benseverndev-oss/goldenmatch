"""Profile scorer — compares statistical profiles of two fields."""
from __future__ import annotations

from infermap._native_loader import native_enabled, native_module
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


def _profile_score_pure(
    src_dtype: str,
    tgt_dtype: str,
    src_null: float,
    tgt_null: float,
    src_uniq: float,
    tgt_uniq: float,
    src_val_count: float,
    tgt_val_count: float,
    src_avg_len: float,
    tgt_avg_len: float,
) -> float:
    """Byte-parity reference for ``infermap-core::profile_score``.

    Returns the raw (pre-clamp) profile score. The caller owns the abstain
    check (value_count == 0), average-length computation, and reasoning.
    """
    total = 0.0
    total += 0.4 * (1.0 if src_dtype == tgt_dtype else 0.0)
    total += 0.2 * _similarity(src_null, tgt_null)
    total += 0.2 * _similarity(src_uniq, tgt_uniq)
    max_len = max(src_avg_len, tgt_avg_len, 1.0)
    total += 0.1 * (1.0 - abs(src_avg_len - tgt_avg_len) / max_len)
    src_card = src_uniq * src_val_count
    tgt_card = tgt_uniq * tgt_val_count
    max_card = max(src_card, tgt_card, 1.0)
    total += 0.1 * (1.0 - abs(src_card - tgt_card) / max_card)
    return total


def _profile_score(
    src_dtype: str,
    tgt_dtype: str,
    src_null: float,
    tgt_null: float,
    src_uniq: float,
    tgt_uniq: float,
    src_val_count: float,
    tgt_val_count: float,
    src_avg_len: float,
    tgt_avg_len: float,
) -> float:
    if native_enabled("profile_score"):
        return native_module().profile_score(
            src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
            float(src_val_count), float(tgt_val_count), src_avg_len, tgt_avg_len)
    return _profile_score_pure(
        src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
        float(src_val_count), float(tgt_val_count), src_avg_len, tgt_avg_len)


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
        # Abstain if either side has zero rows (stays host — kernel never sees a
        # zero-row side).
        if source.value_count == 0 or target.value_count == 0:
            return None

        # Average-length reduction stays host (avoids marshaling sample lists +
        # the code-point-length parity trap).
        src_len = _avg_value_length(source.sample_values)
        tgt_len = _avg_value_length(target.sample_values)

        total_score = _profile_score(
            source.dtype, target.dtype,
            source.null_rate, target.null_rate,
            source.unique_rate, target.unique_rate,
            source.value_count, target.value_count,
            src_len, tgt_len,
        )

        # Reasoning stays host: recompute the sub-values for the message (idempotent,
        # no scoring muscle) so the string is byte-identical to the pre-cutover output.
        dtype_match = 1.0 if source.dtype == target.dtype else 0.0
        null_sim = _similarity(source.null_rate, target.null_rate)
        uniq_sim = _similarity(source.unique_rate, target.unique_rate)
        max_len = max(src_len, tgt_len, 1.0)
        len_sim = 1.0 - abs(src_len - tgt_len) / max_len
        src_card = source.unique_rate * source.value_count
        tgt_card = target.unique_rate * target.value_count
        max_card = max(src_card, tgt_card, 1.0)
        card_sim = 1.0 - abs(src_card - tgt_card) / max_card
        parts = [
            f"dtype={'match' if dtype_match else 'mismatch'}",
            f"null_sim={null_sim:.2f}",
            f"uniq_sim={uniq_sim:.2f}",
            f"len_sim={len_sim:.2f}",
            f"card_sim={card_sim:.2f}",
        ]
        return ScorerResult(
            score=total_score,
            reasoning="Profile comparison: " + ", ".join(parts),
        )
