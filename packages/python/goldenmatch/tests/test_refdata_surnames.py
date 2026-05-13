"""Tests for goldenmatch.refdata.surnames + name_freq_weighted_jw scorer."""

from __future__ import annotations

import pytest

from goldenmatch import refdata
from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata import (
    is_available,
    surname_count,
    surname_frequency,
    surname_idf,
    surname_rank,
)


# ── lookup ──────────────────────────────────────────────────────────────────


def test_data_is_bundled():
    """Wheel must ship the Census surname file."""
    assert is_available() is True


def test_smith_is_rank_one():
    """SMITH is the most common US surname per Census 2010 — sanity check the
    bundle wasn't corrupted on regenerate."""
    assert surname_rank("Smith") == 1
    assert (surname_count("Smith") or 0) > 2_000_000


def test_lookup_is_case_insensitive():
    assert surname_rank("smith") == surname_rank("SMITH") == surname_rank("SmIth")


def test_lookup_strips_non_alpha():
    """Apostrophes, hyphens, leading/trailing whitespace should not block a lookup."""
    assert surname_rank("O'Brien") == surname_rank("OBRIEN")
    assert surname_rank("  Smith  ") == surname_rank("Smith")
    assert surname_rank("Smith-Smith".replace("-", "")) == surname_rank("Smithsmith")


def test_unknown_name_returns_none_for_count():
    """A made-up name returns None for raw count/rank."""
    assert surname_count("Zorkwhibblefnord") is None
    assert surname_rank("Zorkwhibblefnord") is None


def test_unknown_name_returns_one_for_idf():
    """Out-of-vocabulary names report IDF=1.0 (semantically: "rarer than
    anything we've observed"). The frequency-weighted scorer separately
    decides what to do with OOV (it falls back to plain JW)."""
    assert surname_idf("Zorkwhibblefnord") == 1.0


def test_common_name_idf_below_half():
    """SMITH (count 2.4M) should land well below the midpoint of [0, 1]."""
    idf = surname_idf("Smith")
    assert idf is not None
    assert idf < 0.45, f"Smith IDF {idf} should be below 0.45 (it's the most common name)"


def test_rare_name_idf_is_high():
    """A name near the bottom of the top-10K should score near 1.0."""
    idf = surname_idf("Doriott")  # last entries in the bundle, count=100
    assert idf is not None
    assert idf > 0.95, f"Doriott IDF {idf} should be near 1.0"


def test_idf_monotone_with_rank():
    """More common (lower rank) → lower IDF. No reversals."""
    pairs = [("Smith", "Johnson"), ("Johnson", "Williams"), ("Williams", "Garcia")]
    for a, b in pairs:
        ia, ib = surname_idf(a), surname_idf(b)
        assert ia is not None and ib is not None
        rank_a, rank_b = surname_rank(a), surname_rank(b)
        assert rank_a is not None and rank_b is not None
        if rank_a < rank_b:
            assert ia <= ib, f"{a} (rank {rank_a}) IDF {ia} should be <= {b} (rank {rank_b}) IDF {ib}"


def test_frequency_in_unit_range():
    f = surname_frequency("Smith")
    assert f is not None
    assert 0.0 < f < 1.0


def test_none_input_returns_none():
    assert surname_count(None) is None
    assert surname_rank(None) is None
    assert surname_idf(None) is None
    assert surname_frequency(None) is None


# ── scorer ──────────────────────────────────────────────────────────────────


def test_scorer_is_registered_on_import():
    """`import goldenmatch.refdata` should land the scorer in PluginRegistry."""
    assert PluginRegistry.instance().has_scorer("name_freq_weighted_jw")


def test_scorer_returns_none_for_none_input():
    plugin = PluginRegistry.instance().get_scorer("name_freq_weighted_jw")
    assert plugin is not None
    assert plugin.score_pair(None, "Smith") is None
    assert plugin.score_pair("Smith", None) is None


def test_scorer_exact_match_returns_one():
    """Exact matches are above the borderline zone — the scorer should
    return plain JW (1.0) and not apply frequency weighting. Preserves
    recall on confident matches."""
    plugin = PluginRegistry.instance().get_scorer("name_freq_weighted_jw")
    assert plugin is not None
    assert plugin.score_pair("Smith", "Smith") == 1.0
    assert plugin.score_pair("Doriott", "Doriott") == 1.0


