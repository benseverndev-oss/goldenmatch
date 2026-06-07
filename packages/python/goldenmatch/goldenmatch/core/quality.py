"""GoldenCheck integration — enhanced data quality scanning before matching."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def _scan_findings(df: pl.DataFrame, domain: str | None):
    """Run the goldencheck scan and confidence-downgrade pass.

    Prefers the in-memory `scan_dataframe` entry point (added 2026-05-28
    to skip the write_csv/read_csv round-trip that was 121s of the 10M
    pipeline_prep_quality_scan wall). Falls back to the temp-CSV path
    when an older goldencheck is installed.
    """
    from goldencheck.engine.confidence import apply_confidence_downgrade

    try:
        from goldencheck.engine.scanner import scan_dataframe
    except ImportError:
        scan_dataframe = None  # type: ignore[assignment]

    if scan_dataframe is not None:
        findings, _ = scan_dataframe(df, domain=domain)
        return apply_confidence_downgrade(findings, llm_boost=False)

    from goldencheck.engine.scanner import scan_file
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        df.write_csv(tmp.name)
        tmp_path = Path(tmp.name)
    try:
        findings, _ = scan_file(tmp_path, domain=domain)
        return apply_confidence_downgrade(findings, llm_boost=False)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not delete temp file %s", tmp_path)


def _goldencheck_available() -> bool:
    """Check if goldencheck is installed."""
    try:
        import goldencheck  # noqa: F401
        return True
    except ImportError:
        return False


def compute_quality_scores(
    df: pl.DataFrame,
    row_id_col: str = "__row_id__",
) -> dict[tuple[int, str], float] | None:
    """Per-cell quality weights for quality-weighted survivorship, keyed by
    ``(__row_id__, column)`` so the golden-record builders prefer the
    higher-quality value when merging a cluster (e.g. ``"California"`` over
    ``"Californa"``; a real date over a ``2099`` one).

    Delegates to ``goldencheck.cell_quality`` -- the single source of DQ truth.
    Fail-open: returns ``None`` when goldencheck is absent, too old to expose
    ``cell_quality``, or finds no penalized cells. Callers treat ``None`` as "no
    weighting" and keep the fast survivorship path, so a clean frame has ZERO
    behaviour/perf change -- weighting only kicks in when there are real issues.

    ``cell_quality`` returns positional row indices; we remap them to
    ``row_id_col`` so the builders' ``(row_id, col)`` lookups line up."""
    if not _goldencheck_available() or row_id_col not in df.columns:
        return None
    try:
        from goldencheck import cell_quality
    except ImportError:
        return None  # older goldencheck without the per-cell API

    try:
        positional = cell_quality(df)
    except Exception:  # noqa: BLE001 - never let DQ scoring break a dedupe run
        logger.debug("goldencheck.cell_quality failed; skipping quality weighting", exc_info=True)
        return None
    if not positional:
        return None

    row_ids = df[row_id_col].to_list()
    scores: dict[tuple[int, str], float] = {}
    for (idx, col), weight in positional.items():
        if 0 <= idx < len(row_ids) and row_ids[idx] is not None:
            scores[(int(row_ids[idx]), col)] = weight
    return scores or None


def blocking_risk(df: pl.DataFrame) -> dict[str, float] | None:
    """Per-column "block-shatter risk" for quality-aware blocking.

    `risk[col]` is the fraction of rows whose value is an edit-distance variant
    of a more-frequent value in that (string) column -- the records that would
    shard off their canonical exact block and be lost to recall. Derived
    fail-open by reusing `goldencheck.cell_quality`: every penalized cell in a
    string column is such a fuzzy variant (the date/future-dated penalties live
    on non-string columns and are excluded here).

    Returns ``None`` when goldencheck is absent, too old, or the data is clean --
    callers treat that as "no risk" and leave blocking unchanged."""
    if not _goldencheck_available():
        return None
    try:
        from goldencheck import cell_quality
    except ImportError:
        return None
    n = df.height
    if n == 0:
        return None
    try:
        positional = cell_quality(df)
    except Exception:  # noqa: BLE001 - never let DQ scoring break auto-config
        logger.debug("goldencheck.cell_quality failed; skipping blocking risk", exc_info=True)
        return None
    if not positional:
        return None

    string_cols = {c for c, dt in zip(df.columns, df.dtypes) if dt == pl.Utf8}
    counts: dict[str, int] = {}
    for (_idx, col), _weight in positional.items():
        if col in string_cols:
            counts[col] = counts.get(col, 0) + 1
    if not counts:
        return None
    return {col: cnt / n for col, cnt in counts.items()}


