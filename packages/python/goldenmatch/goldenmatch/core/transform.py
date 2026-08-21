"""GoldenFlow integration -- data transformation before matching."""
from __future__ import annotations

import logging

from goldenmatch._polars_lazy import pl

logger = logging.getLogger(__name__)

# One-time guard for the arrow-lane transform polars-absent warning.
_TRANSFORM_POLARS_WARNED = False


def _warn_transform_needs_polars_once() -> None:
    """Warn (once) that GoldenFlow transforms were skipped because this frame's
    auto-detected config is NOT covered by GoldenFlow's polars-free columnar
    engine, and polars is absent to serve as the fallback.

    NOTE the narrow scope (#2430). The arrow lane routes through
    ``goldenflow.transform`` (polars-free) and standardizes normally for the
    common shapes -- string / int / float / bool / date columns all run with
    polars never imported. Only an UNCOVERED config reaches here. The message
    used to claim the whole transform engine was polars-native, which was false
    and made this look unfixable.

    TWO things make a config uncovered, and the distinction matters when
    diagnosing:

    1. An ALL-NULL column, which makes zero-config auto-detect decline.
    2. The ``goldenflow-native`` kernel is missing. GoldenFlow's columnar engine
       has no pure-Python core -- ``transform_columns_public`` gates BOTH its
       zero-config and explicit-config branches on ``native_columns_ready``, so
       with the kernel absent EVERY config declines to the polars engine.
       ``goldenflow-native`` is a BASE dependency of goldenflow, so a normal
       install has it; CI lanes that strip it (``--no-install-package
       goldenflow-native``, to skip the maturin build) do not."""
    global _TRANSFORM_POLARS_WARNED
    if _TRANSFORM_POLARS_WARNED:
        return
    _TRANSFORM_POLARS_WARNED = True
    logger.warning(
        "GoldenFlow transforms skipped for this frame: the auto-detected config "
        "is not covered by GoldenFlow's polars-free columnar engine (an "
        "entirely-null column, or a missing goldenflow-native kernel, are the "
        "usual causes), and polars is not installed to fall back to. "
        "Standardization is not applied. Install goldenmatch[polars] to cover "
        "the remaining cases."
    )


def _goldenflow_available() -> bool:
    """Check if goldenflow is installed."""
    try:
        import goldenflow  # noqa: F401
        return True
    except ImportError as e:
        logger.debug("goldenflow not available: %s", e)
        return False


def _do_transform(df: pl.DataFrame):
    """Call goldenflow.transform_df (the POLARS engine). Separated for testability."""
    from goldenflow import transform_df
    return transform_df(df)


def _do_transform_columnar(columns: dict, config=None):
    """Call goldenflow.transform -- the POLARS-FREE columnar engine (#2430).

    Takes ``dict[str, list]``, returns a ``ColumnarResult`` (``.columns`` dict +
    ``.manifest``). Raises ``ImportError`` when the config is uncovered and
    polars is absent to fall back to. Separated for testability, mirroring
    ``_do_transform``."""
    from goldenflow import transform
    return transform(columns, config)


def _columnar_result_to_arrow(columns: dict, source, source_columns: dict):
    """Rebuild a ``pa.Table`` from the columnar engine's ``dict[str, list]``.

    Preserves the SOURCE arrow type for any column GoldenFlow left unchanged,
    instead of letting ``pa.table`` re-infer it. Without this an untouched
    ``int32`` column silently widens to ``int64`` (measured) -- a schema change
    the polars lane does not make. Changed columns are inferred, which matches
    the polars lane's values (verified: identical on string/int/float/bool/date
    incl. date32 -> ISO string, which BOTH lanes perform).

    ``source_columns`` is the ``to_pydict()`` we already built to feed the
    engine; comparing against it avoids re-materializing every source column
    just to detect which ones changed."""
    import pyarrow as pa  # noqa: PLC0415

    arrays, names = [], []
    for name, values in columns.items():
        names.append(name)
        src = source.column(name) if name in source.column_names else None
        if src is not None:
            if source_columns.get(name) == values:
                arrays.append(src)              # untouched -> keep exact type
                continue
            try:
                arrays.append(pa.array(values, type=src.type))
                continue
            except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
                # Transform changed the value DOMAIN (e.g. date32 -> ISO string);
                # fall through and let pyarrow infer, as the polars lane does.
                pass
        arrays.append(pa.array(values))
    return pa.Table.from_arrays(arrays, names=names)


