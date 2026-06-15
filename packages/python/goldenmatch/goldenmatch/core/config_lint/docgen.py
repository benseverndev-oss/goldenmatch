"""Generate the config-linter docs FROM the rule registry.

The registry is the single source of truth: this renders the canonical
`docs-site/goldenmatch/config-linter.mdx`. A finding's `rationale` and the
doc body are the SAME string, so they cannot drift. CI regenerates and diffs
the committed page (see scripts/gen_lint_docs.py --check + tests/test_config_lint.py).
"""
from __future__ import annotations

from pathlib import Path

import goldenmatch.core.config_lint.rules  # noqa: F401 - populates the registry
from goldenmatch.core.config_lint.registry import all_rules

DOC_PATH = (
    Path(__file__).resolve().parents[6]
    / "docs-site" / "goldenmatch" / "config-linter.mdx"
)

_HEADER = """\
---
title: "Config linter"
description: "Pre-flight checks the config linter runs against your data shape before a dedupe/match run — what each rule flags, when it fires, and why. Generated from the rule registry; do not edit by hand."
---

{/* GENERATED FILE — do not edit. Source of truth: goldenmatch/core/config_lint/rules.py. Regenerate: python scripts/gen_lint_docs.py --write */}

The config linter sanity-checks the **resolved** config (zero-config *or* user-submitted) against your data's shape before the pipeline runs, so degenerate configs are caught up front instead of failing slowly. Each finding links back to the rule below; the rule's reason here is the exact reason the linter reports.

Severities: **error** (will OOM or collapse recall), **warn** (likely suboptimal for this data shape), **info** (advisory).
"""

_CATEGORY_TITLES = {
    "blocking": "Blocking",
    "scoring": "Scoring",
    "scale": "Scale & backend",
}


def render_lint_docs() -> str:
    lines: list[str] = [_HEADER]
    current = None
    for rule in all_rules():
        if rule.category != current:
            current = rule.category
            lines.append(f"\n## {_CATEGORY_TITLES.get(current, current.title())}\n")
        lines.append(f"### {rule.title}\n")
        lines.append(f"**`{rule.id}`** · severity **{rule.severity.value}** · fires when {rule.fires_when}.\n")
        lines.append(f"{rule.rationale}\n")
    return "\n".join(lines).rstrip() + "\n"


def write_docs() -> Path:
    DOC_PATH.write_text(render_lint_docs(), encoding="utf-8")
    return DOC_PATH


def docs_are_current() -> bool:
    if not DOC_PATH.exists():
        return False
    return DOC_PATH.read_text(encoding="utf-8") == render_lint_docs()
