"""Reference-data-aware scorers.

Currently registers one scorer:

- ``name_freq_weighted_jw`` — Jaro–Winkler edit similarity, modulated by an
  IDF-style weight derived from US Census 2010 surname frequency. A match
  on "Smith" carries less evidence than a match on "Zorkian"; a mismatch on
  any name carries the same evidence as plain Jaro–Winkler would.

The scorer is registered into ``goldenmatch.plugins.PluginRegistry`` so it
plugs into ``score_field`` / ``MatchkeyField.scorer`` validation through the
existing plugin path — no changes to ``VALID_SCORERS`` required.
"""
from __future__ import annotations

from rapidfuzz.distance import JaroWinkler

from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata.surnames import is_available, surname_idf

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
        idf_a = surname_idf(val_a)
        idf_b = surname_idf(val_b)
        if idf_a is None or idf_b is None:
            return jw
        from goldenmatch.refdata.surnames import surname_rank

        if surname_rank(val_a) is None or surname_rank(val_b) is None:
            return jw
        idf = (idf_a + idf_b) / 2.0
        weight = _COMMON_NAME_FLOOR + (1.0 - _COMMON_NAME_FLOOR) * idf
        return jw * weight


def register_scorers() -> None:
    """Register every scorer in this module into the global plugin registry.

    Idempotent: re-registration overwrites in place, which is safe because
    the scorer classes are stateless. Called automatically on
    ``import goldenmatch.refdata``.
    """
    reg = PluginRegistry.instance()
    if not reg.has_scorer(NameFreqWeightedJW.name):
        reg.register_scorer(NameFreqWeightedJW.name, NameFreqWeightedJW())
