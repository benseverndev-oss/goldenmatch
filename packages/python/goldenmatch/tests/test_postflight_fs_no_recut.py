"""Postflight must not re-cut a Fellegi-Sunter (probabilistic) matchkey's pairs.

`postflight()` reads the score histogram against `_resolve_current_threshold`: the first
WEIGHTED matchkey's threshold, else 0.7. An FS-only config has no weighted threshold, so
its pairs were measured against 0.7 -- a weighted scorer's default with no relation to the
link cut the FS model resolved (link-cut rules, then the refit guard). A bimodal histogram
whose valley sat more than 0.05 from 0.7 produced a threshold adjustment, and
`_apply_postflight` then dropped every FS pair below that valley before clustering. That is
a second, unguarded re-cut of the FS link: the refit that already moves the FS cut only
commits when re-clustering passes its over-merge and expelled-share checks.

Measured on MusicBrainz-20K zero-config (with the planner's bucket backend already removed):
F1 0.176 with the postflight re-cut, 0.475 without it.

Weighted matchkeys keep the adjustment; configs mixing a weighted matchkey with a
probabilistic one are unchanged here.
"""
from __future__ import annotations

import random

import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_verify import PreflightReport, postflight


def _df() -> pl.DataFrame:
    return pl.DataFrame({"name": [f"x{i}" for i in range(100)]})


def _blocking() -> BlockingConfig:
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["name"])])


def _fs_config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        blocking=_blocking(),
        matchkeys=[MatchkeyConfig(
            name="probabilistic_auto", type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="jaro_winkler")],
        )],
    )


def _weighted_config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        blocking=_blocking(),
        matchkeys=[MatchkeyConfig(
            name="mk", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="token_sort", weight=1.0)],
        )],
    )


def _bimodal_pairs() -> list[tuple[int, int, float]]:
    """Two modes at 0.2 and 0.9, the valley far from 0.7 -- the shape that re-cut."""
    rng = random.Random(42)
    low = [(i, i + 1000, max(0.0, min(1.0, rng.gauss(0.2, 0.08)))) for i in range(500)]
    high = [(i + 2000, i + 3000, max(0.0, min(1.0, rng.gauss(0.9, 0.05)))) for i in range(500)]
    return low + high


def test_postflight_emits_no_threshold_adjustment_for_an_fs_only_config() -> None:
    """THE REGRESSION. Before the fix this emitted a threshold adjustment to the valley."""
    report = postflight(_df(), _fs_config(), pair_scores=_bimodal_pairs())
    assert not any(adj.field == "threshold" for adj in report.adjustments)
    assert any("probabilistic" in a for a in report.advisories), (
        "declining silently looks exactly like a guard that never ran -- say why"
    )


def test_postflight_still_adjusts_a_weighted_config_on_the_same_scores() -> None:
    """The control: the signal is unchanged where there is a weighted threshold to move."""
    report = postflight(_df(), _weighted_config(), pair_scores=_bimodal_pairs())
    assert any(adj.field == "threshold" for adj in report.adjustments)


def test_apply_postflight_keeps_every_fs_pair() -> None:
    """End of the chain: an auto-configured FS config (it carries a preflight report)
    reaches clustering with its pairs intact."""
    from goldenmatch.core.pipeline import _apply_postflight

    cfg = _fs_config()
    cfg._preflight_report = PreflightReport()
    pairs = _bimodal_pairs()
    kept, report = _apply_postflight(_df(), cfg, pairs)
    assert report is not None, "postflight must still run and report its signals"
    assert kept == pairs
