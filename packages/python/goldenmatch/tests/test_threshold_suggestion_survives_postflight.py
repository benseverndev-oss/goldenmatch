"""An applied threshold suggestion must survive `_apply_postflight`.

`_apply_postflight` re-cuts the scored pairs to its OWN recomputed threshold
whenever the config came from `auto_configure_df` (it carries a
`_preflight_report`), unless `_strict_autoconfig` is set. Applying a
`set_threshold` suggestion truncates the score distribution; postflight then
recomputes a cut FROM that truncated distribution and the raise is undone --
the postflight-on-a-truncated-distribution failure this codebase has hit before.

The visible symptom was that threshold suggestions became unverifiable.
`review_config`'s verify gate scored `thr:raise:fuzzy_match` at cand_health
0.8200 against a 0.6467 baseline with postflight suppressed, and 0.3913 -> DROP
with it active. The suggester was correctly refusing to recommend a change the
pipeline would immediately revert -- so `suggest_quality`'s dblp_acm convergence
sat at base (0.5645) instead of 0.7296.

It surfaced when the TUI's MatchEngine stopped running its own parallel pipeline
and delegated to `run_dedupe_df` (#2826): the old copy never called postflight,
so verification had been measuring a pipeline that did not re-cut. The delegation
was right -- verification should measure the shipped path -- which is what made
the underlying conflict visible.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.suggest.apply import apply_suggestion
from goldenmatch.core.suggest.types import Suggestion


def _config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_match",
                type="weighted",
                threshold=0.65,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
        # A weighted matchkey requires a blocking config; irrelevant to what is
        # asserted here, but the schema refuses to build without it.
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["name"], transforms=["lowercase"])],
        ),
    )


def _suggestion(**patch) -> Suggestion:
    kind = patch.pop("kind", "raise_threshold")
    return Suggestion(
        id=f"test:{kind}",
        kind=kind,
        target="fuzzy_match",
        rationale="test",
        current_value=0.65,
        proposed_value=patch.get("value", 0.82),
        predicted_effect="test",
        confidence=1.0,
        patch=patch,
    )


def test_a_threshold_patch_pins_strict_autoconfig():
    cfg = apply_suggestion(
        _config(),
        _suggestion(op="set_threshold", matchkey="fuzzy_match", value=0.82),
    )
    assert cfg._strict_autoconfig is True
    assert cfg.get_matchkeys()[0].threshold == pytest.approx(0.82)


def test_other_patches_do_not_pin_strict():
    """Only `set_threshold` fights postflight. Pinning strict for the rest would
    suppress adjustments nobody asked to keep."""
    cfg = apply_suggestion(
        _config(),
        _suggestion(
            kind="set_scorer",
            op="set_scorer",
            matchkey="fuzzy_match",
            field="name",
            scorer="levenshtein",
        ),
    )
    assert not getattr(cfg, "_strict_autoconfig", False)


def test_the_original_config_is_still_not_mutated():
    original = _config()
    apply_suggestion(
        original,
        _suggestion(op="set_threshold", matchkey="fuzzy_match", value=0.82),
    )
    assert not getattr(original, "_strict_autoconfig", False)
    assert original.get_matchkeys()[0].threshold == pytest.approx(0.65)


def test_postflight_does_not_recut_a_strict_config():
    """The other half of the guarantee: strict must actually suppress the re-cut.

    Without it, postflight filters the pair list to its own `adj.to_value` and
    the applied threshold is gone.
    """
    pl = pytest.importorskip("polars")
    from goldenmatch.core.autoconfig_verify import PreflightReport
    from goldenmatch.core.pipeline import _apply_postflight

    df = pl.DataFrame(
        {"__row_id__": list(range(6)), "name": [f"n{i}" for i in range(6)]}
    )
    pairs = [(0, 1, 0.95), (2, 3, 0.85), (4, 5, 0.70)]

    cfg = apply_suggestion(
        _config(),
        _suggestion(op="set_threshold", matchkey="fuzzy_match", value=0.82),
    )
    cfg._preflight_report = PreflightReport()

    out, _report = _apply_postflight(df, cfg, list(pairs))
    assert out == pairs, "postflight re-cut a config that pinned its threshold"
