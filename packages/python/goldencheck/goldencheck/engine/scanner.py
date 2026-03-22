"""Scanner — orchestrates all profilers and collects findings."""
from __future__ import annotations
import logging
from pathlib import Path
import polars as pl
from goldencheck.engine.reader import read_file
from goldencheck.engine.sampler import maybe_sample
from goldencheck.models.finding import Finding
from goldencheck.models.profile import ColumnProfile, DatasetProfile
from goldencheck.profilers.type_inference import TypeInferenceProfiler
from goldencheck.profilers.nullability import NullabilityProfiler
from goldencheck.profilers.uniqueness import UniquenessProfiler
from goldencheck.profilers.format_detection import FormatDetectionProfiler
from goldencheck.profilers.range_distribution import RangeDistributionProfiler
from goldencheck.profilers.cardinality import CardinalityProfiler
from goldencheck.profilers.pattern_consistency import PatternConsistencyProfiler
from goldencheck.relations.temporal import TemporalOrderProfiler
from goldencheck.relations.null_correlation import NullCorrelationProfiler

logger = logging.getLogger(__name__)

COLUMN_PROFILERS = [
    TypeInferenceProfiler(),
    NullabilityProfiler(),
    UniquenessProfiler(),
    FormatDetectionProfiler(),
    RangeDistributionProfiler(),
    CardinalityProfiler(),
    PatternConsistencyProfiler(),
]

RELATION_PROFILERS = [
    TemporalOrderProfiler(),
    NullCorrelationProfiler(),
]

def scan_file(path: Path, sample_size: int = 100_000) -> tuple[list[Finding], DatasetProfile]:
    df = read_file(path)
    row_count = len(df)
    sample = maybe_sample(df, max_rows=sample_size)
    logger.info("Scanning %d rows, %d columns", row_count, len(df.columns))

    all_findings: list[Finding] = []
    column_profiles: list[ColumnProfile] = []

    for col_name in df.columns:
        col = df[col_name]
        non_null = col.drop_nulls()
        cp = ColumnProfile(
            name=col_name,
            inferred_type=str(col.dtype),
            null_count=col.null_count(),
            null_pct=col.null_count() / row_count if row_count > 0 else 0,
            unique_count=non_null.n_unique() if len(non_null) > 0 else 0,
            unique_pct=non_null.n_unique() / len(non_null) if len(non_null) > 0 else 0,
            row_count=row_count,
        )
        column_profiles.append(cp)
        for profiler in COLUMN_PROFILERS:
            try:
                findings = profiler.profile(sample, col_name)
                all_findings.extend(findings)
            except Exception as e:
                logger.warning("Profiler %s failed on column %s: %s", type(profiler).__name__, col_name, e)

    for profiler in RELATION_PROFILERS:
        try:
            findings = profiler.profile(sample)
            all_findings.extend(findings)
        except Exception as e:
            logger.warning("Relation profiler %s failed: %s", type(profiler).__name__, e)

    all_findings.sort(key=lambda f: f.severity, reverse=True)
    profile = DatasetProfile(file_path=str(path), row_count=row_count, column_count=len(df.columns), columns=column_profiles)
    return all_findings, profile
