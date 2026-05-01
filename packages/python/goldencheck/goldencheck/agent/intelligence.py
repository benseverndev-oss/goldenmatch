"""Intelligence layer — strategy selection, finding explanation, domain comparison."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.sampler import maybe_sample
from goldencheck.engine.scanner import scan_file
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import ColumnProfile, DatasetProfile
from goldencheck.semantic.classifier import (
    classify_columns,
    list_available_domains,
    load_type_defs,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AgentSession",
    "StrategyDecision",
    "select_strategy",
    "build_alternatives",
    "explain_finding",
    "explain_column",
    "compare_domains",
    "findings_to_fbc",
]

SEVERITY_LABELS = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "info"}

PREVIEW_ROWS = 10_000


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AgentSession:
    """Mutable state for a single agent run."""

    sample: pl.DataFrame | None = None
    profile: DatasetProfile | None = None
    findings: list[Finding] = field(default_factory=list)
    review_queue: "ReviewQueue | None" = None  # noqa: F821 — forward ref
    reasoning: dict = field(default_factory=dict)
    job_name: str = ""


@dataclass
class StrategyDecision:
    """Output of select_strategy — captures what we decided and why."""

    domain: str | None = None
    domain_confidence: float = 0.0
    sample_strategy: str = "full"
    profiler_strategy: str = "standard"
    llm_boost: bool = False
    why: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


def _detect_domain(preview: pl.DataFrame) -> tuple[str | None, float, dict[str, float]]:
    """Score every available domain against *preview* and return the best."""
    available = list_available_domains()
    if not available:
        return None, 0.0, {}

    scores: dict[str, float] = {}
    for domain_name in available:
        type_defs = load_type_defs(domain=domain_name)
        col_types = classify_columns(preview, type_defs=type_defs)
        matched = sum(1 for c in col_types.values() if c.type_name is not None)
        total = max(len(col_types), 1)
        scores[domain_name] = matched / total

    if not scores:
        return None, 0.0, scores

    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_score = scores[best]
    # Only pick a domain if it meaningfully matches (>20% columns)
    if best_score > 0.20:
        return best, best_score, scores
    return None, 0.0, scores


def _check_llm_available() -> bool:
    """Return True if any LLM provider is usable (non-fatal check)."""
    try:
        from goldencheck.llm.providers import check_llm_available

        for provider in ("anthropic", "openai"):
            try:
                check_llm_available(provider)
                return True
            except (SystemExit, Exception):
                continue
    except ImportError:
        pass
    return False


def select_strategy(df: pl.DataFrame) -> StrategyDecision:
    """Analyse *df* and decide how to scan it.

    Returns a ``StrategyDecision`` with domain, sampling, profiler, and LLM
    choices plus an explanation dict.
    """
    row_count = len(df)
    col_count = len(df.columns)

    # --- Preview sample for domain detection (fast, capped at 10k) ---
    preview = maybe_sample(df, max_rows=PREVIEW_ROWS)

    # --- Domain detection ---
    domain, domain_conf, domain_scores = _detect_domain(preview)

    # --- Sample strategy (based on total rows) ---
    if row_count <= 50_000:
        sample_strategy = "full"
    elif row_count <= 500_000:
        sample_strategy = "sample_100k"
    else:
        sample_strategy = "sample_100k_stratified"

    # --- Profiler strategy (based on column count) ---
    if col_count <= 20:
        profiler_strategy = "standard"
    elif col_count <= 80:
        profiler_strategy = "parallel_batched"
    else:
        profiler_strategy = "wide_table"

    # --- LLM availability ---
    llm_available = _check_llm_available()

    why = {
        "row_count": row_count,
        "col_count": col_count,
        "preview_rows": len(preview),
        "domain_scores": domain_scores,
        "llm_available": llm_available,
        "sample_strategy_reason": (
            f"{row_count} rows -> {sample_strategy}"
        ),
        "profiler_strategy_reason": (
            f"{col_count} columns -> {profiler_strategy}"
        ),
    }

    return StrategyDecision(
        domain=domain,
        domain_confidence=domain_conf,
        sample_strategy=sample_strategy,
        profiler_strategy=profiler_strategy,
        llm_boost=llm_available,
        why=why,
    )


# ---------------------------------------------------------------------------
# Alternatives builder
# ---------------------------------------------------------------------------


def build_alternatives(
    decision: StrategyDecision,
    domain_scores: dict[str, float],
) -> list[dict]:
    """Build a ranked list of alternative strategies the user could try."""
    alts: list[dict] = []

    # Suggest runner-up domains
    for name, score in sorted(domain_scores.items(), key=lambda x: x[1], reverse=True):
        if name == decision.domain:
            continue
        if score > 0.10:
            alts.append({
                "type": "domain",
                "value": name,
                "score": round(score, 3),
                "reason": f"Domain '{name}' matched {score:.0%} of columns",
            })

    # Suggest LLM boost if not already enabled
    if not decision.llm_boost:
        alts.append({
            "type": "llm_boost",
            "value": True,
            "reason": "Install LLM extras (pip install goldencheck[llm]) for deeper analysis",
        })

    # Suggest no-domain scan if a domain was picked
    if decision.domain is not None:
        alts.append({
            "type": "domain",
            "value": None,
            "score": 0.0,
            "reason": "Run without a domain pack for generic analysis",
        })

    return alts


# ---------------------------------------------------------------------------
# Finding / column explanation
# ---------------------------------------------------------------------------


def explain_finding(finding: Finding, profile: DatasetProfile) -> dict:
    """Return a natural-language explanation dict for a single finding."""
    col_profile: ColumnProfile | None = next(
        (c for c in profile.columns if c.name == finding.column), None
    )

    severity_label = SEVERITY_LABELS.get(finding.severity, "unknown")
    conf_label = (
        "high" if finding.confidence >= 0.8
        else "medium" if finding.confidence >= 0.5
        else "low"
    )

    what = (
        f"The '{finding.check}' check found an issue in column '{finding.column}': "
        f"{finding.message}"
    )

    impact_parts = [f"Severity is {severity_label} (confidence: {conf_label})."]
    if finding.affected_rows:
        if col_profile and col_profile.row_count > 0:
            pct = finding.affected_rows / col_profile.row_count
            impact_parts.append(
                f"Affects {finding.affected_rows:,} row(s) "
                f"({pct:.1%} of {col_profile.row_count:,} total)."
            )
        else:
            impact_parts.append(f"Affects {finding.affected_rows:,} row(s).")

    suggestion = finding.suggestion or "Review the flagged values and correct or confirm them."

    result: dict = {
        "what": what,
        "severity": severity_label,
        "confidence": round(finding.confidence, 3),
        "impact": " ".join(impact_parts),
        "suggestion": suggestion,
        "affected_rows": finding.affected_rows,
    }
    if finding.sample_values:
        result["sample_values"] = finding.sample_values[:5]
    if col_profile:
        result["column_type"] = col_profile.inferred_type
        result["column_null_pct"] = round(col_profile.null_pct, 4)
    return result


def explain_column(file_path: str, column: str) -> dict:
    """Deep-dive explanation for a single column's health."""
    path = Path(file_path)
    findings, profile = scan_file(path)
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    col_findings = [f for f in findings if f.column == column]
    col_profile: ColumnProfile | None = next(
        (c for c in profile.columns if c.name == column), None
    )

    errors = sum(1 for f in col_findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in col_findings if f.severity == Severity.WARNING)
    infos = sum(1 for f in col_findings if f.severity == Severity.INFO)

    # Build narrative
    if errors > 0:
        health = "unhealthy"
    elif warnings > 0:
        health = "needs attention"
    else:
        health = "healthy"

    narrative = f"Column '{column}' is {health}."
    if col_profile:
        narrative += (
            f" Type: {col_profile.inferred_type},"
            f" {col_profile.null_pct:.1%} null,"
            f" {col_profile.unique_pct:.1%} unique."
        )
    if errors:
        narrative += f" {errors} error(s) detected."
    if warnings:
        narrative += f" {warnings} warning(s) detected."

    explained = [explain_finding(f, profile) for f in col_findings]

    return {
        "column": column,
        "health": health,
        "narrative": narrative,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "findings": explained,
        "profile": {
            "type": col_profile.inferred_type if col_profile else None,
            "null_pct": round(col_profile.null_pct, 4) if col_profile else None,
            "unique_pct": round(col_profile.unique_pct, 4) if col_profile else None,
            "row_count": col_profile.row_count if col_profile else None,
        },
    }


