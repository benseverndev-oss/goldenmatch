"""Scanner — orchestrates all profilers and collects findings."""
from __future__ import annotations

import dataclasses
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldencheck.baseline.models import BaselineProfile
from goldencheck.core._native_loader import native_enabled
from goldencheck.core.frame import PyFrame
from goldencheck.engine.confidence import apply_corroboration_boost
from goldencheck.engine.reader import read_columns
from goldencheck.engine.sampler import maybe_sample
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import ColumnProfile, DatasetProfile
from goldencheck.profilers.cardinality import CardinalityProfiler
from goldencheck.profilers.drift_detection import DriftDetectionProfiler
from goldencheck.profilers.encoding_detection import EncodingDetectionProfiler
from goldencheck.profilers.format_detection import FormatDetectionProfiler
from goldencheck.profilers.freshness import FreshnessProfiler
from goldencheck.profilers.fuzzy_values import FuzzyValuesProfiler
from goldencheck.profilers.nullability import NullabilityProfiler
from goldencheck.profilers.pattern_consistency import PatternConsistencyProfiler
from goldencheck.profilers.range_distribution import RangeDistributionProfiler
from goldencheck.profilers.sequence_detection import SequenceDetectionProfiler
from goldencheck.profilers.type_inference import TypeInferenceProfiler
from goldencheck.profilers.uniqueness import UniquenessProfiler
from goldencheck.relations.age_validation import AgeValidationProfiler
from goldencheck.relations.approx_duplicate import ApproxDuplicateProfiler
from goldencheck.relations.approx_fd import ApproximateFDProfiler
from goldencheck.relations.composite_key import CompositeKeyProfiler
from goldencheck.relations.functional_dependency import FunctionalDependencyProfiler
from goldencheck.relations.identity_safe_pk import IdentitySafePkProfiler
from goldencheck.relations.null_correlation import NullCorrelationProfiler
from goldencheck.relations.numeric_cross import NumericCrossColumnProfiler
from goldencheck.relations.temporal import TemporalOrderProfiler

logger = logging.getLogger(__name__)

__all__ = [
    "scan_file", "scan_file_with_llm", "scan_dataframe", "scan_columns", "scan_file_columns",
]

COLUMN_PROFILERS = [
    TypeInferenceProfiler(),
    NullabilityProfiler(),
    UniquenessProfiler(),
    FormatDetectionProfiler(),
    RangeDistributionProfiler(),
    CardinalityProfiler(),
    PatternConsistencyProfiler(),
    EncodingDetectionProfiler(),
    SequenceDetectionProfiler(),
    DriftDetectionProfiler(),
    # Fuzzy near-duplicate VALUE detection (inconsistent categorical encodings).
    # Kernel-backed (trigram+prefix blocking + Levenshtein); Python fallback.
    FuzzyValuesProfiler(),
    # Freshness: future-dated values + (name-gated) staleness on date/datetime cols.
    FreshnessProfiler(),
]

RELATION_PROFILERS = [
    TemporalOrderProfiler(),
    NullCorrelationProfiler(),
    NumericCrossColumnProfiler(),
    AgeValidationProfiler(),
    # Preflight: warn when no stable PK column exists (goldenmatch #207).
    # Identity Graph downstreams need source_pk_column to avoid record_id
    # collisions on duplicate raw rows.
    IdentitySafePkProfiler(),
    # Discover minimal composite keys when no single-column key exists
    # (kernel-backed via goldencheck[native]; pure-Python fallback otherwise).
    CompositeKeyProfiler(),
    # Exact + near-duplicate (normalized) row detection.
    ApproxDuplicateProfiler(),
    # Discover strict single-column functional dependencies (redundant columns /
    # lookup relationships). Kernel-backed; pure-Polars fallback.
    FunctionalDependencyProfiler(),
    # Surface rows that BREAK a near-strict FD (likely data-entry errors).
    ApproximateFDProfiler(),
]

