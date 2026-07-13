"""Block-SIZE measurement parity gate (autoconfig arrow-port PR-5).

The block-size measurement functions (``_fast_static_block_sizes`` /
``measure_blocking_profile``) were rewired off ``pl.Expr`` / lazy ``group_by``
onto the proven ``frame.derive_block_key`` seam op + ``filter_valid_key`` +
``group_len``. This is recall-adjacent: the block key decides which records are
ever compared, so a silent divergence between the seam key and the legacy
``_build_block_key_expr`` key would drop true pairs.

Two gates:

(a) ``derive_block_key(fields, transforms)`` must produce byte-identical
    per-row keys on ``PolarsFrame`` vs ``ArrowFrame`` (same data) AND both must
    equal the legacy ``_build_block_key_expr`` output on the polars path, across
    every transform in the corpus (incl. soundex/phonetic map_elements chains,
    numeric fields, substring, nulls, sentinels, duplicates). A mismatch on ANY
    transform is a recall break -- STOP, do not ship.

(b) ``_fast_static_block_sizes`` / ``measure_blocking_profile`` must produce
    identical block-size results on a polars frame vs an arrow frame AND both
    must equal the pinned pre-rewire polars oracle (the exact legacy
    ``group_by(block_key_expr).agg(pl.len())`` reduction, replicated inline).
"""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.blocker import (
    _build_block_key_expr,
    _fast_static_block_sizes,
    measure_blocking_profile,
)
from goldenmatch.core.frame import ArrowFrame, PolarsFrame

# ---- realistic multi-matchkey corpus -------------------------------------
# Several fields, mixed dtypes (string + numeric), transforms including
# soundex (map_elements), nulls, stringified sentinels, and duplicates that
# form real (size >= 2) blocks.

_DATA = {
    "first": ["John", "john", "JANE", None, "Bob", "Bob", "söund", "Bob"],
    "last": ["Smith", "Smith", "Doe", "Doe", "nan", "Nan", None, "smith"],
    "zip": [12345, 12345, 67890, 67890, None, 11111, 11111, 12345],
    "dob": [
        "1990-01-02", "1990-01-02", "1985-05-05", None,
        "2000-12-31", "2000-12-31", "1970-01-01", "1990-01-02",
    ],
}


def _df() -> pl.DataFrame:
    return pl.DataFrame(_DATA)


# (fields, transforms) covering native chains + map_elements (soundex) +
# numeric fields + multi-field composites.
_KEY_CASES = [
    (["first"], ["lowercase"]),
    (["first"], ["soundex"]),
    (["last"], ["lowercase", "soundex"]),
    (["first", "last"], ["lowercase", "strip"]),
    (["zip"], []),
    (["zip"], ["digits_only"]),
    (["first", "zip"], ["lowercase"]),
    (["dob"], ["substring:0:4"]),
    (["first", "last", "zip"], ["lowercase"]),
]


# ---- gate (a): key parity -------------------------------------------------


def _legacy_key(df: pl.DataFrame, fields, transforms) -> list:
    cfg = SimpleNamespace(fields=list(fields), transforms=list(transforms))
    return (
        df.lazy()
        .select(_build_block_key_expr(cfg))
        .collect()
        .get_column("__block_key__")
        .to_list()
    )


@pytest.mark.parametrize("fields,transforms", _KEY_CASES)
def test_derive_block_key_matches_legacy_and_cross_backend(fields, transforms):
    """derive_block_key (polars AND arrow) == legacy _build_block_key_expr.

    A divergence here is a silent recall break -- this assertion IS the gate.
    """
    df = _df()
    legacy = _legacy_key(df, fields, transforms)
    seam_polars = PolarsFrame(df).derive_block_key(fields, transforms).to_list()
    seam_arrow = ArrowFrame(df.to_arrow()).derive_block_key(fields, transforms).to_list()

    assert seam_polars == legacy, (
        f"PolarsFrame.derive_block_key diverged from _build_block_key_expr on "
        f"{fields} / {transforms} -- RECALL BREAK"
    )
    assert seam_arrow == legacy, (
        f"ArrowFrame.derive_block_key diverged from _build_block_key_expr on "
        f"{fields} / {transforms} -- RECALL BREAK"
    )


# ---- gate (b): block-size measurement parity ------------------------------


