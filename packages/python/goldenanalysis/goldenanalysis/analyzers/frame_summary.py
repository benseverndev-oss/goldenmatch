"""``frame.summary`` — generic frame metrics, zero suite deps, always available.

Emits the Appendix-A metric set: row/column counts, mean null ratio, exact-
duplicate-row ratio, estimated in-memory size, plus a ``per_column`` table
(column, dtype, null_ratio, n_unique).
"""

from __future__ import annotations

from goldenanalysis.core import aggregate as agg
from goldenanalysis.models import (
    AnalysisTable,
    AnalyzerInfo,
    AnalyzerInput,
    AnalyzerResult,
    Metric,
)

_PRODUCES = [
    "frame.row_count",
    "frame.column_count",
    "frame.null_ratio_mean",
    "frame.duplicate_row_ratio",
    "frame.memory_bytes",
]


class FrameSummaryAnalyzer:
    """Summarize a raw frame: shape, null mass, duplication, memory footprint."""

    info = AnalyzerInfo(name="frame.summary", consumes=["frame"], produces=_PRODUCES)

    def run(self, inp: AnalyzerInput) -> AnalyzerResult:
        df = inp.frame
        if df is None:
            raise ValueError("frame.summary requires AnalyzerInput.frame (a polars DataFrame)")

        n_rows = df.height
        n_cols = df.width
        null_ratios = agg.null_ratio_per_column(df)
        null_mean = sum(null_ratios.values()) / n_cols if n_cols else 0.0
        dup_ratio = agg.duplicate_row_ratio(df)
        mem_bytes = df.estimated_size()

        metrics = [
            Metric(key="frame.row_count", value=n_rows, unit="rows", direction="neutral"),
            Metric(key="frame.column_count", value=n_cols, unit="columns", direction="neutral"),
            Metric(
                key="frame.null_ratio_mean",
                value=null_mean,
                unit="ratio",
                direction="lower_better",
            ),
            Metric(
                key="frame.duplicate_row_ratio",
                value=dup_ratio,
                unit="ratio",
                direction="lower_better",
            ),
            Metric(key="frame.memory_bytes", value=mem_bytes, unit="bytes", direction="neutral"),
        ]

        per_column = AnalysisTable(
            name="per_column",
            columns=["column", "dtype", "null_ratio", "n_unique"],
            rows=[
                [col, str(df[col].dtype), null_ratios[col], df[col].n_unique()]
                for col in df.columns
            ],
        )

        return AnalyzerResult(metrics=metrics, tables=[per_column])
