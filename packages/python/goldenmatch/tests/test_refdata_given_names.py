"""Tests for goldenmatch.refdata.given_names + given_name_aliased_jw scorer."""

from __future__ import annotations

import goldenmatch.refdata  # noqa: F401  registers scorer
from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata import (
    aliases_of,
    are_equivalent,
    canonical_form,
    given_names_available,
)

# ── lookup ──────────────────────────────────────────────────────────────────


def test_data_is_bundled():
    assert given_names_available() is True


def test_canonical_known_alias():
    assert canonical_form("Bob") == "robert"
    assert canonical_form("Bill") == "william"
    assert canonical_form("Peggy") == "margaret"


def test_canonical_oov_returns_normalized_input():
    """OOV names degrade gracefully — return the normalized form, not None.
    This lets callers use ``canonical_form`` as a key-builder."""
    assert canonical_form("Zorkwhibblefnord") == "zorkwhibblefnord"


def test_canonical_is_case_and_punct_insensitive():
    assert canonical_form("BOB") == canonical_form("bob") == canonical_form("B.o.b")


def test_canonical_none_returns_none():
    assert canonical_form(None) is None


def test_aliases_of_known_name_is_symmetric():
    """The equivalence class includes the input itself and the canonical."""
    bobs = aliases_of("Bob")
    assert "bob" in bobs
    assert "robert" in bobs
    assert "rob" in bobs
    assert "bobby" in bobs


def test_aliases_of_canonical_returns_full_class():
    roberts = aliases_of("Robert")
    assert "bob" in roberts
    assert "robert" in roberts


def test_aliases_of_oov_returns_empty():
    """OOV → empty frozenset. Callers can iterate freely."""
    assert aliases_of("Zorkwhibblefnord") == frozenset()


def test_are_equivalent_known_pair():
    assert are_equivalent("Robert", "Bob") is True
    assert are_equivalent("Bob", "Robert") is True
    assert are_equivalent("William", "Bill") is True


def test_are_equivalent_within_class():
    """Bob and Rob are both Robert. Should be equivalent."""
    assert are_equivalent("Bob", "Rob") is True


def test_are_equivalent_reflexive():
    assert are_equivalent("Robert", "Robert") is True
    assert are_equivalent("zorkian", "Zorkian") is True  # OOV but identical after normalize


def test_are_equivalent_distinct_names():
    assert are_equivalent("Robert", "William") is False
    assert are_equivalent("Bob", "Bill") is False


def test_are_equivalent_handles_none():
    assert are_equivalent(None, "Robert") is False
    assert are_equivalent("Robert", None) is False
    assert are_equivalent(None, None) is False


def test_are_equivalent_handles_empty():
    assert are_equivalent("", "") is False
    assert are_equivalent("!!", "Bob") is False  # normalizes to empty


def test_are_equivalent_symmetric_for_ambiguous_short_forms():
    """Regression: short forms that belong to multiple canonicals (e.g.
    'kate' is shared by Catherine, Kathleen, Kaitlyn) must be detected as
    equivalent to every parent canonical from *either* direction. Pre-fix,
    ``are_equivalent("Kate", "Catherine")`` returned False (because Kate's
    last-loaded canonical was Kaitlyn) while ``("Catherine", "Kate")``
    returned True — silently dropping every multi-canonical alias pair
    from the matcher's NxN score matrix."""
    ambiguous_pairs = [
        ("Kate", "Catherine"),
        ("Kate", "Kathleen"),
        ("Kate", "Kaitlyn"),
        ("Chris", "Christopher"),
        ("Chris", "Christine"),
        ("Chris", "Christina"),
        ("Sue", "Susan"),
        ("Sue", "Suzanne"),
        ("Pat", "Patricia"),
        ("Pat", "Patrick"),
    ]
    for short, canon in ambiguous_pairs:
        assert are_equivalent(short, canon), f"{short} ↔ {canon} a→b broke"
        assert are_equivalent(canon, short), f"{short} ↔ {canon} b→a broke"


# ── scorer ──────────────────────────────────────────────────────────────────


