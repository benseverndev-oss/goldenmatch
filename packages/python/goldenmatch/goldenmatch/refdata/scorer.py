"""Reference-data-aware scorers.

Currently registers one scorer:

- ``name_freq_weighted_jw`` — Jaro–Winkler edit similarity, modulated by an
  IDF-style weight derived from US Census 2010 surname frequency. A match
  on "Smith" carries less evidence than a match on "Zorkian"; a mismatch on
  any name carries the same evidence as plain Jaro–Winkler would.

The scorer is registered into ``goldenmatch.plugins.PluginRegistry`` so it
plugs into ``score_field`` / ``MatchkeyField.scorer`` validation through the
existing plugin path — no changes to ``VALID_SCORERS`` required.

The scorer also exposes a vectorized ``score_matrix(values)`` method that
``core.scorer._fuzzy_score_matrix`` picks up automatically — without it the
plugin would fall back to an O(N^2) Python double-loop on the hot path.
"""
from __future__ import annotations

import numpy as np
from rapidfuzz.distance import JaroWinkler
from rapidfuzz.process import cdist

from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata.surnames import is_available, surname_idf, surname_rank

# Borderline zone: only re-weight when the underlying JW falls in this band.
# Above the upper bound we trust the edit-similarity directly (preserves
# recall on exact / near-exact matches); below the lower bound the names
# don't match at all (no point fiddling with the weight). Inside the band
# is where frequency evidence carries real discrimination.
_BORDERLINE_LOW = 0.70
_BORDERLINE_HIGH = 0.95

# Floor weight: matches on the most common surnames still carry SOME
# evidence (otherwise Smith~Smyth at JW=0.93 scores ~0 and the scorer is
# useless on common-name shapes). 0.6 puts the lower bound at 60% of the
# underlying JW.
_COMMON_NAME_FLOOR = 0.6


class NameFreqWeightedJW:
    """Frequency-weighted Jaro–Winkler scorer.

    Algorithm::

        jw = JaroWinkler.similarity(a, b)
        if jw >= _BORDERLINE_HIGH or jw < _BORDERLINE_LOW:
            return jw                         # confident — no re-weighting
        if either side is OOV in the bundled table:
            return jw                         # can't classify frequency
        idf = mean(surname_idf(a), surname_idf(b))
        weight = _COMMON_NAME_FLOOR + (1 - _COMMON_NAME_FLOOR) * idf
        return jw * weight

    The weighting is active only in the borderline zone — exact and very
    high-JW matches return plain JW, so the scorer preserves recall on
    confident matches. The lift over plain JW comes from suppressing
    common-name borderline false positives.
    """

    name = "name_freq_weighted_jw"

    def score_pair(self, val_a: str | None, val_b: str | None) -> float | None:
        if val_a is None or val_b is None:
            return None
        jw = JaroWinkler.similarity(val_a, val_b)
        # Out of borderline zone — trust JW directly.
        if jw >= _BORDERLINE_HIGH or jw < _BORDERLINE_LOW:
            return jw
        if not is_available():
            return jw
        # OOV gate: surname_rank returns None for names absent from the
        # bundled table. Both-sides-known is the precondition for IDF
        # weighting; OOV-on-either-side falls back to plain JW so a typo
        # of a common name doesn't get credit-by-rarity.
        rank_a = surname_rank(val_a)
        rank_b = surname_rank(val_b)
        if rank_a is None or rank_b is None:
            return jw
        idf_a = surname_idf(val_a)
        idf_b = surname_idf(val_b)
        if idf_a is None or idf_b is None:
            return jw
        idf = (idf_a + idf_b) / 2.0
        weight = _COMMON_NAME_FLOOR + (1.0 - _COMMON_NAME_FLOOR) * idf
        return jw * weight

    def score_matrix(self, values: list[str | None]) -> np.ndarray:
        """Vectorized NxN scorer for hot paths.

        Replaces an O(N^2) ``score_pair`` Python loop with one rapidfuzz
        cdist + a handful of numpy ops. The semantics match ``score_pair``
        exactly:
        - JW outside the borderline band passes through.
        - In-band pairs with both names in the bundled table get
          ``jw * (floor + (1-floor) * mean_idf)``.
        - In-band pairs with either side OOV get plain JW (no down-weighting).

        ``None`` values are coerced to ``""`` (matches the wrapping code in
        ``core.scorer._fuzzy_score_matrix`` which does the same before
        calling other scorers).
        """
        n = len(values)
        clean = [v if v is not None else "" for v in values]
        jw = np.asarray(
            cdist(clean, clean, scorer=JaroWinkler.similarity),
            dtype=np.float32,
        )
        if n == 0 or not is_available():
            return jw
        # Per-value IDF + known-flag arrays. surname_rank=None marks OOV;
        # surname_idf returns 1.0 for OOV which would over-credit (a
        # known-rare ↔ OOV pair would weight to 1.0). Keep OOV separate via
        # the known mask and pass through plain JW for those pairs.
        idf_arr = np.zeros(n, dtype=np.float32)
        is_known = np.zeros(n, dtype=bool)
        for i, v in enumerate(clean):
            if not v:
                continue
            r = surname_rank(v)
            if r is None:
                continue
            is_known[i] = True
            iv = surname_idf(v)
            if iv is not None:
                idf_arr[i] = iv
        mean_idf = (idf_arr[:, None] + idf_arr[None, :]) / 2.0
        weight = _COMMON_NAME_FLOOR + (1.0 - _COMMON_NAME_FLOOR) * mean_idf
        in_zone = (jw >= _BORDERLINE_LOW) & (jw < _BORDERLINE_HIGH)
        both_known = is_known[:, None] & is_known[None, :]
        apply_mask = in_zone & both_known
        return np.where(apply_mask, jw * weight, jw).astype(np.float32)


def register_scorers() -> None:
    """Register every scorer in this module into the global plugin registry.

    Idempotent: re-registration overwrites in place, which is safe because
    the scorer classes are stateless. Called automatically on
    ``import goldenmatch.refdata``.
    """
    reg = PluginRegistry.instance()
    if not reg.has_scorer(NameFreqWeightedJW.name):
        reg.register_scorer(NameFreqWeightedJW.name, NameFreqWeightedJW())
