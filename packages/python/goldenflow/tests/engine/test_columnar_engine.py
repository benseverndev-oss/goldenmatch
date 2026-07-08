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
        # Phase 3 wave 1: phonetic joins the fused chain -> columnar-ready
        [("name", ["strip", "lowercase", "soundex"])],
        [("name", ["strip", "double_metaphone_primary"])],
        # Phase 3 wave 2: nullable chain (URL/company/email Option-returning),
        # incl. runs that MIX total + nullable kernels
        [("email", ["strip", "lowercase", "email_extract_domain"])],
        [("email", ["strip", "email_mask"])],
        [("name", ["strip", "company_normalize"])],
        [("name", ["strip", "url_normalize", "url_strip_www"])],
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


@pytest.mark.parametrize(
    "ops,data",
    [
        (["currency_strip"], ["  $1,234.50 ", "$0.5", "10", "", None, "bad"]),
        (["strip", "currency_strip"], ["  $1,234.50 ", "$0.5", "", None, "bad"]),
        (["currency_strip", "round:1"], ["$1,234.56", "$0.5", "", None]),
        (["percentage_normalize"], ["50%", "12.5%", "", None, "3"]),
        (["to_integer"], ["  42 ", "1,000", "-5", "", None, "3.9"]),
        (["to_integer", "abs_value"], ["42", "-5", "", None]),  # Int64 -> Float64
        (["roman_to_int"], ["IV", "XII", "", None, "bad"]),
        (["currency_strip"], [1.5, 2.0, None, 3.25]),  # already-numeric input
    ],
)
def test_columnar_numeric_equals_polars(monkeypatch, ops, data) -> None:
    """Phase 3 wave 3d: numeric configs run on the IN-MEMORY Column path too — the
    result egresses as a real Int64/Float64 column (compared by value + dtype),
    byte-identical to the Polars engine incl. the manifest."""
    nm = native_module()
    if nm is None or not hasattr(nm, "columnar_numeric_ready") or not hasattr(
        getattr(nm, "Column", object), "apply_numeric"
    ):
        pytest.skip("native in-memory numeric not built (pre-0.23 wheel)")
    df = pl.DataFrame({"x": data, "keep": list(range(len(data)))})
    cfg = _cfg([("x", ops)])
    assert columnar.config_is_columnar_ready(cfg)

    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    polars_out = transform_df(df, config=cfg)

    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    columnar_out = transform_df(df, config=cfg)

    assert columnar_out.df.equals(polars_out.df), "numeric in-memory frame diverged"
    assert columnar_out.df["x"].dtype == polars_out.df["x"].dtype
    assert _manifest_rows(columnar_out) == _manifest_rows(polars_out)


@pytest.mark.parametrize(
    "ops,col,data",
    [
        (["split_name"], "name", ["John Smith", "Mary Jane Doe", "Cher", None, "", "o'Brien"]),
        (["strip", "split_name"], "name", ["  John Smith  ", "Mary Doe", None, ""]),
        (["split_name_reverse"], "name", ["Smith, John", "Doe, Mary", None, ""]),
        (["split_address"], "addr", ["123 Main St, Springfield, IL 62704", "5 Oak Ave", None, ""]),
        (["strip", "lowercase", "split_address"], "addr", ["  123 Main St, Reno, NV 89501 ", None]),
    ],
)
def test_columnar_split_equals_polars(monkeypatch, ops, col, data) -> None:
    """Phase 3 wave 3e: multi-output splits (split_name/_reverse/split_address) run on
    the in-memory Column path — the source column keeps its value and the fixed-name
    output columns are appended, byte-identical to the Polars engine (frame + manifest)."""
    nm = native_module()
    if nm is None or not hasattr(nm, "columnar_split_ready") or not hasattr(
        getattr(nm, "Column", object), "apply_split"
    ):
        pytest.skip("native split not built (pre-0.24 wheel)")
    df = pl.DataFrame({col: data, "keep": list(range(len(data)))})
    cfg = _cfg([(col, ops)])
    assert columnar.config_is_columnar_ready(cfg)

    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    polars_out = transform_df(df, config=cfg)

    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    columnar_out = transform_df(df, config=cfg)

    assert columnar_out.df.columns == polars_out.df.columns, "split output schema diverged"
    assert columnar_out.df.equals(polars_out.df), "split output frame diverged"
    assert _manifest_rows(columnar_out) == _manifest_rows(polars_out)


def test_columnar_declines_unsupported_config(monkeypatch) -> None:
    """A config with a data-dependent op (category_auto_correct) or a frame-level op
    is NOT columnar-ready; it falls through to the Polars engine (still correct)."""
    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    # category_auto_correct is data-dependent (whole-column frequency map), not a
    # scalar or fused kernel -> declines (deliberately excluded from the columnar path)
    assert not columnar.config_is_columnar_ready(_cfg([("name", ["strip", "category_auto_correct"])]))
    # a rename is a frame-level op -> declines
    cfg = GoldenFlowConfig(transforms=[TransformSpec(column="name", ops=["strip"])], renames={"name": "n"})
    assert not columnar.config_is_columnar_ready(cfg)
    # but it still produces correct output via the Polars fallback
    out = transform_df(SAMPLE, config=cfg)
    assert out.df["n"].to_list()[0] == "<b>John</b>  SMITH!"  # strip applied, renamed
