"""Auto-fix module for common data issues in GoldenMatch."""

from __future__ import annotations

from goldenmatch._polars_lazy import pl


def auto_fix_dataframe(
    df: pl.DataFrame,
    profile: dict | None = None,
) -> tuple[pl.DataFrame, list[dict]]:
    """Automatically detect and fix common data issues.

    Returns (fixed_df, list_of_fixes_applied). Each fix is a dict with keys:
    fix, column, rows_affected, detail.

    W5b-3b: the seven fixes live on the Frame seam (``Frame.auto_fix`` --
    polars impl VERBATIM from this module's old body; probed arrow twin).
    """
    from goldenmatch.core.frame import to_frame

    fixed, fixes = to_frame(df).auto_fix(profile)
    return fixed.native, fixes
