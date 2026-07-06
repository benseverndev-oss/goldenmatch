"""Fused columnar apply bridge — run a whole run of owned, string->string, no-arg
transforms over one column in a single native Arrow round-trip, instead of the
engine crossing the boundary once per transform.

``FUSABLE_KERNELS`` is the single Python-side source of truth for which transforms
the fused chain supports; it MUST mirror ``goldenflow_core::chain::Kernel`` (the
coverage guard in ``tests/transforms/test_fused_chain.py`` asserts they agree).
Opt-in via ``GOLDENFLOW_FUSED_APPLY``; native must also be enabled
(``GOLDENFLOW_NATIVE`` != ``0`` and the ``apply_chain_arrow`` symbol present).
"""
from __future__ import annotations

import os

import polars as pl

from goldenflow.core._native_loader import native_module

# Owned, no-arg, TOTAL (never-null) string->string kernels eligible for fusion.
# Mirror of goldenflow_core::chain::Kernel::ALL_NAMES. Option-returning
# (email_extract_domain, *_validate, *_mask), parameterized (truncate/pad), and
# multi-arity (split_*/merge_name) transforms are intentionally excluded.
FUSABLE_KERNELS: frozenset[str] = frozenset(
    {
        "strip",
        "lowercase",
        "uppercase",
        "title_case",
        "fix_mojibake",
        "collapse_whitespace",
        "normalize_quotes",
        "normalize_line_endings",
        "normalize_unicode",
        "remove_html_tags",
        "remove_urls",
        "remove_digits",
        "remove_punctuation",
        "remove_emojis",
        "extract_numbers",
        # email family (total string->string)
        "email_lowercase",
        "email_normalize",
        "email_canonical",
        # name-normalizer family (total string->string)
        "name_transliterate",
        "strip_titles",
        "strip_suffixes",
        "name_proper",
        "nickname_standardize",
        "name_initials",
        "strip_middle",
    }
)


def fused_enabled() -> bool:
    """The fused chain path is active by DEFAULT whenever the native fused kernel is
    available (measured byte-identical to the per-transform path, faster wall + ~20%
    lower peak RSS at scale). Opt-OUT via ``GOLDENFLOW_FUSED_APPLY=0`` (mirrors
    ``GOLDENFLOW_NATIVE``'s auto/0 semantics); it also stays off when native is
    forced off or the fused symbol isn't in the installed wheel (graceful fallback
    to per-transform, so a pre-0.12.0 goldenflow-native is unaffected)."""
    if os.environ.get("GOLDENFLOW_FUSED_APPLY", "").lower() in ("0", "false", "off"):
        return False
    if os.environ.get("GOLDENFLOW_NATIVE", "auto").lower() == "0":
        return False
    nm = native_module()
    return nm is not None and hasattr(nm, "apply_chain_arrow")


def apply_chain_native(
    series: pl.Series, kernel_names: list[str]
) -> tuple[pl.Series, list[int]] | None:
    """Apply ``kernel_names`` (all in ``FUSABLE_KERNELS``) in order over ``series``
    in one native pass. Returns ``(transformed_series, per_kernel_changed_counts)``,
    or ``None`` when the native path is unavailable (caller falls back to the
    per-transform path). The Arrow round-trip is zero-copy; Polars exports strings
    as LargeUtf8, which the kernel handles natively (i64 offsets)."""
    nm = native_module()
    if nm is None or not hasattr(nm, "apply_chain_arrow"):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    out_arrow, changed = nm.apply_chain_arrow(series.to_arrow(), list(kernel_names))
    return pl.from_arrow(out_arrow), [int(c) for c in changed]
