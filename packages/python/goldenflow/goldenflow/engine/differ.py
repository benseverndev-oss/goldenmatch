from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl


@dataclass
class DiffResult:
    total_changes: int = 0
    changed_columns: list[str] = field(default_factory=list)
    added_columns: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)
    row_count_before: int = 0
    row_count_after: int = 0
    column_details: dict[str, dict] = field(default_factory=dict)


def diff_dataframes(before: pl.DataFrame, after: pl.DataFrame) -> DiffResult:
    """Compare two DataFrames and report differences."""
    result = DiffResult(
        row_count_before=before.shape[0],
        row_count_after=after.shape[0],
    )

    before_cols = set(before.columns)
    after_cols = set(after.columns)

    result.added_columns = sorted(after_cols - before_cols)
    result.removed_columns = sorted(before_cols - after_cols)

    common_cols = before_cols & after_cols
    total_changes = 0

    for col in sorted(common_cols):
        if before.shape[0] != after.shape[0]:
            result.changed_columns.append(col)
            total_changes += abs(before.shape[0] - after.shape[0])
            continue

        try:
            b_series = before[col].cast(pl.Utf8)
            a_series = after[col].cast(pl.Utf8)
            changes = (b_series != a_series).sum()
            if changes > 0:
                result.changed_columns.append(col)
                total_changes += changes
                result.column_details[col] = {"changed_rows": changes}
        except Exception:
            result.changed_columns.append(col)
            total_changes += before.shape[0]

    result.total_changes = total_changes
    return result
