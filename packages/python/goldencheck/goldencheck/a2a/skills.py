"""Skill dispatch for the GoldenCheck A2A server."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from goldencheck.agent.handoff import generate_handoff
from goldencheck.agent.intelligence import (
    build_alternatives,
    compare_domains,
    explain_finding,
    findings_to_fbc,
    select_strategy,
)
from goldencheck.agent.review_queue import ReviewQueue
from goldencheck.config.loader import load_config
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.fixer import apply_fixes
from goldencheck.engine.reader import read_file
from goldencheck.engine.scanner import scan_file
from goldencheck.engine.triage import auto_triage
from goldencheck.engine.validator import validate_file
from goldencheck.models.finding import Severity

logger = logging.getLogger(__name__)

__all__ = ["dispatch_skill"]

# Shared review queue (memory backend for the server lifetime)
_review_queue = ReviewQueue(backend="memory")


def _extract_params(message: dict) -> dict:
    """Pull structured params from the first data part of a message."""
    parts = message.get("parts", [])
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "data":
            return part.get("data", {})
        # Also accept a plain dict without the wrapper
        if isinstance(part, dict) and "type" not in part:
            return part
    return {}


def _finding_to_dict(f) -> dict:
    """Serialise a Finding to a JSON-safe dict."""
    return {
        "severity": f.severity.name,
        "column": f.column,
        "check": f.check,
        "message": f.message,
        "affected_rows": f.affected_rows,
        "sample_values": f.sample_values[:5],
        "suggestion": f.suggestion,
        "confidence": round(f.confidence, 4),
        "source": f.source,
    }


def _review_item_to_dict(item) -> dict:
    """Serialise a ReviewItem to a JSON-safe dict."""
    return {
        "item_id": item.item_id,
        "column": item.column,
        "check": item.check,
        "severity": item.severity,
        "confidence": round(item.confidence, 4),
        "message": item.message,
        "explanation": item.explanation,
        "status": item.status,
    }


# ---------------------------------------------------------------------------
# Skill handlers
# ---------------------------------------------------------------------------


def _handle_analyze_data(params: dict) -> dict:
    file_path = params.get("file_path", "")
    if not file_path:
        return {"error": "file_path is required"}

    df = read_file(Path(file_path))
    decision = select_strategy(df)
    alternatives = build_alternatives(decision, decision.why.get("domain_scores", {}))

    return {
        "domain": decision.domain,
        "domain_confidence": round(decision.domain_confidence, 4),
        "sample_strategy": decision.sample_strategy,
        "profiler_strategy": decision.profiler_strategy,
        "llm_boost": decision.llm_boost,
        "why": decision.why,
        "alternatives": alternatives,
    }


def _handle_scan(params: dict) -> dict:
    file_path = params.get("file_path", "")
    if not file_path:
        return {"error": "file_path is required"}

    domain = params.get("domain")
    job_name = params.get("job_name", f"a2a-{uuid.uuid4().hex[:8]}")

    findings, profile = scan_file(Path(file_path), domain=domain)
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    # Classify into review queue
    classified = _review_queue.classify_findings(findings, job_name)

    fbc = findings_to_fbc(findings)
    grade, score = profile.health_score(findings_by_column=fbc)

    return {
        "job_name": job_name,
        "row_count": profile.row_count,
        "column_count": profile.column_count,
        "health": {"grade": grade, "score": score},
        "total_findings": len(findings),
        "errors": sum(1 for f in findings if f.severity == Severity.ERROR),
        "warnings": sum(1 for f in findings if f.severity == Severity.WARNING),
        "infos": sum(1 for f in findings if f.severity == Severity.INFO),
        "auto_pinned": len(classified["pinned"]),
        "review_queue": len(classified["review"]),
        "auto_dismissed": len(classified["dismissed"]),
        "findings": [_finding_to_dict(f) for f in findings],
    }


def _handle_validate(params: dict) -> dict:
    file_path = params.get("file_path", "")
    config_path = params.get("config_path", "goldencheck.yml")
    if not file_path:
        return {"error": "file_path is required"}

    config = load_config(Path(config_path))
    if config is None:
        return {"error": f"Config not found at {config_path}"}

    findings = validate_file(Path(file_path), config)
    return {
        "total_findings": len(findings),
        "errors": sum(1 for f in findings if f.severity == Severity.ERROR),
        "warnings": sum(1 for f in findings if f.severity == Severity.WARNING),
        "findings": [_finding_to_dict(f) for f in findings],
    }


def _handle_explain(params: dict) -> dict:
    file_path = params.get("file_path", "")
    column = params.get("column", "")
    check = params.get("check", "")
    if not file_path or not column or not check:
        return {"error": "file_path, column, and check are required"}

    findings, profile = scan_file(Path(file_path))
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    target = next(
        (f for f in findings if f.column == column and f.check == check),
        None,
    )
    if target is None:
        return {"error": f"No finding for column={column!r}, check={check!r}"}

    return explain_finding(target, profile)


def _handle_review(params: dict) -> dict:
    job_name = params.get("job_name", "")
    action = params.get("action", "list")

    if action == "list":
        if not job_name:
            return {"error": "job_name is required for listing review items"}
        pending = _review_queue.pending(job_name)
        stats = _review_queue.stats(job_name)
        return {
            "job_name": job_name,
            "stats": stats,
            "pending": [_review_item_to_dict(item) for item in pending],
        }

    if action in ("approve", "reject"):
        item_id = params.get("item_id", "")
        decided_by = params.get("decided_by", "a2a-agent")
        reason = params.get("reason", "")
        if not item_id:
            return {"error": "item_id is required for approve/reject"}
        try:
            if action == "approve":
                _review_queue.approve(item_id, decided_by, reason)
            else:
                _review_queue.reject(item_id, decided_by, reason)
            return {"item_id": item_id, "action": action, "status": "ok"}
        except KeyError as exc:
            return {"error": str(exc)}

    return {"error": f"Unknown review action: {action!r}"}


def _handle_configure(params: dict) -> dict:
    file_path = params.get("file_path", "")
    if not file_path:
        return {"error": "file_path is required"}

    domain = params.get("domain")
    findings, profile = scan_file(Path(file_path), domain=domain)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    triage = auto_triage(findings)

    # Build a YAML-compatible config dict from pinned findings
    columns: dict[str, dict] = {}
    for f in triage.pin:
        col_cfg = columns.setdefault(f.column, {})
        if f.check == "nullability" and "required" not in col_cfg:
            col_cfg["required"] = True
        elif f.check == "uniqueness" and "unique" not in col_cfg:
            col_cfg["unique"] = True
        elif f.check == "range_distribution" and f.metadata:
            lo = f.metadata.get("expected_min")
            hi = f.metadata.get("expected_max")
            if lo is not None or hi is not None:
                col_cfg["range"] = [lo, hi]

    config_dict = {
        "version": 1,
        "columns": columns,
    }
    if domain:
        config_dict["domain"] = domain

    return {
        "config": config_dict,
        "pinned_count": len(triage.pin),
        "review_count": len(triage.review),
        "dismissed_count": len(triage.dismiss),
    }


def _handle_fix(params: dict) -> dict:
    file_path = params.get("file_path", "")
    mode = params.get("mode", "safe")
    if not file_path:
        return {"error": "file_path is required"}

    df = read_file(Path(file_path))
    findings, _profile = scan_file(Path(file_path))
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    # Dry-run: always force=False for safe/moderate, don't actually write
    try:
        _fixed_df, report = apply_fixes(df, findings, mode=mode)
    except ValueError as exc:
        return {"error": str(exc)}

    return {
        "mode": mode,
        "total_rows_fixed": report.total_rows_fixed,
        "fixes": [
            {
                "column": e.column,
                "fix_type": e.fix_type,
                "rows_affected": e.rows_affected,
                "sample_before": e.sample_before[:3],
                "sample_after": e.sample_after[:3],
            }
            for e in report.entries
        ],
    }


def _handle_compare_domains(params: dict) -> dict:
    file_path = params.get("file_path", "")
    if not file_path:
        return {"error": "file_path is required"}
    return compare_domains(file_path)


def _handle_handoff(params: dict) -> dict:
    file_path = params.get("file_path", "")
    job_name = params.get("job_name", f"a2a-{uuid.uuid4().hex[:8]}")
    if not file_path:
        return {"error": "file_path is required"}

    findings, profile = scan_file(Path(file_path))
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    triage = auto_triage(findings)
    pinned_rules = [
        {"column": f.column, "check": f.check, "message": f.message}
        for f in triage.pin
    ]

    return generate_handoff(
        file_path=file_path,
        findings=findings,
        profile=profile,
        pinned_rules=pinned_rules,
        review_pending=len(triage.review),
        dismissed=len(triage.dismiss),
        job_name=job_name,
    )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_SKILL_HANDLERS: dict[str, callable] = {
    "analyze_data": _handle_analyze_data,
    "scan": _handle_scan,
    "validate": _handle_validate,
    "explain": _handle_explain,
    "review": _handle_review,
    "configure": _handle_configure,
    "fix": _handle_fix,
    "compare_domains": _handle_compare_domains,
    "handoff": _handle_handoff,
}


def dispatch_skill(skill_id: str, message: dict) -> dict:
    """Route a skill request to the appropriate handler.

    Parameters
    ----------
    skill_id:
        One of the skill IDs from the agent card.
    message:
        The A2A message dict containing ``{role, parts}``.

    Returns
    -------
    dict
        The result payload (JSON-serialisable).
    """
    handler = _SKILL_HANDLERS.get(skill_id)
    if handler is None:
        return {"error": f"Unknown skill: {skill_id!r}"}

    params = _extract_params(message)
    try:
        return handler(params)
    except Exception:
        logger.exception("Skill %r failed", skill_id)
        raise
