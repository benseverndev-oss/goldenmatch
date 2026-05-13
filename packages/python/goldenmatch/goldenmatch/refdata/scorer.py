"""Reference-data-aware scorers.

Registers two scorers via ``goldenmatch.plugins.PluginRegistry``:

- ``name_freq_weighted_jw`` — Jaro–Winkler modulated by US Census 2010
  surname IDF. Down-weights matches on common surnames in the borderline
  JW zone; preserves plain JW elsewhere. Built for ``last_name`` fields.
  Also exposes a vectorized ``score_matrix(values)`` method that
  ``core.scorer._fuzzy_score_matrix`` picks up automatically — without
  it the plugin would fall back to an O(N^2) Python double-loop on the
  hot path.

- ``given_name_aliased_jw`` — Jaro–Winkler with an alias-aware exact
  bonus: if two given names are known forms of the same person
  (William ↔ Bill, Robert ↔ Bob), score = 1.0 regardless of edit
  distance. Otherwise plain JW. Built for ``first_name`` fields.

Both scorers plug into ``score_field`` / ``MatchkeyField.scorer``
validation through the existing plugin path — no changes to
``VALID_SCORERS`` required.
"""
from __future__ import annotations

import numpy as np
from rapidfuzz.distance import JaroWinkler
from rapidfuzz.process import cdist

from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata.given_names import are_equivalent
from goldenmatch.refdata.given_names import is_available as given_names_available
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


from goldenmatch.plugins.base import ScorerPlugin  # noqa: E402


class NameFreqWeightedJW(ScorerPlugin):
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
    """

    name = "name_freq_weighted_jw"

    def score_pair(self, val_a: str | None, val_b: str | None) -> float | None:
        if val_a is None or val_b is None:
            return None
        jw = JaroWinkler.similarity(val_a, val_b)
        if jw >= _BORDERLINE_HIGH or jw < _BORDERLINE_LOW:
            return jw
        if not is_available():
            return jw
        # OOV gate: surname_rank returns None for names absent from the
        # bundled table. OOV-on-either-side falls back to plain JW so a
        # typo of a common name doesn't get credit-by-rarity.
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
        """Vectorized NxN scorer. Replaces an O(N^2) score_pair loop with
        one rapidfuzz cdist + numpy ops. Semantics match score_pair."""
        n = len(values)
        clean = [v if v is not None else "" for v in values]
        jw = np.asarray(
            cdist(clean, clean, scorer=JaroWinkler.similarity),
            dtype=np.float32,
        )
        if n == 0 or not is_available():
            return jw
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


class GivenNameAliasedJW(ScorerPlugin):
    """Jaro–Winkler with alias-aware exact bonus.

    Algorithm::

        if a and b are known aliases of the same canonical name:
            return 1.0          # William ↔ Bill, Robert ↔ Bob, etc.
        else:
            return JaroWinkler.similarity(a, b)

    Net effect: pairs that plain JW would score low (William vs Bill,
    JW ~= 0.55) but that are actually the same person get promoted to
    1.0. The scorer never *lowers* a JW score — it only promotes known
    aliases. Degrades cleanly when the bundled alias table is missing.
    """

    name = "given_name_aliased_jw"

    def score_pair(self, val_a: str | None, val_b: str | None) -> float | None:
        if val_a is None or val_b is None:
            return None
        if given_names_available() and are_equivalent(val_a, val_b):
            return 1.0
        return JaroWinkler.similarity(val_a, val_b)


def register_scorers() -> None:
    """Register every scorer in this module. Idempotent."""
    reg = PluginRegistry.instance()
    if not reg.has_scorer(NameFreqWeightedJW.name):
        reg.register_scorer(NameFreqWeightedJW.name, NameFreqWeightedJW())
    if not reg.has_scorer(GivenNameAliasedJW.name):
        reg.register_scorer(GivenNameAliasedJW.name, GivenNameAliasedJW())
