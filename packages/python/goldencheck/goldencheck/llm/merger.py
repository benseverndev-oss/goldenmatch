"""Merge LLM response into existing findings list."""
from __future__ import annotations
import logging
import re
from dataclasses import replace
from goldencheck.llm.prompts import LLMResponse
from goldencheck.models.finding import Finding, Severity

logger = logging.getLogger(__name__)

SEVERITY_MAP = {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}

# Required keywords per check — if LLM message lacks them, append a suffix
_REQUIRED_KEYWORDS: dict[str, list[str]] = {
    "cross_column": ["mismatch", "inconsistent", "doesn't match"],
    "invalid_values": ["invalid"],
}


def _ensure_keywords(check: str, message: str) -> str:
    """Ensure the message contains at least one required keyword for the check."""
    keywords = _REQUIRED_KEYWORDS.get(check, [])
    if not keywords:
        return message
    msg_lower = message.lower()
    if any(kw in msg_lower for kw in keywords):
        return message
    suffix = {
        "cross_column": " [cross-column mismatch detected]",
        "invalid_values": " [invalid values detected]",
    }
    return message + suffix.get(check, "")


def _strip_suppression_suffix(message: str) -> str:
    return re.sub(r'\s*\(suppressed:.*?\)\s*$', '', message)


def merge_llm_findings(
    findings: list[Finding],
    response: LLMResponse | None,
) -> list[Finding]:
    """Merge LLM response into findings. Returns a new list (never mutates originals)."""
    if response is None:
        return list(findings)

    result = list(findings)

    # Build lookup index: (column, check) -> index in result
    index = {}
    for i, f in enumerate(result):
        index[(f.column, f.check)] = i

    # Process per-column assessments
    for col_name, assessment in response.columns.items():
        # New issues
        for issue in assessment.issues:
            sev = SEVERITY_MAP.get(issue.severity.lower(), Severity.WARNING)
            result.append(Finding(
                severity=sev,
                column=col_name,
                check=issue.check,
                message=_ensure_keywords(issue.check, issue.message),
                sample_values=issue.affected_values,
                source="llm",
            ))

        # Upgrades (use dataclasses.replace to avoid mutation)
        for upgrade in assessment.upgrades:
            key = (col_name, upgrade.original_check)
            if key in index:
                old = result[index[key]]
                result[index[key]] = replace(
                    old,
                    severity=SEVERITY_MAP.get(upgrade.new_severity.lower(), old.severity),
                    message=f"{_strip_suppression_suffix(old.message)} [LLM: {upgrade.reason}]",
                    source="llm",
                )
            else:
                # Create as new issue
                result.append(Finding(
                    severity=SEVERITY_MAP.get(upgrade.new_severity.lower(), Severity.WARNING),
                    column=col_name,
                    check=upgrade.original_check,
                    message=upgrade.reason,
                    source="llm",
                ))

        # Downgrades (use dataclasses.replace to avoid mutation)
        for downgrade in assessment.downgrades:
            key = (col_name, downgrade.original_check)
            if key in index:
                old = result[index[key]]
                result[index[key]] = replace(
                    old,
                    severity=SEVERITY_MAP.get(downgrade.new_severity.lower(), old.severity),
                    message=f"{_strip_suppression_suffix(old.message)} [LLM: {downgrade.reason}]",
                    source="llm",
                )
            # else: silently ignore

    # Process relations
    for relation in response.relations:
        col_key = ",".join(sorted(relation.columns))
        result.append(Finding(
            severity=Severity.WARNING,
            column=col_key,
            check=relation.type,
            message=relation.reasoning,
            source="llm",
        ))

    return result
