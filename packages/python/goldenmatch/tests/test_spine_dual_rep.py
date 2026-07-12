"""D2s-a pins: exact-match + static-blocking entries accept a seam Frame.

The spine descent (plan 2026-07-12, D2s series) moves the arrow->polars
boundary below the collect; these fixtures pin that the two hot lazy-consumer
entries produce identical output for a pl.LazyFrame, a PolarsFrame, and an
ArrowFrame carrying the same rows.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.frame import ArrowFrame, PolarsFrame
from goldenmatch.core.scorer import _find_exact_match_ids, find_exact_matches


def _df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": pl.Series([0, 1, 2, 3, 4, 5], dtype=pl.Int64),
            "name": ["ann", "ann", "bob", None, "nan", "cat"],
            "__mk_k__": ["a1", "a1", "b2", None, "", "c3"],
        }
    )


def _entries():
    df = _df()
    return {
        "lazy": df.lazy(),
        "polars_frame": PolarsFrame(df),
        "arrow_frame": ArrowFrame(df.to_arrow()),
    }


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_find_exact_match_ids_dual_rep(rep):
    mk = MatchkeyConfig(name="k", type="exact", fields=[MatchkeyField(field="name")])
    ids_a, ids_b = _find_exact_match_ids(_entries()[rep], mk)
    assert sorted(zip(ids_a.tolist(), ids_b.tolist())) == [(0, 1)]


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_find_exact_matches_dual_rep(rep):
    mk = MatchkeyConfig(name="k", type="exact", fields=[MatchkeyField(field="name")])
    assert find_exact_matches(_entries()[rep], mk) == [(0, 1, 1.0)]


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_build_static_blocks_dual_rep(rep):
    # "nan" is a sentinel-filtered key; null drops; ann pair survives.
    cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])])
    blocks = build_blocks(_entries()[rep], cfg)
    keyed = {b.block_key: sorted(b.materialize().column("__row_id__").to_list()) for b in blocks}
    assert keyed == {"ann": [0, 1]}


# -- D2s-b: Frame-level precompute_matchkey_transforms ------------------------------


def _mk_cfgs():
    from goldenmatch.config.schemas import NegativeEvidenceField

    return [
        MatchkeyConfig(
            name="a",
            type="weighted", threshold=0.8,
            fields=[
                MatchkeyField(field="first", transforms=["lowercase", "strip"], scorer="jaro_winkler", weight=1.0),
                MatchkeyField(field="last", transforms=["soundex"], scorer="jaro_winkler", weight=1.0),
                MatchkeyField(field="plain", scorer="exact", weight=1.0),
            ],
            negative_evidence=[
                NegativeEvidenceField(
                    field="full_name",
                    derive_from=["first", "last"],
                    scorer="jaro_winkler",
                    threshold=0.9,
                    penalty=0.5,
                )
            ],
        ),
        # Same (field, transforms) as mk "a" -> signature reuse, no dup column.
        MatchkeyConfig(
            name="b",
            type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="first", transforms=["lowercase", "strip"], scorer="jaro_winkler", weight=1.0)],
        ),
    ]


def test_precompute_frame_arrow_matches_legacy_polars():
    from goldenmatch.core.matchkey import (
        precompute_matchkey_transforms,
        precompute_matchkey_transforms_frame,
    )

    df = pl.DataFrame(
        {
            "__row_id__": pl.Series([0, 1, 2], dtype=pl.Int64),
            "first": ["  Ann ", None, "BOB"],
            "last": ["Smith", "Jones", None],
            "plain": ["x", None, "z"],
        }
    )
    mks = _mk_cfgs()
    legacy = precompute_matchkey_transforms(df, mks)
    arrow = precompute_matchkey_transforms_frame(ArrowFrame(df.to_arrow()), mks)
    # Column ORDER is not part of the contract (consumers read __xform_*__
    # by name; legacy batches python-fallback columns after all native ones).
    assert set(arrow.columns) == set(legacy.columns)
    for c in legacy.columns:
        assert arrow.column(c).to_list() == legacy[c].to_list(), c


def test_precompute_frame_polars_delegates_verbatim():
    from goldenmatch.core.matchkey import (
        precompute_matchkey_transforms,
        precompute_matchkey_transforms_frame,
    )

    df = pl.DataFrame({"__row_id__": [0, 1], "first": ["A", "b"]})
    mks = [
        MatchkeyConfig(
            name="a", type="weighted", threshold=0.8, fields=[MatchkeyField(field="first", transforms=["lowercase"], scorer="jaro_winkler", weight=1.0)]
        )
    ]
    out = precompute_matchkey_transforms_frame(PolarsFrame(df), mks)
    assert out.native.equals(precompute_matchkey_transforms(df, mks))


def test_precompute_frame_skips_record_embedding_and_missing_fields():
    from goldenmatch.core.matchkey import precompute_matchkey_transforms_frame

    df = pl.DataFrame({"__row_id__": [0], "first": ["a"]})
    mks = [
        MatchkeyConfig(
            name="a",
            type="weighted", threshold=0.8,
            fields=[
                MatchkeyField(field="first", scorer="record_embedding", columns=["first"], weight=1.0),
                MatchkeyField(field="absent", scorer="exact", weight=1.0),
            ],
        )
    ]
    out = precompute_matchkey_transforms_frame(ArrowFrame(df.to_arrow()), mks)
    assert list(out.columns) == ["__row_id__", "first"]


# -- D2s-d2a: the Frame-lane eligibility predicate ----------------------------------


def _base_cfg(**overrides):
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="k",
                type="exact",
                fields=[MatchkeyField(field="name")],
            )
        ],
        **overrides,
    )
    return cfg


def _eligible(cfg, writes_outputs=False):
    from goldenmatch.core.pipeline import _frame_lane_eligible

    return _frame_lane_eligible(cfg, cfg.get_matchkeys(), writes_outputs=writes_outputs)


def test_frame_lane_eligible_baseline():
    assert _eligible(_base_cfg()) is True


def test_frame_lane_declines_writes_outputs():
    assert _eligible(_base_cfg(), writes_outputs=True) is False


def test_frame_lane_declines_throughput_plan():
    cfg = _base_cfg()
    object.__setattr__(cfg, "_throughput_plan", object())
    assert _eligible(cfg) is False


def test_frame_lane_declines_preflight():
    cfg = _base_cfg()
    object.__setattr__(cfg, "_preflight_report", {"x": 1})
    assert _eligible(cfg) is False


def test_frame_lane_declines_probabilistic_mk():
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="p",
                type="probabilistic",
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
                threshold=0.8,
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])]),
    )
    assert _eligible(cfg) is False


def test_frame_lane_declines_ne_on_exact():
    from goldenmatch.config.schemas import NegativeEvidenceField

    cfg = _base_cfg()
    cfg.matchkeys[0].negative_evidence = [
        NegativeEvidenceField(
            field="name", scorer="jaro_winkler", threshold=0.9, penalty=0.5
        )
    ]
    assert _eligible(cfg) is False