def _post_classification_checks(
    sample,
    findings: list[Finding],
    column_types: dict,
) -> list[Finding]:
    """Add findings that require semantic type knowledge.

    Routes through the Frame/Column seam so ``sample`` may be an Arrow-native
    ``ArrowFrame`` (default scan path) or a ``PolarsFrame``.
    """
    from goldencheck.core.frame import to_frame

    frame = to_frame(sample)
    cols = frame.columns
    new_findings = list(findings)

    for col_name, classification in column_types.items():
        if classification.type_name != "person_name":
            continue
        if col_name not in cols:
            continue
        col = frame.column(col_name)
        if col.dtype != "str":
            continue

        # Detect digit characters in person name columns
        non_null = col.drop_nulls()
        if len(non_null) == 0:
            continue

        digit_count = non_null.str_match_count(r"\d")
        if digit_count > 0:
            digit_pct = digit_count / len(non_null)
            # Only flag if it's a minority (< 10%) — widespread digits means it's not really a name column
            if 0 < digit_pct < 0.10:
                sample_vals = non_null.str_filter(r"\d", matching=True).slice(0, 5).to_list()
                new_findings.append(Finding(
                    severity=Severity.WARNING,
                    column=col_name,
                    check="type_inference",
                    message=(
                        f"Column '{col_name}' appears to be a person name but {digit_count} "
                        f"row(s) ({digit_pct:.1%}) contain numeric characters — possible invalid values"
                    ),
                    affected_rows=digit_count,
                    sample_values=[str(v) for v in sample_vals],
                    suggestion="Check for data entry errors or encoding issues in name values",
                    confidence=0.85,
                ))

    # --- Code-like format inconsistency (e.g. 5-digit vs 9-digit zip) ---
    # Only add if no pattern_consistency finding already exists at WARNING+ for this column
    existing_pc_cols = {
        f.column for f in new_findings
        if f.check == "pattern_consistency" and f.severity in (Severity.WARNING, Severity.ERROR)
    }
    for col_name, classification in column_types.items():
        if not classification or classification.type_name not in ("geo", "identifier"):
            continue
        if col_name in existing_pc_cols:
            continue
        if col_name not in cols:
            continue
        col = frame.column(col_name)
        if col.dtype != "str":
            continue
        non_null = col.drop_nulls()
        total = len(non_null)
        if total == 0:
            continue
        # Generalise each value to its digit/letter skeleton (letters -> L, then
        # digits -> D; order matches PatternConsistencyProfiler / _generalize_series),
        # then tally the skeletons via the seam.
        patterns = non_null.str_replace_all(r"\p{L}", "L").str_replace_all(r"\d", "D")
        pattern_counts = patterns.value_counts_desc()
        if len(pattern_counts) < 2:
            continue
        dominant_pattern = pattern_counts[0][0]
        # Only check code-like patterns (mostly digits)
        digit_ratio = sum(1 for c in dominant_pattern if c == "D") / max(len(dominant_pattern), 1)
        if digit_ratio < 0.5:
            continue
        # Look for any secondary pattern with different length
        for i in range(1, len(pattern_counts)):
            minority_pattern, minority_count = pattern_counts[i]
            minority_count = int(minority_count)
            if abs(len(dominant_pattern) - len(minority_pattern)) > 1:
                new_findings.append(Finding(
                    severity=Severity.WARNING,
                    column=col_name,
                    check="pattern_consistency",
                    message=(
                        f"Inconsistent pattern detected: '{minority_pattern}' appears in "
                        f"{minority_count} row(s) ({minority_count / total:.1%}) vs dominant pattern "
                        f"'{dominant_pattern}'"
                    ),
                    affected_rows=minority_count,
                    sample_values=non_null.filter_by(patterns.eq(minority_pattern)).slice(0, 5).to_list(),
                    suggestion="Standardize values to a single format/pattern",
                    confidence=0.8,
                    metadata={"dominant_pattern": dominant_pattern, "minority_pattern": minority_pattern},
                ))
                break  # Only flag the most significant pattern difference

    # --- String length format check for identifier-like columns ---
    _ID_NAME_KEYWORDS = ("id", "number", "code", "auth", "key")
    _ID_NAME_EXCLUDE = ("phone", "npi")
    for col_name in cols:
        col = frame.column(col_name)

        # Accept string or numeric columns (numeric IDs are common)
        _cat = col.dtype
        is_string = _cat == "str"
        is_numeric = _cat in ("int", "uint", "float")
        if not (is_string or is_numeric):
            continue

        # Check if column name suggests it's an identifier/code
        name_lower = col_name.lower()
        if not any(kw in name_lower for kw in _ID_NAME_KEYWORDS):
            continue
        if any(exc in name_lower for exc in _ID_NAME_EXCLUDE):
            continue

        non_null = col.drop_nulls()
        total = len(non_null)
        if total == 0:
            continue

        # Cast to string for length analysis
        str_vals = non_null.cast("str") if is_numeric else non_null
        lengths = str_vals.str_len_chars()
        length_counts = lengths.value_counts_desc()

        if len(length_counts) == 0:
            continue

        dominant_length = int(length_counts[0][0])
        dominant_count = int(length_counts[0][1])
        dominant_pct = dominant_count / total

        outlier_count = total - dominant_count

        if dominant_pct > 0.90 and outlier_count > 0:
            _lens = lengths.to_list()
            _svals = str_vals.to_list()
            sample_vals = [_svals[i] for i in range(len(_lens)) if _lens[i] != dominant_length][:5]
            new_findings.append(Finding(
                severity=Severity.WARNING,
                column=col_name,
                check="format_detection",
                message=(
                    f"Inconsistent string length: {dominant_pct:.0%} of values are "
                    f"{dominant_length} chars but {outlier_count} row(s) have different "
                    f"lengths — possible invalid format"
                ),
                affected_rows=outlier_count,
                sample_values=[str(v) for v in sample_vals],
                suggestion="Verify that all values conform to the expected length",
                confidence=0.75,
            ))

    return new_findings


