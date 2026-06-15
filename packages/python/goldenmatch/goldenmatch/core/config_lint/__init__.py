"""Pre-flight config linter: data-shape sanity checks on the resolved config.

Deterministic, offline, fail-open. The rule registry is the single source of
truth for both the checks and the generated docs. See ANALYTICS.md sibling
ANALYTICS for the privacy posture; here the contract is: never mutate the
config, never raise, always carry a doc-anchored reason.
"""
from __future__ import annotations

import goldenmatch.core.config_lint.rules  # noqa: F401 - registers rules on import
from goldenmatch.core.config_lint.profile import build_lint_input
from goldenmatch.core.config_lint.registry import (
    REGISTRY,
    Finding,
    LintInput,
    LintRule,
    RuleHit,
    Severity,
    all_rules,
    lint,
    slugify,
)

__all__ = [
    "REGISTRY",
    "Finding",
    "LintInput",
    "LintRule",
    "RuleHit",
    "Severity",
    "build_lint_input",
    "lint",
    "all_rules",
    "slugify",
]
