"""Build representative sample blocks from DataFrame + findings."""
from __future__ import annotations
import logging
import random
from collections import defaultdict
import polars as pl
from goldencheck.models.finding import Finding

logger = logging.getLogger(__name__)


def build_sample_blocks(
    df: pl.DataFrame,
    findings: list[Finding],
    max_columns: int = 50,
    focus_columns: set[str] | None = None,
) -> dict[str, dict]:
    """Build a representative sample block for each column."""
    random.seed(42)

    # If focus_columns is specified, filter to only those columns early
    columns = list(df.columns)
    if focus_columns is not None:
        columns = [c for c in columns if c in focus_columns]

    # If too many columns, prioritize those with most findings
    if len(columns) > max_columns:
        logger.warning(
            "LLM boost limited to %d columns (dataset has %d). "
            "Columns with most findings prioritized.", max_columns, len(columns)
        )
        finding_counts = defaultdict(int)
        for f in findings:
            finding_counts[f.column] += 1
        columns = sorted(columns, key=lambda c: finding_counts[c], reverse=True)[:max_columns]

    # Index findings by column
    findings_by_col = defaultdict(list)
    for f in findings:
        findings_by_col[f.column].append(f)

    blocks = {}
    for col_name in columns:
        col = df[col_name]
        non_null = col.drop_nulls()

        # Metadata
        block: dict = {
            "column": col_name,
            "dtype": str(col.dtype),
            "row_count": len(df),
            "null_count": col.null_count(),
            "null_pct": round(col.null_count() / len(df), 3) if len(df) > 0 else 0,
            "unique_count": non_null.n_unique() if len(non_null) > 0 else 0,
        }

        # Top values (most frequent)
        if len(non_null) > 0:
            vc = non_null.value_counts().sort("count", descending=True)
            col_val_name = vc.columns[0]
            top = vc.head(5)
            block["top_values"] = [
                {"value": str(row[col_val_name]), "count": row["count"]}
                for row in top.iter_rows(named=True)
            ]

            # Rare values (least frequent)
            rare = vc.tail(5)
            block["rare_values"] = [
                {"value": str(row[col_val_name]), "count": row["count"]}
                for row in rare.iter_rows(named=True)
            ]

            # Random sample from middle
            all_vals = non_null.to_list()
            sample_size = min(5, len(all_vals))
            block["random_sample"] = [str(v) for v in random.sample(all_vals, sample_size)]
        else:
            block["top_values"] = []
            block["rare_values"] = []
            block["random_sample"] = []

        # Flagged values from profiler findings
        flagged = set()
        for f in findings_by_col.get(col_name, []):
            flagged.update(f.sample_values)
        block["flagged_values"] = list(flagged)

        # Existing findings
        block["existing_findings"] = [
            {"severity": f.severity.name.lower(), "check": f.check, "message": f.message}
            for f in findings_by_col.get(col_name, [])
        ]

        blocks[col_name] = block

    return blocks
