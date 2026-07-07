"""Columnar (Polars-free) engine — Phase 1 parity gate.

The ``GOLDENFLOW_ENGINE=columnar`` path executes owned string transforms via the
native arrow-free chain with NO Polars. It must be byte-identical to the Polars
engine — same output frame AND same audit manifest — for every config it accepts.
"""
from __future__ import annotations

import goldenflow  # noqa: F401 -- import-time transform registration
import polars as pl
import pytest
from goldenflow import transform_df
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar


def _manifest_rows(result) -> list[tuple]:
    return [
        (
            r.column,
            r.transform,
            r.affected_rows,
            tuple(r.sample_before or []),
            tuple(r.sample_after or []),
        )
        for r in result.manifest.records
    ]


def _cfg(specs: list[tuple[str, list[str]]]) -> GoldenFlowConfig:
    return GoldenFlowConfig(transforms=[TransformSpec(column=c, ops=o) for c, o in specs])


SAMPLE = pl.DataFrame({
    "name": ["  <b>John</b>  SMITH!  ", "o'BRIEN, jr.  123", None, "café  éé  #7", "", "  a   b  "],
    "email": ["  JOHN@X.COM ", "MARY@Y.com  ", None, "b@z.io", "", "q@a.com"],
    "keep_int": [1, 2, 3, 4, 5, 6],
})


def test_native_list_binding_present() -> None:
    nm = native_module()
    if nm is None or not hasattr(nm, "apply_chain_str_list"):
        pytest.skip("native arrow-free list chain not built (pre-columnar wheel)")
    assert hasattr(nm, "apply_chain_str_list")


@pytest.mark.parametrize(
    "specs",
    [
        [("name", ["strip", "lowercase"])],
        [("name", ["strip", "lowercase", "collapse_whitespace", "remove_punctuation"])],
        [("name", ["remove_html_tags", "strip", "collapse_whitespace"]), ("email", ["strip", "lowercase"])],
        [("name", ["strip", "title_case", "name_proper"])],
        [("email", ["strip", "lowercase", "email_normalize"])],
        [("name", ["strip", "truncate:6"])],
    ],
)
def test_columnar_equals_polars(monkeypatch, specs) -> None:
    nm = native_module()
    if nm is None or not hasattr(nm, "apply_chain_str_list"):
        pytest.skip("native arrow-free list chain not built")

    cfg = _cfg(specs)
    # config must actually be accepted by the columnar engine (else this proves nothing)
    assert columnar.config_is_columnar_ready(cfg)

    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)  # Polars engine
    polars_out = transform_df(SAMPLE, config=cfg)

    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")  # Polars-free path
    columnar_out = transform_df(SAMPLE, config=cfg)

    assert columnar_out.df.equals(polars_out.df), "columnar output frame diverged"
    assert _manifest_rows(columnar_out) == _manifest_rows(polars_out), "columnar manifest diverged"
    # untransformed column + its dtype are preserved
    assert columnar_out.df["keep_int"].to_list() == [1, 2, 3, 4, 5, 6]


def test_columnar_declines_unsupported_config(monkeypatch) -> None:
    """A config with a non-owned-string op (phone) or a frame-level op is NOT
    columnar-ready; it falls through to the Polars engine (still correct)."""
    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    # phone_e164 is not an owned string kernel -> declines
    assert not columnar.config_is_columnar_ready(_cfg([("name", ["strip", "phone_e164"])]))
    # a rename is a frame-level op -> declines
    cfg = GoldenFlowConfig(transforms=[TransformSpec(column="name", ops=["strip"])], renames={"name": "n"})
    assert not columnar.config_is_columnar_ready(cfg)
    # but it still produces correct output via the Polars fallback
    out = transform_df(SAMPLE, config=_cfg([("name", ["strip", "phone_e164"])]))
    assert out.df["name"].to_list()[0] == "<b>John</b>  SMITH!"  # strip applied