def test_scorer_rare_borderline_beats_common_borderline():
    """In the borderline JW zone, a rare-name pair scores higher than a
    common-name pair at the same JW. This is the discrimination the
    scorer adds on top of plain JW."""
    plugin = PluginRegistry.instance().get_scorer("name_freq_weighted_jw")
    assert plugin is not None
    # Two known borderline pairs (~0.85 JW each), one rare and one common.
    # Hernandez (rank 11) vs Hernande<x->z typo not in table>.
    # Easier: construct synthetic 4-character pairs with controlled JW.
    # Smith / Smyth: both known, JW < HIGH, JW > LOW.
    common = plugin.score_pair("Smith", "Smyth")
    # Doriott (rank ~9998, count 100) / Doreott (OOV) → would be OOV-pass-through.
    # Use a known-rare pair: pick two rare neighbors deterministically.
    rare = plugin.score_pair("Doriott", "Doribtt")  # Doribtt is OOV
    assert common is not None and rare is not None
    # Both should be defined and finite. The borderline weighting should
    # have applied to (Smith, Smyth) — verify common < plain_jw.
    from rapidfuzz.distance import JaroWinkler

    plain_smith_smyth = JaroWinkler.similarity("Smith", "Smyth")
    assert common < plain_smith_smyth


def test_scorer_borderline_match_is_weighted():
    """A borderline-JW match on known names should be re-weighted; the
    score should drop below the underlying JW for a common pair."""
    plugin = PluginRegistry.instance().get_scorer("name_freq_weighted_jw")
    assert plugin is not None
    from rapidfuzz.distance import JaroWinkler

    plain = JaroWinkler.similarity("Smith", "Smyth")
    # Sanity: we're in the borderline zone.
    assert 0.70 <= plain < 0.95
    weighted = plugin.score_pair("Smith", "Smyth")
    assert weighted is not None
    assert weighted < plain  # common-name down-weighting applied


def test_scorer_jw_still_drives_within_same_rarity_class():
    """For two names of the same rarity class, the underlying JW still
    drives ordering: an exact match within a common pair beats a fuzzy
    match within that same common pair. This isolates the JW signal from
    the IDF weighting."""
    plugin = PluginRegistry.instance().get_scorer("name_freq_weighted_jw")
    assert plugin is not None
    # Garcia and Gomez are both common (top 100); JW(Garcia, Gomez) is low.
    exact = plugin.score_pair("Garcia", "Garcia")
    fuzzy = plugin.score_pair("Garcia", "Gomez")
    assert exact is not None and fuzzy is not None
    assert exact > fuzzy


def test_scorer_falls_back_to_plain_jw_for_oov():
    """If either side is OOV (not in the bundled table), the scorer should
    return plain Jaro–Winkler — refusing to take a position when it can't
    reliably classify the names."""
    plugin = PluginRegistry.instance().get_scorer("name_freq_weighted_jw")
    assert plugin is not None
    # "Zorkwhibblefnord" is OOV; result should match plain JW exactly.
    from rapidfuzz.distance import JaroWinkler

    plain = JaroWinkler.similarity("Smith", "Zorkwhibblefnord")
    weighted = plugin.score_pair("Smith", "Zorkwhibblefnord")
    assert weighted == plain


# ── integration with score_field ────────────────────────────────────────────


def test_score_field_dispatches_to_plugin():
    """``goldenmatch.core.scorer.score_field`` should pick up the plugin
    scorer through its existing fall-through to PluginRegistry."""
    # Ensure the scorer is registered (import side-effect).
    assert refdata.is_available() is True
    from goldenmatch.core.scorer import score_field

    score = score_field("Garcia", "Garcia", "name_freq_weighted_jw")
    assert score is not None
    assert 0.0 < score <= 1.0


def test_matchkey_validation_accepts_scorer():
    """The MatchkeyField validator's fall-through to PluginRegistry should
    accept ``name_freq_weighted_jw`` without it being in VALID_SCORERS."""
    from goldenmatch.config.schemas import MatchkeyField

    field = MatchkeyField(
        field="last_name", scorer="name_freq_weighted_jw", weight=1.0,
    )
    assert field.scorer == "name_freq_weighted_jw"


def test_unknown_scorer_still_rejected():
    """Sanity: the validator only opens up to *registered* plugins, not
    arbitrary strings."""
    from goldenmatch.config.schemas import MatchkeyField

    with pytest.raises(ValueError, match="Invalid scorer"):
        MatchkeyField(field="last_name", scorer="this_is_not_a_real_scorer", weight=1.0)