_MECHANICAL_PROFILERS = [NullabilityProfiler(), UniquenessProfiler(), CardinalityProfiler()]
_HARD_PROFILERS = [EncodingDetectionProfiler(), FormatDetectionProfiler(), PatternConsistencyProfiler()]


def scan_columns(columns: dict[str, list]) -> list[Finding]:
    """Polars-free reduced scan of the covered column checks over in-memory columns.
    The mechanical checks (nullability/uniqueness/cardinality) always run; the regex
    checks (encoding/format/pattern_consistency) run when the native regex kernel is
    available (`pip install goldencheck[native]`) and are skipped-with-a-log otherwise.
    The temporal-order relation check runs once over the whole frame when the native
    date kernel (`str_to_date`) is available and is skipped-with-a-log otherwise.
    Other relational checks still need Polars -- use scan_dataframe for a full scan."""
    frame = PyFrame.from_columns(columns)
    profilers = list(_MECHANICAL_PROFILERS)
    if native_enabled("regex"):
        profilers += _HARD_PROFILERS
    else:
        logger.info(
            "scan_columns: native regex kernel unavailable; skipping encoding/format/"
            "pattern_consistency checks. Install with `pip install goldencheck[native]`."
        )
    findings: list[Finding] = []
    for name in columns:
        for profiler in profilers:
            findings.extend(profiler.profile(frame, name))
    if native_enabled("str_to_date"):
        findings.extend(TemporalOrderProfiler().profile(frame))
    else:
        logger.info(
            "scan_columns: native date kernel unavailable; skipping the temporal-order "
            "check. Install with `pip install goldencheck[native]`."
        )
    return findings


def scan_file_columns(path: Path) -> list[Finding]:
    """Polars-free file scan: read a file into columns (Parquet/Excel without Polars;
    CSV needs Polars) and run the covered structural checks via scan_columns(). For the
    full scan (classification, sampling, denial, Polars-only relation checks) use
    scan_file()."""
    return scan_columns(read_columns(path))