# ---------------------------------------------------------------------------
# Domain comparison
# ---------------------------------------------------------------------------


def findings_to_fbc(findings: list[Finding]) -> dict[str, dict[str, int]]:
    """Convert a findings list to a findings-by-column dict for health_score."""
    fbc: dict[str, dict[str, int]] = {}
    for f in findings:
        bucket = fbc.setdefault(f.column, {"errors": 0, "warnings": 0})
        if f.severity == Severity.ERROR:
            bucket["errors"] += 1
        elif f.severity == Severity.WARNING:
            bucket["warnings"] += 1
    return fbc


def compare_domains(file_path: str) -> dict:
    """Scan *file_path* with each available domain (+ base) and compare."""
    path = Path(file_path)
    available = list_available_domains()
    candidates = [None, *available]  # None = no domain (base)

    results: dict[str, dict] = {}
    for domain in candidates:
        label = domain or "base"
        findings, profile = scan_file(path, domain=domain)
        findings = apply_confidence_downgrade(findings, llm_boost=False)

        fbc = findings_to_fbc(findings)
        grade, score = profile.health_score(findings_by_column=fbc)

        errors = sum(1 for f in findings if f.severity == Severity.ERROR)
        warnings = sum(1 for f in findings if f.severity == Severity.WARNING)

        results[label] = {
            "grade": grade,
            "score": score,
            "errors": errors,
            "warnings": warnings,
            "total_findings": len(findings),
        }

    # Determine recommendation
    best_label = max(results, key=lambda k: results[k]["score"])

    return {
        "domains_tested": [d or "base" for d in candidates],
        "results": results,
        "recommendation": best_label,
        "reason": (
            f"'{best_label}' achieves the highest health score "
            f"({results[best_label]['score']}) with fewest false positives."
        ),
    }
