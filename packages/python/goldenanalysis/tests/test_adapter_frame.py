"""The generic frame adapter."""

from __future__ import annotations

import polars as pl

from goldenanalysis.adapters import FrameArtifactAdapter


def test_load_defaults_dataset() -> None:
    df = pl.DataFrame({"a": [1, 2, 3]})
    inp = FrameArtifactAdapter().load(df)
    assert inp.dataset == "frame"
    assert inp.frame is df


def test_load_named_dataset() -> None:
    df = pl.DataFrame({"a": [1]})
    inp = FrameArtifactAdapter().load(df, dataset="customers")
    assert inp.dataset == "customers"
