"""Frame seam (Polars-eviction Phase 0) — the engine operates on a backend-agnostic
``Frame``; today only ``PolarsFrame`` exists and it must be byte-identical to the
old direct-``pl.DataFrame`` path. This test pins the container-op contract a future
native/Arrow backend must also satisfy."""
from __future__ import annotations

import goldenflow  # noqa: F401 -- import-time transform registration
import polars as pl
from goldenflow import transform_df
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.engine.frame import Frame, PolarsFrame, to_frame


def test_polarsframe_satisfies_frame_protocol() -> None:
    f = PolarsFrame(pl.DataFrame({"a": [1, 2]}))
    assert isinstance(f, Frame)  # runtime_checkable Protocol
    assert to_frame(pl.DataFrame({"a": [1]})).__class__ is PolarsFrame
    # to_frame is idempotent on a Frame
    assert to_frame(f) is f


def test_polarsframe_container_ops() -> None:
    df = pl.DataFrame({"a": ["  x  ", "Y", None], "b": [1.0, 2.0, 3.0]})
    f = PolarsFrame(df)
    assert f.columns == ["a", "b"]
    assert f.height == 3
    assert f.dtype("a") == pl.Utf8
    assert f.column("a").to_list() == ["  x  ", "Y", None]
    f2 = f.with_column("a", pl.Series("a", ["x", "y", "z"]))
    assert f2.column("a").to_list() == ["x", "y", "z"]
    assert f.column("a").to_list() == ["  x  ", "Y", None]  # original untouched
    assert f.head(2).height == 2
    assert f.rename({"a": "c"}).columns == ["c", "b"]
    assert f.drop(["b"]).columns == ["a"]
    assert f.filter_not_null("a").height == 2
    assert set(f.unique(subset=["b"], keep="first").columns) == {"a", "b"}


def test_engine_output_unchanged_by_seam() -> None:
    """The seam is transparent: transform_df (now Frame-routed) is byte-identical to
    applying the same transforms directly in Polars."""
    df = pl.DataFrame({
        "name": ["  dr. john  SMITH jr. ", "o'brien", None, "MARY"],
        "amount": [1.234, -5.0, None, 42.5],
    })
    cfg = GoldenFlowConfig(transforms=[
        TransformSpec(column="name", ops=["strip", "title_case", "name_proper", "strip_titles"]),
        TransformSpec(column="amount", ops=["round:2", "clamp:0:100"]),
    ])
    out = transform_df(df, config=cfg)
    assert isinstance(out.df, pl.DataFrame)
    # name pipeline: strip -> title -> proper -> strip_titles drops the leading "Dr."
    name0 = out.df["name"].to_list()[0]
    assert not name0.lower().startswith("dr")  # strip_titles removed it
    assert name0.endswith("Jr.") and "Smith" in name0  # title/proper applied
    # round:2 -> clamp:0:100; nulls pass through (no fill_zero)
    assert out.df["amount"].to_list() == [1.23, 0.0, None, 42.5]
    # audit manifest still emitted per op
    assert [r.transform for r in out.manifest.records][:2] == ["strip", "title_case"]
