"""Tests for the perturbation catalog (Task 3 of the suggester gym).

TDD: this file is written BEFORE perturbations.py and must fail until
the implementation is complete.
"""
from __future__ import annotations


def _cfg():
    """Minimal weighted matchkey config with two free-text fields."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="person",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="address", scorer="jaro_winkler", weight=1.0),
        ],
    )
    # Weighted matchkeys require blocking.
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["first_name"])],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


def _cfg_with_ne():
    """Config whose primary weighted matchkey already has negative_evidence."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )

    mk = MatchkeyConfig(
        name="person",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="address", scorer="jaro_winkler", weight=1.0),
        ],
        negative_evidence=[
            NegativeEvidenceField(
                field="email",
                scorer="exact",
                threshold=0.9,
                penalty=0.3,
            )
        ],
    )
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["first_name"])],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


def _cfg_multi_pass():
    """Config with multi-pass blocking (>1 pass so dropped_blocking_pass can apply)."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="person",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="address", scorer="jaro_winkler", weight=1.0),
        ],
    )
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["first_name"]),
            BlockingKeyConfig(fields=["address"]),
        ],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


# ── shape + catalog invariants ────────────────────────────────────────────────


def test_every_catalog_entry_has_required_shape():
    from scripts.suggest_quality.perturbations import CATALOG

    for p in CATALOG:
        assert p.name, f"Perturbation missing name: {p}"
        assert p.expected_rule is not None, f"{p.name}: expected_rule is None"
        assert callable(p.apply), f"{p.name}: apply not callable"
        assert callable(p.applies_to), f"{p.name}: applies_to not callable"
        assert isinstance(p.builds_on_existing_rule, bool), (
            f"{p.name}: builds_on_existing_rule must be bool"
        )


def test_catalog_has_nine_entries():
    from scripts.suggest_quality.perturbations import CATALOG

    assert len(CATALOG) == 9, f"Expected 9 catalog entries, got {len(CATALOG)}"


def test_built_rule_expected_rules_are_real_suggestion_kinds():
    from scripts.suggest_quality.perturbations import CATALOG

    real = {"raise_threshold", "lower_threshold", "swap_scorer", "add_negative_evidence"}
    for p in CATALOG:
        if p.builds_on_existing_rule:
            assert p.expected_rule in real, (
                f"{p.name}: {p.expected_rule!r} not a real SuggestionKind"
            )


def test_get_by_name_returns_correct_perturbation():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    p = get_perturbation("threshold_too_low")
    assert p.name == "threshold_too_low"


def test_get_unknown_raises_key_error():
    import pytest

    from scripts.suggest_quality.perturbations import get as get_perturbation

    with pytest.raises(KeyError):
        get_perturbation("does_not_exist")


# ── threshold_too_low ─────────────────────────────────────────────────────────


def test_threshold_too_low_lowers_and_preserves_original():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    p = get_perturbation("threshold_too_low")
    orig = _cfg()
    out = p.apply(orig)
    assert out.get_matchkeys()[0].threshold == pytest_approx(0.70)  # 0.85 - 0.15
    assert orig.get_matchkeys()[0].threshold == pytest_approx(0.85)  # immutability


def test_threshold_too_low_applies_to_weighted_config():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("threshold_too_low").applies_to(_cfg()) is True


def test_threshold_too_low_floor_at_0_50():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    from scripts.suggest_quality.perturbations import get as get_perturbation

    mk = MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.55,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )
    cfg = GoldenMatchConfig(
        matchkeys=[mk],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["name"])]),
    )
    out = get_perturbation("threshold_too_low").apply(cfg)
    # 0.55 - 0.15 = 0.40, clamped to 0.50
    assert out.get_matchkeys()[0].threshold == pytest_approx(0.50)


# ── threshold_too_high ────────────────────────────────────────────────────────


def test_threshold_too_high_raises_and_preserves_original():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    p = get_perturbation("threshold_too_high")
    orig = _cfg()
    out = p.apply(orig)
    assert out.get_matchkeys()[0].threshold == pytest_approx(0.95)  # 0.85 + 0.10
    assert orig.get_matchkeys()[0].threshold == pytest_approx(0.85)


def test_threshold_too_high_applies_to_weighted_config():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("threshold_too_high").applies_to(_cfg()) is True


def test_threshold_too_high_ceiling_at_0_99():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    from scripts.suggest_quality.perturbations import get as get_perturbation

    mk = MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.95,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )
    cfg = GoldenMatchConfig(
        matchkeys=[mk],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["name"])]),
    )
    out = get_perturbation("threshold_too_high").apply(cfg)
    # 0.95 + 0.10 = 1.05, clamped to 0.99
    assert out.get_matchkeys()[0].threshold == pytest_approx(0.99)


# ── bad_freetext_scorer ───────────────────────────────────────────────────────


def test_bad_freetext_scorer_sets_token_sort():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    out = get_perturbation("bad_freetext_scorer").apply(_cfg())
    addr = [f for f in out.get_matchkeys()[0].fields if f.field == "address"][0]
    assert addr.scorer == "token_sort"


def test_bad_freetext_scorer_preserves_original():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg()
    get_perturbation("bad_freetext_scorer").apply(orig)
    addr = [f for f in orig.get_matchkeys()[0].fields if f.field == "address"][0]
    assert addr.scorer == "jaro_winkler"  # original unchanged


def test_bad_freetext_scorer_applies_to_freetext_config():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("bad_freetext_scorer").applies_to(_cfg()) is True


def test_bad_freetext_scorer_skips_jaro_winkler_email_field():
    """A jaro_winkler-on-email field must NOT be picked as 'free text'.

    Auto-config routinely uses jaro_winkler on email/phone/id fields, so the
    scorer alone is not a free-text signal. With ONLY an email field (no
    real free-text field), bad_freetext_scorer must not apply; with a real
    free-text field present, it must pick that one and leave email alone."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    from scripts.suggest_quality.perturbations import get as get_perturbation

    # Email-only config: no real free-text field -> must not apply.
    email_only_mk = MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.85,
        fields=[MatchkeyField(field="email", scorer="jaro_winkler", weight=1.0)],
    )
    email_only = GoldenMatchConfig(
        matchkeys=[email_only_mk],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["email"])]),
    )
    assert get_perturbation("bad_freetext_scorer").applies_to(email_only) is False

    # email (jaro_winkler) + address (a real free-text field). Must pick
    # address, leaving email untouched.
    mixed_mk = MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(field="email", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="address", scorer="jaro_winkler", weight=1.0),
        ],
    )
    mixed = GoldenMatchConfig(
        matchkeys=[mixed_mk],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["email"])]),
    )
    out = get_perturbation("bad_freetext_scorer").apply(mixed)
    out_fields = {f.field: f.scorer for f in out.get_matchkeys()[0].fields}
    assert out_fields["address"] == "token_sort"
    assert out_fields["email"] == "jaro_winkler"  # email left alone