def scan_dataframe(
    df,
    file_path: str | Path = "<dataframe>",
    sample_size: int = 100_000,
    return_sample: bool = False,
    domain: str | None = None,
    baseline: BaselineProfile | Path | None = None,
    schema: object | None = None,
    deep: bool = False,
    denial: bool = False,
) -> tuple[list[Finding], DatasetProfile] | tuple[list[Finding], DatasetProfile, object]:
    """Scan an already-loaded table.

    Accepts a ``pyarrow.Table`` natively (wrapped in an Arrow-native
    ``ArrowFrame``); a ``polars.DataFrame`` is a convenience overload, converted
    via ``.to_arrow()`` when Polars is importable. Same semantics as
    :func:`scan_file` but the caller hands the in-memory table straight to the
    scanner (skips a CSV round-trip -- the 10M QIS bench spent 121s of
    `pipeline_prep_quality_scan` wall writing df to a temp CSV just to read it
    back).

    `file_path` is purely cosmetic (populates `DatasetProfile.file_path`).
    """
    frame = _coerce_to_arrow_frame(df)
    return _scan_dataframe_impl(
        frame,
        file_path=str(file_path),
        sample_size=sample_size,
        return_sample=return_sample,
        domain=domain,
        baseline=baseline,
        schema=schema,
        deep=deep,
        denial=denial,
    )


def scan_file(
    path: Path,
    sample_size: int = 100_000,
    return_sample: bool = False,
    domain: str | None = None,
    baseline: BaselineProfile | Path | None = None,
    schema: object | None = None,  # goldencheck_types.InferredSchema; loose typing avoids hard import dep
    deep: bool = False,
    denial: bool = False,
) -> tuple[list[Finding], DatasetProfile] | tuple[list[Finding], DatasetProfile, object]:
    from goldencheck.core.frame import ArrowFrame
    from goldencheck.engine.reader import read_file_arrow

    frame = ArrowFrame(read_file_arrow(path))
    return _scan_dataframe_impl(
        frame,
        file_path=str(path),
        sample_size=sample_size,
        return_sample=return_sample,
        domain=domain,
        baseline=baseline,
        schema=schema,
        deep=deep,
        denial=denial,
    )


def _coerce_to_arrow_frame(data):
    """Wrap ``data`` in an Arrow-native ``ArrowFrame``. Accepts a ``pyarrow.Table``
    or (convenience) a ``polars.DataFrame`` (converted via ``.to_arrow()`` only
    when Polars imports) or an already-wrapped ``ArrowFrame``."""
    import pyarrow as pa

    from goldencheck.core.frame import ArrowFrame

    if isinstance(data, ArrowFrame):
        return data
    if isinstance(data, pa.Table):
        return ArrowFrame(data)
    try:
        import polars as _pl
    except ImportError:
        raise TypeError(
            f"scan_dataframe expects a pyarrow.Table or polars.DataFrame; got {type(data)!r}"
        )
    if isinstance(data, _pl.DataFrame):
        return ArrowFrame(data.to_arrow())
    raise TypeError(
        f"scan_dataframe expects a pyarrow.Table or polars.DataFrame; got {type(data)!r}"
    )


def _to_polars(frame):
    """Materialise a seam Frame back to a ``polars.DataFrame`` for the off-default
    branches (baseline drift, learned rules, denial) that still run on Polars."""
    import polars as _pl

    native = frame.native
    if isinstance(native, _pl.DataFrame):
        return native
    return _pl.from_arrow(native)


def _sample_for_return(frame):
    """The ``return_sample=True`` payload: a ``polars.DataFrame`` when Polars is
    importable (downstream LLM sample-block builder consumes Polars), else the
    Arrow-native ``pyarrow.Table``."""
    native = frame.native
    try:
        import polars as _pl
    except ImportError:
        return native
    if isinstance(native, _pl.DataFrame):
        return native
    return _pl.from_arrow(native)


def _scan_threads(ncols: int, row_count: int) -> int:
    """How many threads to fan the column loop across. `GOLDENCHECK_SCAN_THREADS`
    overrides (1 = sequential, for debug/determinism checks). Otherwise parallelize
    only when there's enough work to beat the thread-pool overhead: multiple columns
    and a non-trivial row count. Capped at min(cpu, 8, ncols)."""
    env = os.environ.get("GOLDENCHECK_SCAN_THREADS")
    if env is not None:
        try:
            return max(1, min(int(env), ncols))
        except ValueError:
            return 1
    if ncols < 2 or row_count < 50_000:
        return 1
    return max(1, min(os.cpu_count() or 1, 8, ncols))


