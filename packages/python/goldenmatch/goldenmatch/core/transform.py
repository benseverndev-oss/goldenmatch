"""GoldenFlow integration -- data transformation before matching."""
from __future__ import annotations

import logging

import polars as pl

logger = logging.getLogger(__name__)


def _goldenflow_available() -> bool:
    """Check if goldenflow is installed."""
    try:
        import goldenflow  # noqa: F401
        return True
    except ImportError as e:
        logger.debug("goldenflow not available: %s", e)
        return False


def _do_transform(df: pl.DataFrame):
    """Call goldenflow.transform_df. Separated for testability."""
    from goldenflow import transform_df
    return transform_df(df)


def run_transform(
    df: pl.DataFrame,
    config=None,
    *,
    strict: bool = False,
) -> tuple[pl.DataFrame, list[dict]]:
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
    excluded_set: set[str] = set()
    try:
        from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
        runtime_excl = _RUNTIME_EXCLUDE_COLUMNS.get()
        if runtime_excl:
            excluded_set = {c for c in runtime_excl if c in df.columns}
    except Exception:
        # ContextVar lookup is best-effort; pipeline never blocks on it.
        excluded_set = set()

    original_columns = df.columns
    preserved_df: pl.DataFrame | None = None
    if excluded_set:
        preserved_df = df.select(list(excluded_set))
        df = df.drop(list(excluded_set))
        if preserved_df.width > 0:
            logger.debug(
                "GoldenFlow: %d column(s) skipped via exclude_columns: %s",
                len(excluded_set), sorted(excluded_set),
            )

    try:
        result = _do_transform(df)
    except Exception:
        logger.warning("GoldenFlow: transform failed, skipping", exc_info=True)
        if strict:
            raise
        # Restore preserved columns to the input df before returning.
        if preserved_df is not None and preserved_df.width > 0:
            df = df.hstack(preserved_df).select(original_columns)
        return df, []

    # Re-attach preserved columns and restore the original column order.
    if preserved_df is not None and preserved_df.width > 0:
        result.df = result.df.hstack(preserved_df).select(original_columns)

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

    return result.df, fixes


def build_transform(column: str, op: str):
    """Back-compat shim. Returns a closure equivalent to apply_plan(df, TransformPlan(column, op)).

    New code should construct `TransformPlan` directly and call `apply_plan`.
    This shim exists so callers that still take a callable continue working.
    """
    from goldenmatch.distributed.transforms import TransformPlan, apply_plan

    plan = TransformPlan(column=column, op=op)
    return lambda df: apply_plan(df, plan)