def test_bad_freetext_scorer_not_applies_when_already_token_sort():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    from scripts.suggest_quality.perturbations import get as get_perturbation

    mk = MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.85,
        fields=[MatchkeyField(field="address", scorer="token_sort", weight=1.0)],
    )
    cfg = GoldenMatchConfig(
        matchkeys=[mk],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["address"])]),
    )
    assert get_perturbation("bad_freetext_scorer").applies_to(cfg) is False


# ── missing_negative_evidence ─────────────────────────────────────────────────


def test_missing_negative_evidence_drops_one_entry():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg_with_ne()
    out = get_perturbation("missing_negative_evidence").apply(orig)
    # Original had 1, perturbation drops it.
    assert out.get_matchkeys()[0].negative_evidence == [] or (
        out.get_matchkeys()[0].negative_evidence is None
        or len(out.get_matchkeys()[0].negative_evidence) == 0
    )


def test_missing_negative_evidence_preserves_original():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg_with_ne()
    get_perturbation("missing_negative_evidence").apply(orig)
    assert len(orig.get_matchkeys()[0].negative_evidence) == 1


def test_missing_negative_evidence_applies_to_ne_config():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("missing_negative_evidence").applies_to(_cfg_with_ne()) is True


def test_missing_negative_evidence_not_applies_without_ne():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("missing_negative_evidence").applies_to(_cfg()) is False


# ── dropped_blocking_pass ─────────────────────────────────────────────────────


def test_dropped_blocking_pass_removes_one_pass():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg_multi_pass()
    out = get_perturbation("dropped_blocking_pass").apply(orig)
    assert len(out.blocking.passes) == 1


def test_dropped_blocking_pass_preserves_original():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg_multi_pass()
    get_perturbation("dropped_blocking_pass").apply(orig)
    assert len(orig.blocking.passes) == 2


def test_dropped_blocking_pass_applies_to_multi_pass():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("dropped_blocking_pass").applies_to(_cfg_multi_pass()) is True


def test_dropped_blocking_pass_not_applies_without_multi_pass():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    # _cfg() uses static strategy with keys, no passes.
    assert get_perturbation("dropped_blocking_pass").applies_to(_cfg()) is False


# ── flattened_weights ─────────────────────────────────────────────────────────