def _oracle_fast_static(df: pl.DataFrame, config) -> tuple[list[int], int, int]:
    """Replica of the PRE-rewire ``_fast_static_block_sizes`` polars path.

    Pinned inline as the oracle so the rewired implementation is compared
    against the exact legacy reduction, independent of the rewire.
    """
    lf = df.lazy()
    keys = config.keys
    max_block_size = config.max_block_size
    skip_oversized = config.skip_oversized
    all_sizes: list[int] = []
    f1 = 0
    f2 = 0
    for key_config in keys:
        block_key_expr = _build_block_key_expr(key_config)
        agg = lf.group_by(block_key_expr).agg(pl.len().alias("__sz__")).collect()
        agg = agg.filter(
            pl.col("__block_key__").is_not_null()
            & ~pl.col("__block_key__")
            .str.strip_chars()
            .str.to_lowercase()
            .is_in(["nan", "null", "none"])
        )
        all_key_sizes = agg.get_column("__sz__").to_list()
        f1 += sum(1 for s in all_key_sizes if s == 1)
        f2 += sum(1 for s in all_key_sizes if s == 2)
        sizes = [s for s in all_key_sizes if s >= 2]
        if skip_oversized and any(s > max_block_size for s in sizes):
            return None  # type: ignore[return-value]
        all_sizes.extend(sizes)
    return all_sizes, f1, f2


def _norm(result):
    """Sort the sizes list so oracle/impl compare as an order-free multiset.

    The seam's group_len and the legacy group_by both yield an unordered set of
    per-key sizes (block order is never contractual downstream); sort to compare
    the multiset + the Chao1 counts.
    """
    if result is None:
        return None
    sizes, f1, f2 = result
    return sorted(sizes), f1, f2


_SIZE_CONFIGS = [
    # single string key, native transform
    BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["first"], transforms=["lowercase"])],
        max_block_size=1000,
        skip_oversized=False,
        auto_select=False,
    ),
    # soundex (map_elements) key
    BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last"], transforms=["lowercase", "soundex"])],
        max_block_size=1000,
        skip_oversized=False,
        auto_select=False,
    ),
    # numeric key, no transform
    BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["zip"], transforms=[])],
        max_block_size=1000,
        skip_oversized=False,
        auto_select=False,
    ),
    # multi-field composite
    BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["first", "zip"], transforms=["lowercase"])],
        max_block_size=1000,
        skip_oversized=False,
        auto_select=False,
    ),
    # multiple keys at once (f1/f2 accumulate across keys)
    BlockingConfig(
        strategy="static",
        keys=[
            BlockingKeyConfig(fields=["first"], transforms=["lowercase"]),
            BlockingKeyConfig(fields=["dob"], transforms=["substring:0:4"]),
        ],
        max_block_size=1000,
        skip_oversized=False,
        auto_select=False,
    ),
]


@pytest.mark.parametrize("config", _SIZE_CONFIGS)
def test_fast_static_block_sizes_polars_arrow_vs_oracle(config):
    df = _df()
    oracle = _norm(_oracle_fast_static(df, config))

    # polars input (DataFrame + LazyFrame both accepted)
    got_pl_lf = _norm(_fast_static_block_sizes(df.lazy(), config))
    got_pl_df = _norm(_fast_static_block_sizes(PolarsFrame(df), config))
    # arrow input via the seam
    got_arrow = _norm(_fast_static_block_sizes(ArrowFrame(df.to_arrow()), config))

    assert got_pl_lf == oracle, "polars LazyFrame result diverged from pre-rewire oracle"
    assert got_pl_df == oracle, "PolarsFrame result diverged from pre-rewire oracle"
    assert got_arrow == oracle, "ArrowFrame result diverged from polars/oracle"


@pytest.mark.parametrize("config", _SIZE_CONFIGS)
def test_measure_blocking_profile_polars_arrow_identical(config):
    df = _df()
    # measure_blocking_profile reads ``config.blocking`` (a GoldenMatchConfig),
    # not a bare BlockingConfig.
    top = SimpleNamespace(blocking=config)

    prof_pl = measure_blocking_profile(df, top)
    prof_arrow = measure_blocking_profile(ArrowFrame(df.to_arrow()), top)

    assert prof_pl is not None
    assert prof_arrow is not None

    # The full block-size distribution must be identical arrow-vs-polars.
    for attr in (
        "n_blocks",
        "total_comparisons",
        "reduction_ratio",
        "block_sizes_p50",
        "block_sizes_p95",
        "block_sizes_p99",
        "block_sizes_max",
        "singleton_block_count",
        "oversized_block_count",
        "chao1_f1",
        "chao1_f2",
    ):
        assert getattr(prof_pl, attr) == getattr(prof_arrow, attr), (
            f"measure_blocking_profile.{attr} diverged arrow-vs-polars"
        )


def test_measure_blocking_profile_oversized_skip_falls_back():
    """skip_oversized=True + an oversized block => _fast_static returns None,
    measure_blocking_profile falls back to the exact build_blocks loop (which
    still produces a profile). Locks the oversized-bail semantics survive."""
    df = _df()
    config = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["first"], transforms=["lowercase"])],
        max_block_size=1,  # any real block is "oversized"
        skip_oversized=True,
        auto_select=False,
    )
    # _fast_static bails (returns None) on both backends.
    assert _fast_static_block_sizes(df.lazy(), config) is None
    assert _fast_static_block_sizes(ArrowFrame(df.to_arrow()), config) is None
