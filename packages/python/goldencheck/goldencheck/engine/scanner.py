"""Scanner — orchestrates all profilers and collects findings."""
from __future__ import annotations
import logging
from pathlib import Path
import polars as pl
from goldencheck.engine.reader import read_file
from goldencheck.engine.sampler import maybe_sample
from goldencheck.engine.confidence import apply_corroboration_boost
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import ColumnProfile, DatasetProfile
from goldencheck.profilers.type_inference import TypeInferenceProfiler
from goldencheck.profilers.nullability import NullabilityProfiler
from goldencheck.profilers.uniqueness import UniquenessProfiler
from goldencheck.profilers.format_detection import FormatDetectionProfiler
from goldencheck.profilers.range_distribution import RangeDistributionProfiler
from goldencheck.profilers.cardinality import CardinalityProfiler
from goldencheck.profilers.pattern_consistency import PatternConsistencyProfiler
from goldencheck.profilers.encoding_detection import EncodingDetectionProfiler
from goldencheck.profilers.sequence_detection import SequenceDetectionProfiler
from goldencheck.profilers.drift_detection import DriftDetectionProfiler
from goldencheck.relations.temporal import TemporalOrderProfiler
from goldencheck.relations.null_correlation import NullCorrelationProfiler
from goldencheck.relations.numeric_cross import NumericCrossColumnProfiler

logger = logging.getLogger(__name__)

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
]

RELATION_PROFILERS = [
    TemporalOrderProfiler(),
    NullCorrelationProfiler(),
    NumericCrossColumnProfiler(),
]

def _post_classification_checks(
    sample: pl.DataFrame,
    findings: list[Finding],
    column_types: dict,
) -> list[Finding]:
    """Add findings that require semantic type knowledge."""
    new_findings = list(findings)

    for col_name, classification in column_types.items():
        if classification.type_name != "person_name":
            continue
        if col_name not in sample.columns:
            continue
        col = sample[col_name]
        if col.dtype not in (pl.Utf8, pl.String):
            continue

        # Detect digit characters in person name columns
        non_null = col.drop_nulls()
        if len(non_null) == 0:
            continue

        digit_mask = non_null.str.contains(r"\d")
        digit_count = int(digit_mask.sum())
        if digit_count > 0:
            digit_pct = digit_count / len(non_null)
            # Only flag if it's a minority (< 10%) — widespread digits means it's not really a name column
            if 0 < digit_pct < 0.10:
                sample_vals = non_null.filter(digit_mask).head(5).to_list()
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

    return new_findings


def scan_file(
    path: Path,
    sample_size: int = 100_000,
    return_sample: bool = False,
) -> tuple[list[Finding], DatasetProfile] | tuple[list[Finding], DatasetProfile, pl.DataFrame]:
    df = read_file(path)
    row_count = len(df)
    sample = maybe_sample(df, max_rows=sample_size)
    logger.info("Scanning %d rows, %d columns", row_count, len(df.columns))

    all_findings: list[Finding] = []
    column_profiles: list[ColumnProfile] = []
    profiler_context: dict = {}

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
                findings = profiler.profile(sample, col_name, context=profiler_context)
                all_findings.extend(findings)
            except Exception as e:
                logger.warning("Profiler %s failed on column %s: %s", type(profiler).__name__, col_name, e)

    for profiler in RELATION_PROFILERS:
        try:
            findings = profiler.profile(sample)
            all_findings.extend(findings)
        except Exception as e:
            logger.warning("Relation profiler %s failed: %s", type(profiler).__name__, e)

    from goldencheck.semantic.classifier import classify_columns, load_type_defs
    from goldencheck.semantic.suppression import apply_suppression as apply_type_suppression

    # Classify columns
    column_types = classify_columns(sample)
    type_defs = load_type_defs()

    # Apply type suppression BEFORE corroboration boost
    all_findings = apply_type_suppression(all_findings, column_types, type_defs)

    # Post-classification checks: detect issues that require semantic type knowledge
    all_findings = _post_classification_checks(sample, all_findings, column_types)

    # Apply learned LLM rules if available
    rules_path = Path("goldencheck_rules.json")
    if rules_path.exists():
        try:
            from goldencheck.llm.rule_generator import load_rules, apply_rules
            rules = load_rules(rules_path)
            if rules:
                rule_findings = apply_rules(sample, rules)
                all_findings.extend(rule_findings)
                logger.info("Applied %d learned rules, got %d findings", len(rules), len(rule_findings))
        except Exception as e:
            logger.warning("Failed to apply learned rules: %s", e)

    all_findings = apply_corroboration_boost(all_findings)
    all_findings.sort(key=lambda f: f.severity, reverse=True)
    profile = DatasetProfile(file_path=str(path), row_count=row_count, column_count=len(df.columns), columns=column_profiles)
    if return_sample:
        return all_findings, profile, sample
    return all_findings, profile


def scan_file_with_llm(
    path: Path,
    provider: str = "anthropic",
    sample_size: int = 100_000,
) -> tuple[list[Finding], DatasetProfile]:
    """Scan a file with profilers, then enhance with LLM boost."""
    import json
    from goldencheck.llm.sample_block import build_sample_blocks
    from goldencheck.llm.providers import call_llm, check_llm_available
    from goldencheck.llm.parser import parse_llm_response
    from goldencheck.llm.merger import merge_llm_findings
    from goldencheck.llm.budget import CostReport, estimate_cost, check_budget
    from goldencheck.llm.providers import DEFAULT_MODELS

    # Check LLM is available BEFORE doing any work
    check_llm_available(provider)

    # Run profilers first — returns findings, profile, AND the sampled df
    findings, profile, sample = scan_file(path, sample_size=sample_size, return_sample=True)

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