def test_given_name_scorer_registered():
    assert PluginRegistry.instance().has_scorer("given_name_aliased_jw")


def test_scorer_none_input():
    plugin = PluginRegistry.instance().get_scorer("given_name_aliased_jw")
    assert plugin is not None
    assert plugin.score_pair(None, "Bob") is None
    assert plugin.score_pair("Bob", None) is None


def test_scorer_alias_promoted_to_one():
    """The whole point of this scorer."""
    plugin = PluginRegistry.instance().get_scorer("given_name_aliased_jw")
    assert plugin is not None
    assert plugin.score_pair("William", "Bill") == 1.0
    assert plugin.score_pair("Robert", "Bob") == 1.0
    assert plugin.score_pair("Margaret", "Peggy") == 1.0


def test_scorer_exact_match():
    plugin = PluginRegistry.instance().get_scorer("given_name_aliased_jw")
    assert plugin is not None
    assert plugin.score_pair("Robert", "Robert") == 1.0


def test_scorer_non_alias_falls_back_to_jw():
    """Two unrelated names: plain JW."""
    plugin = PluginRegistry.instance().get_scorer("given_name_aliased_jw")
    assert plugin is not None
    from rapidfuzz.distance import JaroWinkler

    plain = JaroWinkler.similarity("Robert", "Richard")
    weighted = plugin.score_pair("Robert", "Richard")
    assert weighted == plain


def test_scorer_oov_pair_falls_back_to_jw():
    """OOV names: identical to plain JW."""
    plugin = PluginRegistry.instance().get_scorer("given_name_aliased_jw")
    assert plugin is not None
    from rapidfuzz.distance import JaroWinkler

    plain = JaroWinkler.similarity("Xenia", "Xeniaa")
    weighted = plugin.score_pair("Xenia", "Xeniaa")
    assert weighted == plain


def test_scorer_typo_lower_than_alias_match():
    """An alias-equivalent pair should beat a typo of the same name."""
    plugin = PluginRegistry.instance().get_scorer("given_name_aliased_jw")
    assert plugin is not None
    alias = plugin.score_pair("William", "Bill")     # alias → 1.0
    typo = plugin.score_pair("William", "Williaq")   # typo → plain JW (<1.0)
    assert alias is not None and typo is not None
    assert alias > typo


# ── integration ─────────────────────────────────────────────────────────────


def test_score_field_dispatches_to_plugin():
    from goldenmatch.core.scorer import score_field

    assert score_field("Bob", "Robert", "given_name_aliased_jw") == 1.0


def test_matchkey_validator_accepts_scorer():
    from goldenmatch.config.schemas import MatchkeyField

    field = MatchkeyField(field="first_name", scorer="given_name_aliased_jw", weight=1.0)
    assert field.scorer == "given_name_aliased_jw"


# ── data-file hygiene ──────────────────────────────────────────────────────


def test_no_duplicate_canonical_keys_in_bundled_json():
    """The v1 ship of given_name_aliases.json had ``"anthony"`` twice as a
    top-level key — json.load silently last-wins, so the earlier alias
    entry was invisibly dropped. The `_load` function now logs WARNING
    on duplicates, but the bundled data file should never trigger it.

    Lock the invariant with a direct check on the source JSON: every
    canonical key under "aliases" must be unique."""
    from importlib import resources

    with resources.files("goldenmatch.refdata.data").joinpath(
        "given_name_aliases.json",
    ).open("r", encoding="utf-8") as f:
        text = f.read()

    # Find every key under the "aliases" object. We walk the raw text to
    # detect duplicates BEFORE json.load collapses them.
    import re

    aliases_match = re.search(r'"aliases"\s*:\s*\{([^}]+)\}', text, re.DOTALL)
    assert aliases_match is not None
    keys = re.findall(r'^\s*"([^"]+)"\s*:', aliases_match.group(1), re.MULTILINE)
    duplicates = [k for k in keys if keys.count(k) > 1]
    assert not duplicates, f"Duplicate canonical keys in bundled JSON: {set(duplicates)}"