def fd_identity_scores(df: pl.DataFrame) -> dict[str, float] | None:
    """Per-column data-driven "identity anchor" strength for FD-driven negative
    evidence (door #3). ``d[col]`` is the strongest functional dependency the
    column supports as a determinant (1.0 for a strict FD, ``1 - violations/rows``
    for an approximate one) -- a column that determines other columns is, by
    construction, a discriminative entity attribute, so disagreement on it is
    negative evidence.

    Delegates to ``goldencheck.functional_dependencies``. Fail-open: ``None`` when
    goldencheck is absent/too old or no FDs are found.

    Note: perfectly-unique keys (cardinality 1.0) are NOT surfaced -- FD discovery
    excludes them as trivial determinants. The complementary signal for those is
    format/structure consistency (a separate door); this catches identity anchors
    with cardinality in [0.5, 1.0) that the name heuristic misses."""
    if not _goldencheck_available():
        return None
    try:
        from goldencheck import functional_dependencies
    except ImportError:
        return None  # older goldencheck without the FD API
    try:
        fds = functional_dependencies(df)
    except Exception:  # noqa: BLE001 - never let FD discovery break auto-config
        logger.debug("goldencheck.functional_dependencies failed; skipping", exc_info=True)
        return None
    if not fds:
        return None
    # one record per determinant; confidence is already the max it supports.
    return {fd.determinant: fd.confidence for fd in fds} or None


def run_quality_check(
    df: pl.DataFrame,
    config=None,
) -> tuple[pl.DataFrame, list[dict]]:
    """Run GoldenCheck scan + fix if available.

    Returns (fixed_df, list_of_fixes) matching autofix format.
    Falls back gracefully if goldencheck is not installed.
    """
    if not _goldencheck_available():
        return df, []

    # Parse config
    enabled = True
    mode = "announced"
    fix_mode = "safe"
    domain = None

    if config is not None:
        mode = getattr(config, "mode", "announced")
        fix_mode = getattr(config, "fix_mode", "safe")
        domain = getattr(config, "domain", None)
        enabled = getattr(config, "enabled", True)

    if not enabled or mode == "disabled":
        return df, []

    if fix_mode == "none":
        # Scan only, no fixes
        return _scan_only(df, mode, domain)

    return _scan_and_fix(df, mode, fix_mode, domain)


def _scan_only(
    df: pl.DataFrame,
    mode: str,
    domain: str | None,
) -> tuple[pl.DataFrame, list[dict]]:
    """Run GoldenCheck scan without fixes. Reports findings."""
    from goldencheck.models.finding import Severity

    findings = _scan_findings(df, domain=domain)

    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)

    if mode == "announced":
        logger.info(
            "GoldenCheck: %d issues found (%d errors, %d warnings)",
            len(findings), errors, warnings,
        )

    # Return findings as dicts so callers (MCP tools) can inspect them
    issues = []
    for f in findings:
        issues.append({
            # goldencheck's Finding dataclass exposes `check` and
            # `affected_rows` (see goldencheck/models/finding.py); the
            # `rule_id`/`rows_affected` names used previously don't exist
            # and raised AttributeError, which the caller swallowed as a
            # quality-scan warning. The serialized dict keys stay
            # "rule"/"rows_affected" (the MCP scan_quality contract).
            "rule": f.check,
            # Severity is goldencheck's IntEnum (INFO=1/WARNING=2/ERROR=3),
            # so `.value` is an int — but consumers compare against the
            # lowercase name (the web router does
            # `i["severity"].lower() == "error"`). Serialize the name, not the
            # int, so `severity` is always a string like "warning"/"error".
            "severity": (
                f.severity.name.lower()
                if hasattr(f.severity, "name")
                else str(f.severity).lower()
            ),
            "column": f.column,
            "message": f.message,
            "rows_affected": f.affected_rows,
            "confidence": round(f.confidence, 2) if hasattr(f, "confidence") else None,
        })

    return df, issues


def _scan_and_fix(
    df: pl.DataFrame,
    mode: str,
    fix_mode: str,
    domain: str | None,
) -> tuple[pl.DataFrame, list[dict]]:
    """Run GoldenCheck scan + apply fixes."""
    from goldencheck.engine.fixer import apply_fixes

    findings = _scan_findings(df, domain=domain)

    # Apply fixes
    fixed_df, report = apply_fixes(df, findings, mode=fix_mode)

    # Convert to autofix-compatible format
    fixes = []
    for entry in report.entries:
        fixes.append({
            "fix": f"goldencheck:{entry.fix_type}",
            "column": entry.column,
            "rows_affected": entry.rows_affected,
            "detail": (
                f"{entry.fix_type}: {entry.rows_affected} rows"
                + (f" (e.g., {entry.sample_before[0]} → {entry.sample_after[0]})"
                   if entry.sample_before and entry.sample_after else "")
            ),
        })

    if mode == "announced" and fixes:
        fix_types = set(e.fix_type for e in report.entries)
        print(
            f"GoldenCheck: scanning data quality... "
            f"{len(findings)} issues found, {len(fixes)} auto-fixed "
            f"({', '.join(sorted(fix_types))})"
        )
    elif mode == "announced":
        print("GoldenCheck: scanning data quality... no fixes needed")

    return fixed_df, fixes