def run_transform(
    df,  # pl.DataFrame | pa.Table (A2: lane preserved; bridge at _do_transform)
    config=None,
    *,
    strict: bool = False,
) -> tuple:
    """Run GoldenFlow transform if available.

    Returns (transformed_df, list_of_fixes) matching autofix format.
    Falls back gracefully if goldenflow is not installed.

    Parameters
    ----------
    strict : bool
        If True, re-raise exceptions instead of silently returning
        unmodified data. Use from MCP/A2A handlers where callers
        explicitly requested transforms.
    """
    if not _goldenflow_available():
        if config is not None and getattr(config, "enabled", True):
            logger.warning(
                "GoldenFlow transforms configured but goldenflow is not installed. "
                "Install with: pip install goldenmatch[transform]"
            )
        return df, []

    # Parse config
    enabled = True
    mode = "announced"

    if config is not None:
        mode = getattr(config, "mode", "announced")
        enabled = getattr(config, "enabled", True)

    if not enabled or mode == "disabled":
        return df, []

    # Unified column exclusions (see spec
    # docs/superpowers/specs/2026-05-21-unified-column-exclusions-design.md):
    # honor the runtime ContextVar populated by dedupe_df / match_df. Excluded
    # columns are STRIPPED before _do_transform sees them and re-attached
    # unchanged after, so a record_hash column with exclude_columns=
    # ['record_hash'] passes through verbatim even when GoldenFlow has a
    # lowercase/strip rule for it. Column order is preserved.
    # A2 (arrow-native endgame): the adapter is dual-rep. Exclusion strip /
    # re-attach runs on the CALLER'S lane via the seam.
    # UPDATED #2430: this used to say goldenflow's polars-free columnar
    # `transform` "needs an explicit covered config + arrow auto-detect". That is
    # NOT true for the shapes that matter -- MEASURED, zero-config auto-detect
    # (config=None) runs polars-free on string / int / float / bool / date
    # columns, standardizing dates to ISO and phones to E.164 with polars never
    # imported. It is now the arrow lane's FALLBACK (`_do_transform_columnar`)
    # when the polars bridge is unavailable. The polars engine stays PREFERRED
    # where it works -- it is ~2.6x faster and covers more configs (see the
    # measurement at the engine-selection ladder below). Residual uncovered
    # cases: an all-null column, and a missing `goldenflow-native` kernel (the
    # columnar engine has no pure-Python core) -- both still decline. See
    # `_warn_transform_needs_polars_once` for the diagnosis notes.
    import pyarrow as _pa  # noqa: PLC0415

    from goldenmatch.core.frame import to_frame as _tf_a2

    _in_frame = _tf_a2(df)
    # Arrow-lane discriminator via `isinstance(df, pa.Table)` (pyarrow is always
    # present) -- NEVER `isinstance(df, pl.DataFrame)`, which imports polars just
    # to type-check on the arrow lane. A non-arrow frame is the polars lane.
    _is_arrow_in = isinstance(df, _pa.Table)
    _is_pl_in = not _is_arrow_in

    excluded_set: set[str] = set()
    try:
        from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
        runtime_excl = _RUNTIME_EXCLUDE_COLUMNS.get()
        if runtime_excl:
            excluded_set = {c for c in runtime_excl if c in _in_frame.columns}
    except Exception:
        # ContextVar lookup is best-effort; pipeline never blocks on it.
        excluded_set = set()

    original_columns = list(_in_frame.columns)
    preserved_frame = None
    if excluded_set:
        preserved_frame = _in_frame.select(list(excluded_set))
        _in_frame = _in_frame.drop(list(excluded_set))
        if len(preserved_frame.columns) > 0:
            logger.debug(
                "GoldenFlow: %d column(s) skipped via exclude_columns: %s",
                len(excluded_set), sorted(excluded_set),
            )

    def _restore(frame):
        if preserved_frame is None or not preserved_frame.columns:
            return frame
        out = frame
        for c in preserved_frame.columns:
            out = out.with_column(c, preserved_frame.column(c))
        return out.select(original_columns)

    # GOLDENMATCH_TRANSFORM_DEBUG=1 prints the arrow-lane bridge split
    # (pl.from_arrow / _do_transform / to_arrow) to size how much of
    # pipeline_prep_transform is the polars round-trip vs the actual transform
    # compute. Off by default, zero cost, output-invariant. Same instrument
    # pattern as GOLDENMATCH_BUCKET_DEBUG.
    import os as _os  # noqa: PLC0415
    _tdbg = _os.environ.get("GOLDENMATCH_TRANSFORM_DEBUG", "") not in ("", "0", "false", "False")
    _t_bridge_in = _t_do = _t_bridge_out = 0.0
    if _tdbg:
        import time as _time  # noqa: PLC0415

    # #2430: on the ARROW lane, fall back to GoldenFlow's POLARS-FREE columnar
    # engine (`goldenflow.transform`) when the polars bridge is unavailable.
    #
    # Before this, the arrow lane ONLY bridged through `pl.from_arrow`, so on a
    # polars-free install every arrow-lane run raised ImportError and SILENTLY
    # skipped standardization -- column profiling then saw unstandardized values
    # while the warning implied nothing could be done short of installing polars.
    # That premise was stale: `transform_df` is polars-native, but module-level
    # `goldenflow.transform` is polars-free and (measured) its zero-config
    # auto-detect covers string/int/float/bool/date columns, standardizing dates
    # to ISO and phones to E.164 with polars never imported.
    #
    # The polars engine is PREFERRED, not replaced. Measured on a 200K x 5 arrow
    # frame with polars present: polars bridge 0.70s / 314 MB vs columnar
    # 1.80s / 531 MB (2.6x slower, +69% RSS) -- `to_pydict()` materializes Python
    # lists where `pl.from_arrow` is near-zero-copy. The polars engine also covers
    # strictly MORE configs. So preferring it means installs that work today are
    # byte-identical AND unregressed; only the polars-free install changes, from
    # "silently skipped" to "standardized".
    _engine = "polars"
    try:
        if _tdbg:
            _t0 = _time.perf_counter()
        try:
            _bridge_in = (
                _in_frame.native if _is_pl_in else pl.from_arrow(_in_frame.native)  # polars-lane: polars engine PREFERRED (measured 2.6x faster / -69% RSS vs columnar, covers more configs); polars-free installs take the columnar fallback below
            )
            if _tdbg:
                _t_bridge_in = _time.perf_counter() - _t0
                _t0 = _time.perf_counter()
            result = _do_transform(_bridge_in)
        except ImportError:
            if _is_pl_in:
                raise          # polars lane genuinely cannot proceed
            _engine = "columnar"
            _bridge_in = _in_frame.native.to_pydict()
            if _tdbg:
                _t_bridge_in = _time.perf_counter() - _t0
                _t0 = _time.perf_counter()
            result = _do_transform_columnar(_bridge_in)
        if _tdbg:
            _t_do = _time.perf_counter() - _t0
    except ImportError:
        # Now a NARROW fallback: BOTH engines are unavailable -- polars is absent
        # AND the columnar engine declined an UNCOVERED config (known trigger: an
        # all-null column). Degrade to no-transform rather than crashing.
        _warn_transform_needs_polars_once()
        if strict:
            raise
        return _restore(_in_frame).native, []
    except Exception:
        logger.warning("GoldenFlow: transform failed, skipping", exc_info=True)
        if strict:
            raise
        # Restore preserved columns to the input frame before returning.
        return _restore(_in_frame).native, []

    # Re-attach preserved columns and restore order on the caller's lane.
    # The two ENGINES return different result shapes -- the polars
    # `TransformResult` carries `.df`, the columnar `ColumnarResult` carries
    # `.columns` (dict[str, list]) -- so branch on `_engine`, NOT on the lane.
    def _result_native():
        if _engine == "columnar":
            return _columnar_result_to_arrow(
                result.columns, _in_frame.native, _bridge_in,
            )
        return result.df if _is_pl_in else result.df.to_arrow()

    if _tdbg:
        if _is_pl_in:
            _res_frame = _tf_a2(result.df)
        else:
            _t0 = _time.perf_counter()
            _res_native = _result_native()
            _t_bridge_out = _time.perf_counter() - _t0
            _res_frame = _tf_a2(_res_native)
        print(
            f"[transform][DEBUG] lane={'polars' if _is_pl_in else 'arrow'} "
            f"engine={_engine} rows={_in_frame.height}: "
            f"bridge_in={_t_bridge_in:.3f}s  "
            f"_do_transform={_t_do:.3f}s  "
            f"bridge_out={_t_bridge_out:.3f}s  "
            f"bridge_total={_t_bridge_in + _t_bridge_out:.3f}s "
            f"(GOLDENMATCH_TRANSFORM_DEBUG=0 to silence)",
            flush=True,
        )
    else:
        _res_frame = _tf_a2(_result_native())
    _res_frame = _restore(_res_frame)

    # Convert manifest to autofix-compatible format
    fixes = []
    for record in result.manifest.records:
        fixes.append({
            "fix": f"goldenflow:{record.transform}",
            "column": record.column,
            "rows_affected": record.affected_rows,
            "detail": (
                f"{record.transform}: {record.affected_rows} rows"
                + (f" (e.g., {record.sample_before[0]} -> {record.sample_after[0]})"
                   if record.sample_before and record.sample_after else "")
            ),
        })

    if mode == "announced" and fixes:
        fix_types = set(record.transform for record in result.manifest.records)
        logger.info(
            "GoldenFlow: %d transforms applied (%s)",
            len(fixes), ", ".join(sorted(fix_types)),
        )
    elif mode == "announced":
        logger.info("GoldenFlow: no transforms needed")

    return _res_frame.native, fixes


def build_transform(column: str, op: str):
    """Back-compat shim. Returns a closure equivalent to apply_plan(df, TransformPlan(column, op)).

    New code should construct `TransformPlan` directly and call `apply_plan`.
    This shim exists so callers that still take a callable continue working.
    """
    from goldenmatch.distributed.transforms import TransformPlan, apply_plan

    plan = TransformPlan(column=column, op=op)
    return lambda df: apply_plan(df, plan)
