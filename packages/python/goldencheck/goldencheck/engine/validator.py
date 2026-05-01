"""Validator — checks data against pinned rules in goldencheck.yml."""
from __future__ import annotations
import logging
from pathlib import Path
import polars as pl
from goldencheck.engine.reader import read_file
from goldencheck.config.schema import GoldenCheckConfig, ColumnRule
from goldencheck.models.finding import Finding, Severity

logger = logging.getLogger(__name__)


def validate_file(path: Path, config: GoldenCheckConfig) -> list[Finding]:
    df = read_file(path)
    findings: list[Finding] = []
    for col_name, rule in config.columns.items():
        if col_name not in df.columns:
            findings.append(Finding(
                severity=Severity.WARNING,
                column=col_name,
                check="existence",
                message=f"Column '{col_name}' defined in rules but not found in data",
            ))
            continue
        col = df[col_name]
        findings.extend(_check_column(col, col_name, rule))
    # Filter out ignored findings
    ignored = {(i.column, i.check) for i in config.ignore}
    findings = [f for f in findings if (f.column, f.check) not in ignored]
    findings.sort(key=lambda f: f.severity, reverse=True)
    return findings


def _check_column(col: pl.Series, name: str, rule: ColumnRule) -> list[Finding]:
    findings: list[Finding] = []

    # Required check
    if rule.required and col.null_count() > 0:
        findings.append(Finding(
            severity=Severity.ERROR,
            column=name,
            check="required",
            message=f"Required column has {col.null_count()} null values",
            affected_rows=col.null_count(),
        ))

    # Unique check
    if rule.unique:
        non_null = col.drop_nulls()
        dups = len(non_null) - non_null.n_unique()
        if dups > 0:
            findings.append(Finding(
                severity=Severity.ERROR,
                column=name,
                check="unique",
                message=f"Column should be unique but has {dups} duplicates",
                affected_rows=dups,
            ))

    # Enum check
    if rule.enum:
        non_null = col.drop_nulls().cast(pl.Utf8)
        invalid = non_null.filter(~non_null.is_in(rule.enum))
        if len(invalid) > 0:
            samples = invalid.head(5).to_list()
            findings.append(Finding(
                severity=Severity.ERROR,
                column=name,
                check="enum",
                message=f"{len(invalid)} values not in allowed enum {rule.enum}",
                affected_rows=len(invalid),
                sample_values=[str(s) for s in samples],
            ))

    # Range check
    if rule.range and len(rule.range) == 2:
        try:
            numeric = col.drop_nulls().cast(pl.Float64, strict=False).drop_nulls()
            lo, hi = rule.range
            out_of_range = numeric.filter((numeric < lo) | (numeric > hi))
            if len(out_of_range) > 0:
                samples = out_of_range.head(5).to_list()
                findings.append(Finding(
                    severity=Severity.ERROR,
                    column=name,
                    check="range",
                    message=f"{len(out_of_range)} values outside range [{lo}, {hi}]",
                    affected_rows=len(out_of_range),
                    sample_values=[str(s) for s in samples],
                ))
        except Exception:
            pass

    return findings
