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
    spelling win a cluster where the typo would otherwise survive.

    Survivorship now breaks ties deterministically by lowest ``__row_id__`` (#870),
    so the typo is placed at the LOW row_id: without quality weighting the
    deterministic pick keeps the typo, and quality weighting flips it to canonical.
    """
    # Typo 'Californa' at the LOWEST row_id (0) so the unweighted first_non_null
    # pick (lowest __row_id__) would keep it; canonical 'California' is frequent
    # and clean, so quality weighting penalizes only the typo cell.
    typo_df = pl.DataFrame({
        "__row_id__": list(range(60)),
        "name": [f"p{i}" for i in range(60)],
        "state": ["Californa"] + ["California"] * 28 + ["Texas"] * 28 + ["Florida"] * 3,
    })
    scores = compute_quality_scores(typo_df)
    assert scores is not None and (0, "state") in scores  # the typo cell is penalized
    cluster = pl.DataFrame({
        "__row_id__": [0, 1],
        "__cluster_id__": [1, 1],
        "name": ["dup", "dup"],
        "state": ["Californa", "California"],
    })
    rules = GoldenRulesConfig(default_strategy="first_non_null", quality_weighting=True)

    without = build_golden_records_batch(cluster, rules, quality_scores=None)
    with_weights = build_golden_records_batch(cluster, rules, quality_scores=scores)

    assert without[0]["state"]["value"] == "Californa"        # lowest-row_id typo survives
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
    import pyarrow.compute as pc

    golden = result.golden.filter(pc.equal(result.golden.column("name"), "dupperson"))
    # Only assert the merge happened + a canonical value was chosen; default
    # most_complete prefers the longer 'California' regardless, so this is a
    # wiring/no-crash guard (the flip itself is locked by the test above).
    assert golden.num_rows == 1
    assert golden.column("state")[0].as_py() in ("California", "Californa")


def test_quality_scan_scoped_to_cluster_members(monkeypatch):
    """The golden-stage cell_quality scan must run only over rows that get a
    golden record (multi-member, non-oversized cluster members), NOT the whole
    frame -- a singleton row never consumes a quality weight. Regression for the
    full-frame scan that ran on every default dedupe."""
    import goldenmatch.core.quality as q
    from goldenmatch import dedupe_df

    seen: dict[str, int] = {}
    orig = q.compute_quality_scores

    def _recording(df):
        seen["height"] = df.height
        return orig(df)

    monkeypatch.setattr(q, "compute_quality_scores", _recording)

    # 2 exact-email duplicate pairs (4 member rows) + 4 unique singletons.
    df = pl.DataFrame({
        "name": ["Ann Lee", "Ann Lee", "Bob Ray", "Bob Ray",
                 "Cy Xu", "Dee Fo", "Ed Gu", "Fi Ho"],
        "email": ["a@x.com", "a@x.com", "b@y.com", "b@y.com",
                  "c@z.com", "d@w.com", "e@v.com", "f@u.com"],
    })
    res = dedupe_df(df)

    # The dup pairs must have clustered, and the scan must have seen ONLY the
    # 4 member rows -- not all 8 (which is what the pre-fix full-frame scan did).
    assert res.clusters, "expected the exact-email dup pairs to cluster"
    assert seen.get("height") == 4
