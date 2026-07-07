"""Fused columnar apply bridge — run a whole run of owned string->string transforms
over one column in a single native Arrow round-trip, instead of the engine crossing
the boundary once per transform.

``FUSABLE_KERNELS`` (no-arg) + ``FUSABLE_PARAM_KERNELS`` (parameterized) are the
Python-side source of truth for which transforms the fused chain supports; together
they MUST mirror ``goldenflow_core::chain`` (``ALL_NAMES`` + ``PARAM_NAMES``) — the
coverage guard in ``tests/transforms/test_fused_chain.py`` asserts they agree.

Two native symbols back this: ``apply_chain_arrow`` (no-arg, since goldenflow-native
0.12.0) and ``apply_chain_ops_arrow`` (superset, adds the parameterized ops, since
0.13.0). The parameterized ops only fuse when the ops symbol is present, so a 0.12.0
wheel still fuses the no-arg families and only the param ops break a run.
"""
from __future__ import annotations

import os

import polars as pl

from goldenflow.core._native_loader import native_module

# Owned, no-arg, TOTAL (never-null) string->string kernels eligible for fusion.
# Mirror of goldenflow_core::chain::Kernel::ALL_NAMES. Option-returning
# (email_extract_domain, *_validate, *_mask) and multi-arity (split_*/merge_name)
# transforms are intentionally excluded.
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

# Parameterized (total string->string) kernels — need the ``apply_chain_ops_arrow``
# symbol (goldenflow-native >= 0.13.0). Mirror of Kernel::PARAM_NAMES.
FUSABLE_PARAM_KERNELS: frozenset[str] = frozenset({"truncate", "pad_left", "pad_right"})

# Owned f64->f64 numeric kernels eligible for the SEPARATE numeric fused chain
# (a Float64 column, not a string one). Mirror of
# goldenflow_core::chain::NumericKernel::ALL_NAMES; ``round``/``clamp`` carry params
# (FUSABLE_F64_PARAM_KERNELS, mirror of NumericKernel::PARAM_NAMES). Need the
# ``apply_chain_f64_arrow`` symbol (goldenflow-native >= 0.13.0).
FUSABLE_F64_KERNELS: frozenset[str] = frozenset(
    {"round", "clamp", "abs_value", "fill_zero"}
)
FUSABLE_F64_PARAM_KERNELS: frozenset[str] = frozenset({"round", "clamp"})

# Every parameterized fusable name (string + numeric), so ``is_fusable`` treats an
# op-with-params as fusable when the op is one that legitimately carries args.
_PARAM_KERNELS: frozenset[str] = FUSABLE_PARAM_KERNELS | FUSABLE_F64_PARAM_KERNELS


def _native_if_on():
    """The native module if fusion is not opted out and native is not forced off,
    else ``None``."""
    if os.environ.get("GOLDENFLOW_FUSED_APPLY", "").lower() in ("0", "false", "off"):
        return None
    if os.environ.get("GOLDENFLOW_NATIVE", "auto").lower() == "0":
        return None
    return native_module()


def fused_enabled() -> bool:
    """The fused chain path is active by DEFAULT whenever the native fused kernel is
    available (measured byte-identical to the per-transform path, faster wall + ~20%
    lower peak RSS at scale). Opt-OUT via ``GOLDENFLOW_FUSED_APPLY=0`` (mirrors
    ``GOLDENFLOW_NATIVE``); off when native is forced off or the fused symbol isn't
    in the installed wheel (graceful fallback, so a pre-0.12.0 wheel is unaffected)."""
    nm = _native_if_on()
    return nm is not None and hasattr(nm, "apply_chain_arrow")


def fusable_names() -> frozenset[str]:
    """The transform names the engine may fuse, given the AVAILABLE native symbol:
    the parameterized ops only join a run when ``apply_chain_ops_arrow`` is present
    (0.13.0+); a 0.12.0 wheel fuses the no-arg families only."""
    nm = _native_if_on()
    if nm is None:
        return frozenset()
    if hasattr(nm, "apply_chain_ops_arrow"):
        return FUSABLE_KERNELS | FUSABLE_PARAM_KERNELS
    if hasattr(nm, "apply_chain_arrow"):
        return FUSABLE_KERNELS
    return frozenset()


def fusable_f64_names() -> frozenset[str]:
    """The numeric (f64) transform names the engine may fuse, given the available
    native symbol: ``FUSABLE_F64_KERNELS`` when ``apply_chain_f64_arrow`` is present
    (goldenflow-native 0.13.0+), else empty (a 0.12.0 wheel has no f64 chain)."""
    nm = _native_if_on()
    if nm is None or not hasattr(nm, "apply_chain_f64_arrow"):
        return frozenset()
    return FUSABLE_F64_KERNELS


def is_fusable(name: str, params: list[str], available: frozenset[str]) -> bool:
    """An op fuses iff its name is in the symbol-available set AND either it's a
    parameterized kernel (params are its args) or a no-arg kernel with no params.
    ``available`` is the dtype-appropriate set (string vs f64) the caller passes."""
    if name not in available:
        return False
    if name in _PARAM_KERNELS:
        return True
    return not params


def apply_chain_native(
    series: pl.Series, ops: list[tuple[str, list[str]]]
) -> tuple[pl.Series, list[int]] | None:
    """Apply ``ops`` (each ``(name, params)``, all fusable for the available symbol)
    in order over ``series`` in one native pass, dispatching on the series dtype: a
    Float64 column routes to the numeric ``apply_chain_f64_arrow`` chain; a string
    column prefers ``apply_chain_ops_arrow`` (parameterized) and falls back to
    ``apply_chain_arrow`` when every op is no-arg. Returns
    ``(transformed_series, per_kernel_changed_counts)`` or ``None`` if the native
    path is unavailable. Zero-copy; Polars exports LargeUtf8 / Float64 natively."""
    nm = native_module()
    if nm is None:
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    if series.dtype == pl.Float64:
        if not hasattr(nm, "apply_chain_f64_arrow"):
            return None
        out_arrow, changed = nm.apply_chain_f64_arrow(
            series.to_arrow(), [(name, list(params)) for name, params in ops]
        )
    elif hasattr(nm, "apply_chain_ops_arrow"):
        out_arrow, changed = nm.apply_chain_ops_arrow(
            series.to_arrow(), [(name, list(params)) for name, params in ops]
        )
    elif hasattr(nm, "apply_chain_arrow") and all(not params for _, params in ops):
        out_arrow, changed = nm.apply_chain_arrow(
            series.to_arrow(), [name for name, _ in ops]
        )
    else:
        return None
    return pl.from_arrow(out_arrow), [int(c) for c in changed]
