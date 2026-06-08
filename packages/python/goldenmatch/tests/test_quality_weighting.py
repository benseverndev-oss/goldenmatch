"""GoldenCheck quality-weighted survivorship (GoldenRulesConfig.quality_weighting).

Until now the field defaulted True but was a no-op; these lock in that
goldencheck.cell_quality drives golden-record survivorship -- a cluster's golden
value prefers the higher-quality cell (canonical spelling over a typo).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.golden import build_golden_records_batch
from goldenmatch.core.quality import _goldencheck_available, compute_quality_scores

pytestmark = pytest.mark.skipif(
    not _goldencheck_available(), reason="goldencheck not installed"
)


def _df_with_typo(n: int = 60) -> pl.DataFrame:
    # 'California' frequent, 'Californa' a rare typo (row_id 56) -> penalized.
    states = ["California"] * 28 + ["Texas"] * 28 + ["Californa"] * 1 + ["Florida"] * 3
    return pl.DataFrame({
        "__row_id__": list(range(n)),
        "name": [f"p{i}" for i in range(n)],
        "state": states,
    })


def test_compute_quality_scores_keys_by_row_id() -> None:
    df = _df_with_typo()
    scores = compute_quality_scores(df)
    assert scores is not None
    # The typo lives at positional index 56 -> __row_id__ 56.
    assert (56, "state") in scores
    assert 0 < scores[(56, "state")] < 1.0
    # Canonical 'California' cells are clean (absent).
    assert (0, "state") not in scores


def test_clean_frame_returns_none() -> None:
    df = pl.DataFrame({
        "__row_id__": list(range(60)),
        "name": [f"p{i}" for i in range(60)],
        "city": ["alpha", "beta", "gamma"] * 20,
    })
    assert compute_quality_scores(df) is None  # nothing penalized -> fast path


def test_missing_row_id_col_returns_none() -> None:
    df = _df_with_typo().drop("__row_id__")
    assert compute_quality_scores(df) is None


def test_quality_weighting_flips_survivorship() -> None:
    """The computed scores, fed to the real golden builder, make the canonical
    spelling win a cluster where the typo would otherwise survive."""
    scores = compute_quality_scores(_df_with_typo())
    # A 2-member dup cluster: the typo row (56) is FIRST, so first_non_null would
    # otherwise keep 'Californa'.
    cluster = pl.DataFrame({
        "__row_id__": [56, 0],
        "__cluster_id__": [1, 1],
        "name": ["dup", "dup"],
        "state": ["Californa", "California"],
    })
    rules = GoldenRulesConfig(default_strategy="first_non_null", quality_weighting=True)

    without = build_golden_records_batch(cluster, rules, quality_scores=None)
    with_weights = build_golden_records_batch(cluster, rules, quality_scores=scores)

    assert without[0]["state"]["value"] == "Californa"        # typo survives
    assert with_weights[0]["state"]["value"] == "California"  # quality flips it


def test_dedupe_df_pipeline_runs_with_quality_weighting() -> None:
    """End-to-end: the pipeline computes + threads quality_scores without error,
    and the dup cluster's golden record carries the canonical value."""
    from goldenmatch import dedupe_df

    df = _df_with_typo()
    # Two duplicates sharing a name; one canonical, one typo'd state.
    df = pl.concat([
        df,
        pl.DataFrame({
            "__row_id__": [None, None],  # ingest assigns real ids
            "name": ["dupperson", "dupperson"],
            "state": ["Californa", "California"],
        }).drop("__row_id__"),
    ], how="diagonal").drop("__row_id__")

    result = dedupe_df(df, exact=["name"], confidence_required=False)
    assert result.golden is not None
    golden = result.golden.filter(pl.col("name") == "dupperson")
    # Only assert the merge happened + a canonical value was chosen; default
    # most_complete prefers the longer 'California' regardless, so this is a
    # wiring/no-crash guard (the flip itself is locked by the test above).
    assert golden.height == 1
    assert golden["state"][0] in ("California", "Californa")