def _cfg_skewed():
    """Config with intentionally different weights."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="person",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=2.0),
            MatchkeyField(field="address", scorer="jaro_winkler", weight=0.5),
        ],
    )
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["first_name"])],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


def test_flattened_weights_sets_all_to_1():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    out = get_perturbation("flattened_weights").apply(_cfg_skewed())
    for f in out.get_matchkeys()[0].fields:
        assert f.weight == pytest_approx(1.0)


def test_flattened_weights_preserves_original():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg_skewed()
    get_perturbation("flattened_weights").apply(orig)
    weights = [f.weight for f in orig.get_matchkeys()[0].fields]
    assert weights == [2.0, 0.5]


def test_flattened_weights_applies_to_skewed_config():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("flattened_weights").applies_to(_cfg_skewed()) is True


def test_flattened_weights_not_applies_when_already_equal():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    # _cfg() already has all weights=1.0
    assert get_perturbation("flattened_weights").applies_to(_cfg()) is False


# ── skewed_weight ─────────────────────────────────────────────────────────────


def test_skewed_weight_multiplies_one_field_by_5():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg()
    original_weights = [f.weight for f in orig.get_matchkeys()[0].fields]
    out = get_perturbation("skewed_weight").apply(orig)
    out_weights = [f.weight for f in out.get_matchkeys()[0].fields]
    # Exactly one field should be multiplied by 5; the rest unchanged.
    diffs = [o - e for o, e in zip(out_weights, original_weights)]
    # One diff should be +4.0 (1.0 * 5 - 1.0 = 4.0), others zero.
    boosted = [d for d in diffs if abs(d) > 0.01]
    assert len(boosted) == 1
    assert abs(boosted[0] - 4.0) < 0.01


def test_skewed_weight_preserves_original():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg()
    get_perturbation("skewed_weight").apply(orig)
    weights = [f.weight for f in orig.get_matchkeys()[0].fields]
    assert weights == [1.0, 1.0]


def test_skewed_weight_applies_to_multi_field_weighted():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("skewed_weight").applies_to(_cfg()) is True


# ── naive_single_fuzzy ────────────────────────────────────────────────────────


def test_naive_single_fuzzy_replaces_with_single_matchkey():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    out = get_perturbation("naive_single_fuzzy").apply(_cfg())
    mks = out.get_matchkeys()
    assert len(mks) == 1
    assert mks[0].type in ("weighted", "fuzzy")


def test_naive_single_fuzzy_default_threshold():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    out = get_perturbation("naive_single_fuzzy").apply(_cfg())
    assert out.get_matchkeys()[0].threshold == pytest_approx(0.85)


def test_naive_single_fuzzy_builds_fresh_single_field_static_blocking():
    """The naive config must build a fresh single-field static blocking config,
    NOT copy the original (possibly multi-pass) blocking -- copying would
    reference fields the single matchkey doesn't cover -> empty blocks -> a
    spurious F1=0 in the gym."""
    from scripts.suggest_quality.perturbations import get as get_perturbation

    # _cfg_multi_pass has a multi_pass blocking config with two passes.
    out = get_perturbation("naive_single_fuzzy").apply(_cfg_multi_pass())
    blocking = out.blocking
    assert blocking is not None
    assert blocking.strategy == "static"
    # No passes carried over from the multi-pass source.
    assert not blocking.passes
    # Single static key covering exactly the one naive matchkey field.
    assert len(blocking.keys) == 1
    naive_field = out.get_matchkeys()[0].fields[0].field
    assert blocking.keys[0].fields == [naive_field]


def test_naive_single_fuzzy_preserves_original():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    orig = _cfg()
    get_perturbation("naive_single_fuzzy").apply(orig)
    assert len(orig.get_matchkeys()) == 1
    assert len(orig.get_matchkeys()[0].fields) == 2


def test_naive_single_fuzzy_applies_to_config_with_string_fields():
    from scripts.suggest_quality.perturbations import get as get_perturbation

    assert get_perturbation("naive_single_fuzzy").applies_to(_cfg()) is True


# ── unbuilt-rule perturbations have correct metadata ─────────────────────────


def test_unbuilt_rule_perturbations_not_builds_on_existing():
    from scripts.suggest_quality.perturbations import CATALOG

    unbuilt_names = {
        "dropped_blocking_pass",
        "flattened_weights",
        "skewed_weight",
        "naive_single_fuzzy",
    }
    for p in CATALOG:
        if p.name in unbuilt_names:
            assert not p.builds_on_existing_rule, (
                f"{p.name} should have builds_on_existing_rule=False"
            )


def test_built_rule_perturbations_build_on_existing():
    from scripts.suggest_quality.perturbations import CATALOG

    built_names = {
        "threshold_too_low",
        "threshold_too_high",
        "bad_freetext_scorer",
        "missing_negative_evidence",
    }
    for p in CATALOG:
        if p.name in built_names:
            assert p.builds_on_existing_rule, (
                f"{p.name} should have builds_on_existing_rule=True"
            )


# ── applies_to returns False rather than crashing on minimal config ───────────


def test_applies_to_minimal_empty_config_does_not_crash():
    """applies_to must return False (not raise) for configs missing relevant structure."""
    from goldenmatch.config.schemas import GoldenMatchConfig

    from scripts.suggest_quality.perturbations import CATALOG

    minimal = GoldenMatchConfig(matchkeys=[])
    for p in CATALOG:
        result = p.applies_to(minimal)
        assert isinstance(result, bool), f"{p.name}: applies_to must return bool"


# ── helper to allow pytest_approx without importing at module level ───────────

def pytest_approx(val, **kw):
    import pytest
    return pytest.approx(val, **kw)
