"""Fused columnar apply (Pillar-1) — guard + parity.

The fused path (``GOLDENFLOW_FUSED_APPLY``) runs a maximal run of owned,
string->string, no-arg transforms over a column in ONE native Arrow round-trip.
It must be byte-identical to the per-transform path — same output frame AND same
audit manifest — so the flag is transparent except for speed.
"""
from __future__ import annotations

import goldenflow  # noqa: F401 -- import-time transform registration
import polars as pl
import pytest
from goldenflow.core._native_loader import native_module
from goldenflow.transforms import get_transform, registry
from goldenflow.transforms._chain import FUSABLE_KERNELS


def test_fusable_kernels_registered_and_single_col_mode() -> None:
    """Every fusable kernel is a registered, single-column transform (mode 'expr'
    or 'series', never 'dataframe'). The fused per-step sample replay dispatches on
    mode; a dataframe-mode (multi-column) kernel would break it — this guard blocks
    that and any unregistered name."""
    reg = set(registry())
    for name in sorted(FUSABLE_KERNELS):
        assert name in reg, f"{name} in FUSABLE_KERNELS but not registered"
        info = get_transform(name)
        assert info is not None and info.mode in ("expr", "series"), (
            f"{name} must be mode 'expr'/'series' for the fused sample replay, got "
            f"{None if info is None else info.mode}"
        )


def test_fusable_matches_native_kernel_table() -> None:
    """Python FUSABLE_KERNELS must mirror goldenflow_core::chain::Kernel (native
    ``fusable_kernel_names``): the host must never send a name the kernel can't
    fuse, nor silently under-fuse a kernel the kernel supports."""
    nm = native_module()
    if nm is None or not hasattr(nm, "fusable_kernel_names"):
        pytest.skip("native chain kernel not built")
    assert set(nm.fusable_kernel_names()) == set(FUSABLE_KERNELS)


def _cfg(column: str, ops: list[str]):
    from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

    return GoldenFlowConfig(transforms=[TransformSpec(column=column, ops=ops)])


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


@pytest.mark.parametrize(
    "ops",
    [
        ["strip", "lowercase"],
        ["strip", "lowercase", "collapse_whitespace", "remove_punctuation"],
        ["remove_html_tags", "remove_urls", "strip", "collapse_whitespace"],
        ["normalize_unicode", "lowercase", "remove_digits"],
        # widened families (email / name normalizers / extract_numbers)
        ["strip", "lowercase", "email_normalize", "email_canonical"],
        ["name_transliterate", "name_proper", "strip_titles", "strip_suffixes"],
        ["strip", "name_proper", "strip_middle", "name_initials"],
        ["strip", "extract_numbers"],
    ],
)
def test_fused_equals_per_transform(monkeypatch, ops) -> None:
    """With native available, the fused path is byte-identical to the per-transform
    path — same output frame AND same manifest (records, affected counts, samples)."""
    nm = native_module()
    if nm is None or not hasattr(nm, "apply_chain_arrow"):
        pytest.skip("native chain kernel not built")

    from goldenflow import transform_df

    df = pl.DataFrame(
        {
            "name": [
                "  <b>John</b>  SMITH!  http://x.com/y ",
                "o'BRIEN, jr.  123",
                None,
                "  a   b  “Q” ",
                "",
                "café  éé  #7",
            ]
        }
    )
    cfg = _cfg("name", ops)

    monkeypatch.setenv("GOLDENFLOW_FUSED_APPLY", "0")  # opt-OUT -> per-transform
    per_op = transform_df(df, config=cfg)

    monkeypatch.setenv("GOLDENFLOW_FUSED_APPLY", "1")
    fused = transform_df(df, config=cfg)

    assert fused.df.equals(per_op.df), "fused output frame diverged"
    assert _manifest_rows(fused) == _manifest_rows(per_op), "fused manifest diverged"


def test_opt_out_is_the_per_transform_path(monkeypatch) -> None:
    """Opt-out: GOLDENFLOW_FUSED_APPLY=0 forces the per-transform path (and it's the
    only path anyway with no native). Output + audit records unchanged."""
    from goldenflow import transform_df

    monkeypatch.setenv("GOLDENFLOW_FUSED_APPLY", "0")
    df = pl.DataFrame({"name": ["  John  ", "MARY  ", None]})
    out = transform_df(df, config=_cfg("name", ["strip", "lowercase"]))
    assert out.df["name"].to_list() == ["john", "mary", None]
    # two ops -> two audit records, in order.
    assert [r.transform for r in out.manifest.records] == ["strip", "lowercase"]


def test_default_is_fused_when_native_available(monkeypatch) -> None:
    """The default (flag unset) now fuses whenever the native kernel is present —
    opt-OUT semantics. With native absent, fused_enabled() is False (graceful)."""
    from goldenflow.core._native_loader import native_module
    from goldenflow.transforms._chain import fused_enabled

    monkeypatch.delenv("GOLDENFLOW_FUSED_APPLY", raising=False)
    monkeypatch.delenv("GOLDENFLOW_NATIVE", raising=False)
    nm = native_module()
    expected = nm is not None and hasattr(nm, "apply_chain_arrow")
    assert fused_enabled() is expected
    # explicit off always wins
    monkeypatch.setenv("GOLDENFLOW_FUSED_APPLY", "0")
    assert fused_enabled() is False
