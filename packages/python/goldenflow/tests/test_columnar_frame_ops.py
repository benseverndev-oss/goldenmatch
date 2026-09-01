"""Frame-level ops on the polars-free columnar engine.

`renames`, `drop`, `filters` and `dedup` used to force the Polars engine: any
config carrying one of them fell through `_frame_level_blocked`, and on a
default install (polars is an optional extra) that fallback raised. They were
never operations the columnar engine could not express -- they were
unimplemented ones.

Two things are pinned here:

* the columnar results MATCH the polars engine, config by config; and
* they are produced with polars BLOCKED at the meta path, so the test cannot
  pass by quietly falling back to the very engine it exists to avoid.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    TransformSpec,
)
from goldenflow.engine.columnar import _frame_level_blocked, transform_columns_public
from goldenflow.engine.frame import ColumnarFrame

DATA = {
    "name": ["  Ann ", "BOB", "  Ann ", "Cy", "  dee"],
    "email": ["A@X.COM", "b@x.com", "A@X.COM", None, "e@x.com"],
    "city": ["Leeds", "York", "Leeds", "Bath", "Hull"],
}

_STRIP = [TransformSpec(column="name", ops=["strip"])]

CASES = {
    "rename_and_drop": GoldenFlowConfig(
        transforms=_STRIP, renames={"email": "mail"}, drop=["city"]
    ),
    "filter_not_null": GoldenFlowConfig(
        transforms=_STRIP, filters=[FilterSpec(column="email", condition="not_null")]
    ),
    "filter_after": GoldenFlowConfig(
        transforms=_STRIP, filters=[FilterSpec(column="city", condition="after:Leeds")]
    ),
    "dedup_first": GoldenFlowConfig(
        transforms=_STRIP, dedup=DedupSpec(columns=["name"], keep="first")
    ),
    "dedup_last": GoldenFlowConfig(
        transforms=_STRIP, dedup=DedupSpec(columns=["name"], keep="last")
    ),
    "combined": GoldenFlowConfig(
        transforms=_STRIP,
        renames={"email": "mail"},
        filters=[FilterSpec(column="mail", condition="not_null")],
        dedup=DedupSpec(columns=["name"], keep="first"),
    ),
}


def _normalized(cols: dict) -> tuple:
    """Column names plus row SET, order-insensitive.

    `pl.DataFrame.unique` does not guarantee row order without
    `maintain_order=True`, so comparing row order would be asserting on
    something the reference does not promise.
    """
    keys = sorted(cols)
    rows = sorted(
        zip(*[cols[k] for k in keys]), key=lambda r: [str(x) for x in r]
    )
    return keys, rows


@pytest.mark.parametrize("label", sorted(CASES))
def test_columnar_frame_ops_match_the_polars_engine(label):
    pl = pytest.importorskip("polars", reason="the reference engine is polars")
    import goldenflow

    cfg = CASES[label]
    columnar = transform_columns_public(dict(DATA), cfg).columns

    res = goldenflow.transform_df(pl.DataFrame(DATA), config=cfg)
    pdf = res.df if hasattr(res, "df") else res
    polars_cols = {c: pdf[c].to_list() for c in pdf.columns}

    assert _normalized(columnar) == _normalized(polars_cols)


def test_frame_ops_no_longer_force_the_polars_engine():
    """`_frame_level_blocked` is the gate that sent these configs to polars."""
    for label, cfg in CASES.items():
        assert not _frame_level_blocked(cfg), f"{label} still forces the polars engine"


def test_whole_file_rust_route_still_rejects_every_frame_op():
    """The Rust CSV route runs read -> transform -> write with no Python step,
    so it cannot apply frame ops at all. It must keep the STRICT rule: sharing
    the loosened `_frame_level_blocked` would have let a rename take that route
    and silently vanish -- no error, just output missing the operation. No test
    in the suite covered that, so this is the one that would catch it."""
    from goldenflow.engine.columnar import columnar_file_ready

    for label, cfg in CASES.items():
        if cfg.renames or cfg.drop or cfg.filters or cfg.dedup:
            assert not columnar_file_ready(cfg), (
                f"{label} must not take the whole-file Rust route"
            )


def test_splits_still_declared_unsupported():
    """Honest boundary: a split dispatches as a `mode="dataframe"` transform
    that takes the NATIVE frame, and those functions are polars-native. It is
    the remaining work, so it must still be reported rather than silently
    mishandled."""
    from goldenflow.config.schema import SplitSpec

    cfg = GoldenFlowConfig(
        transforms=_STRIP,
        splits=[SplitSpec(source="name", target=["a", "b"], method="split_name")],
    )
    assert _frame_level_blocked(cfg)


def test_frame_ops_run_without_polars_installed():
    """The whole point. Run in a SUBPROCESS with polars blocked at the meta
    path -- an in-process hook would leak into every later test."""
    body = textwrap.dedent(
        """
        import sys
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == "polars" or name.startswith("polars."):
                    raise ImportError("No module named 'polars'")
                return None
        sys.meta_path.insert(0, _Block())

        from goldenflow.config.schema import (
            DedupSpec, FilterSpec, GoldenFlowConfig, TransformSpec)
        from goldenflow.engine.columnar import transform_columns_public

        data = {"name": ["  Ann ", "BOB", "  Ann ", "Cy"],
                "email": ["A@X.COM", "b@x.com", "A@X.COM", None]}
        cfg = GoldenFlowConfig(
            transforms=[TransformSpec(column="name", ops=["strip"])],
            renames={"email": "mail"},
            filters=[FilterSpec(column="mail", condition="not_null")],
            dedup=DedupSpec(columns=["name"], keep="first"),
        )
        out = transform_columns_public(data, cfg).columns
        assert out["name"] == ["Ann", "BOB"], out
        assert out["mail"] == ["A@X.COM", "b@x.com"], out
        assert "polars" not in sys.modules, "the columnar path imported polars"
        print("FRAME OPS POLARS-FREE OK")
        """
    )
    import os

    env = dict(os.environ)
    root = Path(__file__).resolve().parents[3]
    existing = env.get("PYTHONPATH", "")
    extra = os.pathsep.join(str(p) for p in sorted((root / "python").iterdir()) if p.is_dir())
    env["PYTHONPATH"] = extra + (os.pathsep + existing if existing else "")

    proc = subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, env=env, timeout=300
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr[-2000:]
    assert "FRAME OPS POLARS-FREE OK" in proc.stdout


# -- ColumnarFrame unit contracts -------------------------------------------


def _frame() -> ColumnarFrame:
    return ColumnarFrame({"a": ["x", "y", "x", "z"], "b": [1, 2, 3, None]})


def test_unique_keeps_first_occurrence_in_input_order():
    assert _frame().unique(["a"], "first").native == {
        "a": ["x", "y", "z"], "b": [1, 2, None]
    }


def test_unique_keep_last_takes_the_later_row():
    assert _frame().unique(["a"], "last").native == {
        "a": ["y", "x", "z"], "b": [2, 3, None]
    }


def test_filter_cmp_excludes_nulls():
    """A null comparison is null in polars and `filter` drops null-mask rows, so
    nulls are excluded on both paths rather than compared."""
    assert _frame().filter_cmp("b", ">", 1).native["b"] == [2, 3]


def test_filter_cmp_on_mismatched_types_drops_rather_than_raises():
    f = ColumnarFrame({"a": ["x", 1, "z"]})
    assert f.filter_cmp("a", ">", "m").native["a"] == ["x", "z"]


def test_height_of_an_empty_frame_is_zero():
    assert ColumnarFrame({}).height == 0
