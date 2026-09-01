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


SPLIT_CASES = {
    "split_name": ("full", "split_name"),
    "split_name_reverse": ("rev", "split_name_reverse"),
    "split_address": ("addr", "split_address"),
}

SPLIT_DATA = {
    "full": ["  Ann Smith ", "Bob Jones", None, "Cher"],
    "rev": ["Smith, Ann", "Jones, Bob", None, "Cher"],
    "addr": ["12 High St, Leeds, LS1 1AA", "9 Low Rd, York, YO1 2BB", None, "nope"],
}


def _split_cfg(src, method):
    from goldenflow.config.schema import SplitSpec

    return GoldenFlowConfig(
        transforms=[TransformSpec(column=src, ops=["strip"])],
        splits=[SplitSpec(source=src, target=[], method=method)],
    )


@pytest.mark.parametrize("label", sorted(SPLIT_CASES))
def test_columnar_splits_match_the_polars_engine(label):
    """Both sides run the SAME Rust kernel, so this checks WIRING, not semantics.

    Worth being exact about, because an earlier version of this test claimed
    otherwise. When the columnar path ran a pure-Python split it was reasonable
    to call this a cross-implementation check; it never was an oracle, since
    under `Rust is the reference` a divergence would have condemned the Python
    side by definition. Now that both paths reach `apply_split`, what remains is
    that the columnar ingress/egress (`from_pylist` / `to_pylist`) puts the same
    values in the same columns as the polars ingress does. That is a real thing
    to protect and a much narrower one than "the split is correct".

    Split CORRECTNESS lives with the kernel, in Rust.
    """
    pl = pytest.importorskip("polars", reason="the reference engine is polars")
    import goldenflow

    src, method = SPLIT_CASES[label]
    cfg = _split_cfg(src, method)
    columnar = transform_columns_public(
        {k: list(v) for k, v in SPLIT_DATA.items()}, cfg
    ).columns

    res = goldenflow.transform_df(pl.DataFrame(SPLIT_DATA), config=cfg)
    pdf = res.df if hasattr(res, "df") else res
    polars_cols = {c: pdf[c].to_list() for c in pdf.columns}

    assert set(columnar) == set(polars_cols)
    for col in sorted(columnar):
        assert columnar[col] == polars_cols[col], col


def test_the_split_runs_on_the_kernel_not_on_python(monkeypatch):
    """The test with actual teeth, and the reason this file was reworked.

    The first implementation of `_apply_splits` looped in Python over
    `_split_name_py` while `goldenflow-native` -- a BASE dependency, always
    present -- sat there with `apply_split`. Output was correct, so every
    value-comparing test passed. Nothing failed; the path was just needlessly
    slow and off the arrow-native line.

    So assert the mechanism, not the values: `Column.apply_split` is CALLED.
    """
    from goldenflow.engine import columnar as C

    real = C.native_module()
    assert real is not None, "goldenflow-native is a base dep; it must be present"
    real_column = real.Column
    calls = []

    class _SpyColumn:
        # Present so `_split_inmem_ok`'s skew probe still sees the capability.
        apply_split = real_column.apply_split

        @staticmethod
        def from_pylist(values):
            inner = real_column.from_pylist(values)

            class _Proxy:
                def apply_split(self, ops):
                    calls.append(ops)
                    return inner.apply_split(ops)

                def __getattr__(self, name):
                    return getattr(inner, name)

            return _Proxy()

    class _SpyNative:
        Column = _SpyColumn

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(C, "native_module", lambda: _SpyNative())

    out = transform_columns_public(
        {k: list(v) for k, v in SPLIT_DATA.items()}, _split_cfg("full", "split_name")
    ).columns

    assert calls == [[("split_name", [])]], (
        "the columnar split did not reach Column.apply_split -- it is running in "
        "Python again"
    )
    assert out["first_name"] == ["Ann", "Bob", None, "Cher"]


def test_split_declines_when_the_kernel_is_unavailable(monkeypatch):
    """Skew or a missing kernel must DECLINE, never silently run Python.

    Declining routes to polars, which on a polars-free install raises an
    actionable ImportError. That is the loud failure; a slower Python path that
    quietly produces the right answer is the one worth preventing.
    """
    from goldenflow.engine import columnar as C

    monkeypatch.setattr(C, "native_module", lambda: None)
    assert C._frame_level_blocked(_split_cfg("full", "split_name"))


def test_an_unknown_split_method_still_falls_back():
    """Coverage is decided by the Rust probe `columnar_split_ready`, so a method
    the kernel does not know declines here without any Python-side list of
    supported methods to keep in sync."""
    cfg = _split_cfg("name", "not_a_real_method")
    assert _frame_level_blocked(cfg)


def test_known_splits_no_longer_force_the_polars_engine():
    for label, (src, method) in SPLIT_CASES.items():
        assert not _frame_level_blocked(_split_cfg(src, method)), label


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