def _profile_column(frame, sample, col_name: str, row_count: int):
    """Profile ONE column: its ColumnProfile (from the full ``frame``) + the
    COLUMN_PROFILERS over ``sample``. Pure w.r.t. its inputs and a LOCAL context
    dict, so it is safe to run concurrently across columns (see the caller)."""
    ctx: dict = {}   # per-column: context is keyed by column, no cross-column reads
    col = frame.column(col_name)
    non_null = col.drop_nulls()
    non_null_len = len(non_null)
    n_unique = non_null.n_unique() if non_null_len > 0 else 0
    null_count = col.null_count()
    cp = ColumnProfile(
        name=col_name,
        # OWNED dtype contract: neutral vocabulary (str/int/uint/float/date/
        # datetime/bool/other) via the Arrow seam, not raw `str(pl.dtype)`.
        inferred_type=col.dtype_repr(),
        null_count=null_count,
        null_pct=null_count / row_count if row_count > 0 else 0,
        unique_count=n_unique,
        unique_pct=n_unique / non_null_len if non_null_len > 0 else 0,
        row_count=row_count,
    )
    findings: list[Finding] = []
    for profiler in COLUMN_PROFILERS:
        try:
            findings.extend(profiler.profile(sample, col_name, context=ctx))
        except Exception as e:  # noqa: BLE001 - one profiler failing must not sink the scan
            logger.warning("Profiler %s failed on column %s: %s", type(profiler).__name__, col_name, e)
    return cp, findings


def _run_relation(profiler, sample) -> list[Finding]:
    """Run ONE relation profiler over the whole (sampled) frame. Each relation
    profiler is independent and only READS the frame (its column caches are already
    populated by the column loop; it builds any derived data locally), so relations
    are safe to run concurrently -- results are merged in RELATION_PROFILERS order."""
    try:
        return list(profiler.profile(sample))
    except Exception as e:  # noqa: BLE001 - one relation profiler failing must not sink the scan
        logger.warning("Relation profiler %s failed: %s", type(profiler).__name__, e)
        return []


