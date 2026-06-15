"""Config lint registry — the single source of truth for pre-flight config rules.

The same `LintRule` object that the engine uses to produce a `Finding` also
emits its row in the docs (`docgen.render_lint_docs` -> the generated
`docs-site/goldenmatch/config-linter.mdx`). So a finding's stated reason IS the
documented reason — they cannot drift, and CI regenerates + diffs the page.

This module is deterministic, offline, and fast: it consults only cheap
data-shape facts (`LintInput`) that the auto-config profiler already computes.
No network, no LLM, no model — that (opt-in) advisory layer sits ABOVE this and
never gates. Severity drives the gate policy (wired in a later change); the
engine itself only returns findings.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


def slugify(text: str) -> str:
    """GitHub/Mintlify-compatible heading slug, so a Finding's doc_anchor
    resolves to the exact heading docgen emits for that rule."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


class Severity(StrEnum):
    ERROR = "error"  # will OOM / collapse recall -> refuse (or warn-and-run behind a flag)
    WARN = "warn"    # likely suboptimal for this data shape
    INFO = "info"    # advisory


@dataclass(frozen=True)
class LintInput:
    """Cheap, data-shape facts a rule may consult. Mirrors the fields the
    auto-config profiler already computes (cardinality_ratio / null_rate /
    col_type), so rules reuse the exact same signals the controller uses."""
    row_count: int
    cardinality_ratio: dict[str, float]  # col -> unique/non-null (0-1)
    null_rate: dict[str, float]          # col -> null fraction (0-1)
    col_type: dict[str, str]             # col -> classified type
    available_ram_gb: float | None = None


@dataclass(frozen=True)
class RuleHit:
    """What a rule's check returns. Terse on purpose: the engine attaches the
    rule's id/severity/rationale/doc_anchor, guaranteeing every finding carries
    the canonical (== documented) reason."""
    message: str                 # rendered with the specific numbers for THIS config/data
    target: str | None = None    # which matchkey / field / blocking key it concerns
    suggestion: str | None = None


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    message: str
    rationale: str               # the rule's canonical "why" == the generated doc text
    doc_anchor: str              # slug into config-linter.mdx (e.g. "config-linter#blocking-near-unique")
    target: str | None = None
    suggestion: str | None = None
    provenance: str = "deterministic"  # vs "llm:<model>" for the opt-in advisory layer


# check signature: (config, LintInput) -> list[RuleHit]
CheckFn = Callable[[object, LintInput], "list[RuleHit]"]


@dataclass(frozen=True)
class LintRule:
    id: str                  # stable dotted id, e.g. "blocking.near_unique"
    category: str            # "blocking" | "scoring" | "scale" | ...
    severity: Severity
    title: str               # short human title
    fires_when: str          # one-line condition, for the docs table
    rationale: str           # SOURCE OF TRUTH for the "why" — generates the doc body
    needs: tuple[str, ...]   # LintInput fields consulted (doc + future sample-gating)
    check: CheckFn

    @property
    def doc_anchor(self) -> str:
        # docgen emits "### {title}"; this resolves to that heading's slug.
        return "config-linter#" + slugify(self.title)


REGISTRY: dict[str, LintRule] = {}


def register(rule: LintRule) -> LintRule:
    if rule.id in REGISTRY:
        raise ValueError(f"duplicate lint rule id: {rule.id!r}")
    REGISTRY[rule.id] = rule
    return rule


def all_rules() -> list[LintRule]:
    """All registered rules, sorted deterministically (category, id) — the order
    the engine evaluates and the docs render in."""
    return sorted(REGISTRY.values(), key=lambda r: (r.category, r.id))


def lint(config: object, inp: LintInput) -> list[Finding]:
    """Run every registered rule against the resolved config + data shape.

    Pure: returns findings, never raises on rule logic (a rule that errors is
    skipped, not fatal — a linter must never break the run it guards) and never
    mutates the config. Refuse/heal policy lives in the caller."""
    out: list[Finding] = []
    for rule in all_rules():
        try:
            hits = rule.check(config, inp)
        except Exception:  # noqa: BLE001 - one bad rule must not break linting
            continue
        for hit in hits:
            out.append(Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=hit.message,
                rationale=rule.rationale,
                doc_anchor=rule.doc_anchor,
                target=hit.target,
                suggestion=hit.suggestion,
            ))
    return out