def _scan_dataframe_impl(
    frame,
    *,
    file_path: str,
    sample_size: int,
    return_sample: bool,
    domain: str | None,
    baseline: BaselineProfile | Path | None,
    schema: object | None,
    deep: bool = False,
    denial: bool = False,
) -> tuple[list[Finding], DatasetProfile] | tuple[list[Finding], DatasetProfile, object]:
    from goldencheck.core.frame import to_frame

    frame = to_frame(frame)   # idempotent; normalises to a seam Frame
    columns = frame.columns
    row_count = frame.height
    # Deep mode profiles the FULL population (no 100K sample cap) -- removes
    # sampling error on cardinality / uniqueness / rare-value / composite-key
    # checks. Heavier, but the seam profilers are vectorized and the native
    # kernels carry the CPU-bound work.
    sample = frame if deep else maybe_sample(frame, max_rows=sample_size)
    logger.info(
        "Scanning %d rows, %d columns%s",
        row_count, len(columns), " (deep: full population)" if deep else "",
    )

    all_findings: list[Finding] = []
    column_profiles: list[ColumnProfile] = []

    # Column profiling is embarrassingly parallel across columns: each column's
    # ColumnProfile + COLUMN_PROFILERS depend only on that column (profiler_context
    # is keyed by column -- type_inference writes context[col], range_distribution
    # reads context[col] -- so a per-column context has no cross-column dependency),
    # and pyarrow.compute / the native kernels release the GIL. We run columns on a
    # thread pool and merge results in COLUMN ORDER (not completion order) so the
    # finding set is identical to the sequential path (the differential enforces it).
    n_threads = _scan_threads(len(columns), row_count)
    if n_threads > 1:
        # Pre-populate the frame column caches single-threaded so the parallel
        # tasks only READ them -- concurrent dict writes (ArrowFrame._col_cache)
        # aren't safe. combine_chunks runs here, once per column. No-op for frames
        # whose column() doesn't cache.
        for col_name in columns:
            frame.column(col_name)
            if sample is not frame:
                sample.column(col_name)
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            results = list(pool.map(lambda c: _profile_column(frame, sample, c, row_count), columns))
    else:
        results = [_profile_column(frame, sample, c, row_count) for c in columns]
    for cp, findings in results:
        column_profiles.append(cp)
        all_findings.extend(findings)

    # Relation profilers are independent of each other and only READ the (now
    # cache-populated) frame, so fan them across the same thread pool and merge in
    # RELATION_PROFILERS order (deterministic, byte-identical to sequential).
    if n_threads > 1 and len(RELATION_PROFILERS) > 1:
        with ThreadPoolExecutor(max_workers=min(n_threads, len(RELATION_PROFILERS))) as pool:
            rel_results = list(pool.map(lambda pr: _run_relation(pr, sample), RELATION_PROFILERS))
        for fs in rel_results:
            all_findings.extend(fs)
    else:
        for profiler in RELATION_PROFILERS:
            all_findings.extend(_run_relation(profiler, sample))

    # Denial-constraint discovery is opt-in (NOT part of RELATION_PROFILERS) --
    # it is a heavier cross-column miner. Under --deep it sees the full
    # population; otherwise the same sample every other profiler ran on.
    if denial:
        from goldencheck.denial.mine import DenialConstraintProfiler
        target = frame if deep else sample
        try:
            all_findings.extend(DenialConstraintProfiler().profile(_to_polars(target)))
        except Exception as e:
            logger.warning("DenialConstraintProfiler failed: %s", e)

    from goldencheck.semantic.classifier import classify_columns, load_type_defs
    from goldencheck.semantic.suppression import apply_suppression as apply_type_suppression

    # Classify columns (load type defs once, pass to both classify and suppress)
    type_defs = load_type_defs(domain=domain)

    # If a schema was provided, use canonical types from the schema for known
    # columns; only fall back to header-heuristic classify_columns for columns
    # the schema flagged as unmapped. Emit one unmapped_column finding per
    # unknown column.
    if schema is not None:
        from goldencheck_types import InferredSchema
        if not isinstance(schema, InferredSchema):
            raise TypeError(
                f"scan_file(schema=) expected InferredSchema, got {type(schema).__name__}",
            )
        from goldencheck.semantic.classifier import ColumnClassification
        schema_types: dict[str, ColumnClassification] = {}
        unmapped_cols: list[str] = []
        for col, mapping in schema.fields.items():
            if mapping.is_unknown:
                unmapped_cols.append(col)
            else:
                schema_types[col] = ColumnClassification(
                    type_name=mapping.type, source="schema"
                )
        if unmapped_cols:
            cols_in_sample = [c for c in unmapped_cols if c in sample.columns]
            if cols_in_sample:
                heuristic = classify_columns(sample, type_defs=type_defs, only=cols_in_sample)
            else:
                heuristic = {}
        else:
            heuristic = {}
        column_types = {**schema_types, **heuristic}
        for col in unmapped_cols:
            all_findings.append(
                Finding(
                    severity=Severity.INFO,
                    column=col,
                    check="unmapped_column",
                    message=(
                        f"Column {col!r} could not be typed against domain pack "
                        f"{schema.domain!r}. Consider adding name_hints to the pack."
                    ),
                    confidence=1.0,
                )
            )
    else:
        column_types = classify_columns(sample, type_defs=type_defs)

    # Apply type suppression BEFORE corroboration boost
    all_findings = apply_type_suppression(all_findings, column_types, type_defs)

    # Post-classification checks: detect issues that require semantic type knowledge
    all_findings = _post_classification_checks(sample, all_findings, column_types)

    # Apply learned LLM rules if available
    rules_path = Path("goldencheck_rules.json")
    if rules_path.exists():
        try:
            from goldencheck.llm.rule_generator import apply_rules, load_rules
            rules = load_rules(rules_path)
            if rules:
                rule_findings = apply_rules(_to_polars(sample), rules)
                all_findings.extend(rule_findings)
                logger.info("Applied %d learned rules, got %d findings", len(rules), len(rule_findings))
        except Exception as e:
            logger.warning("Failed to apply learned rules: %s", e)

    # Apply baseline confidence priors BEFORE corroboration boost
    if baseline is not None:
        # Load if path was supplied
        if isinstance(baseline, (Path, str)):
            from goldencheck.baseline import load_baseline
            baseline = load_baseline(baseline)
        # Blend raw confidence toward learned priors
        if hasattr(baseline, "confidence_priors") and baseline.confidence_priors:
            from goldencheck.baseline.priors import apply_prior
            for i, finding in enumerate(all_findings):
                check_priors = baseline.confidence_priors.get(finding.check, {})
                prior = check_priors.get(finding.column)
                if prior:
                    new_conf = apply_prior(finding.confidence, prior)
                    all_findings[i] = dataclasses.replace(finding, confidence=new_conf)

    all_findings = apply_corroboration_boost(all_findings)

    # Run drift detection AFTER corroboration boost
    if baseline is not None:
        from goldencheck.drift import run_drift_checks
        scan_basename = Path(file_path).name
        if baseline.source_filename and baseline.source_filename != scan_basename:
            # %r quotes both values so YAML-supplied filename can't smuggle
            # newlines / control chars into structured logs.
            logger.warning(
                "Baseline source %r doesn't match scan file %r",
                baseline.source_filename, scan_basename,
            )
        drift_findings = run_drift_checks(_to_polars(sample), baseline)
        all_findings.extend(drift_findings)

    # Suppress PatternConsistencyProfiler findings for baseline-covered columns
    if baseline is not None and baseline.patterns:
        baseline_pattern_cols = set(baseline.patterns.keys())
        all_findings = [
            f for f in all_findings
            if not (f.check == "pattern_consistency" and f.column in baseline_pattern_cols)
        ]

    all_findings.sort(key=lambda f: f.severity, reverse=True)
    profile = DatasetProfile(file_path=file_path, row_count=row_count, column_count=len(columns), columns=column_profiles)
    if return_sample:
        return all_findings, profile, _sample_for_return(sample)
    return all_findings, profile


def scan_file_with_llm(
    path: Path,
    provider: str = "anthropic",
    sample_size: int = 100_000,
    domain: str | None = None,
    deep: bool = False,
) -> tuple[list[Finding], DatasetProfile]:
    """Scan a file with profilers, then enhance with LLM boost."""
    import json

    from goldencheck.llm.budget import CostReport, check_budget, estimate_cost
    from goldencheck.llm.merger import merge_llm_findings
    from goldencheck.llm.parser import parse_llm_response
    from goldencheck.llm.providers import DEFAULT_MODELS, call_llm, check_llm_available
    from goldencheck.llm.sample_block import build_sample_blocks

    # Check LLM is available BEFORE doing any work
    check_llm_available(provider)

    # Run profilers first — returns findings, profile, AND the sampled df
    findings, profile, sample = scan_file(
        path, sample_size=sample_size, return_sample=True, domain=domain, deep=deep
    )

    # Budget check before calling LLM (~2000 input, ~500 output as estimates)
    import os
    model = os.environ.get("GOLDENCHECK_LLM_MODEL", DEFAULT_MODELS.get(provider, ""))
    estimated_cost = estimate_cost(2000, 500, model)
    if not check_budget(estimated_cost):
        logger.warning("Skipping LLM boost due to budget constraint.")
        findings.sort(key=lambda f: f.severity, reverse=True)
        return findings, profile

    # Send all columns to LLM — it provides value even on high-confidence columns
    # by catching semantic issues profilers can't detect (encoding, checksums, cross-column logic)
    blocks = build_sample_blocks(sample, findings)

    # Build user prompt
    user_prompt = "Here is the dataset summary:\n\n" + json.dumps(blocks, indent=2, default=str)

    # Call LLM
    cost_report = CostReport()
    try:
        raw_response, input_tokens, output_tokens = call_llm(provider, user_prompt)
        cost_report.record(input_tokens, output_tokens, model)
        logger.info(
            "LLM boost cost: $%.4f (input: %d, output: %d, model: %s)",
            cost_report.cost_usd, input_tokens, output_tokens, model,
        )
        llm_response = parse_llm_response(raw_response)
        if llm_response:
            findings = merge_llm_findings(findings, llm_response)
            logger.info("LLM boost: merged %d column assessments, %d relations",
                       len(llm_response.columns), len(llm_response.relations))
        else:
            logger.warning("LLM response could not be parsed. Showing profiler-only results.")
    except SystemExit:
        raise
    except Exception as e:
        logger.warning("LLM boost failed: %s. Showing profiler-only results.", e)

    # Re-sort by severity
    findings.sort(key=lambda f: f.severity, reverse=True)
    return findings, profile
