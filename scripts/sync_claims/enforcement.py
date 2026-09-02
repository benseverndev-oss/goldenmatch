"""Does any test exercise a claimant alongside what it claims to mirror?

A claim is UNENFORCED when no single test file references both the claimant
and its target in EXECUTABLE code. That definition is load-bearing in both
directions:

  * EXECUTABLE, not textual. At 6c89042c7^ `tests/test_engine.py` named both
    `_run_pipeline` and `run_dedupe` -- in a docstring. Counting text marks
    the motivating incident enforced and the whole phase misses it. Counting
    Name/Attribute/alias nodes: 2 tests referenced the claimant, 10 the
    target, 0 both.

  * SOUND AS A NEGATIVE, SUGGESTIVE AS A POSITIVE. No co-reference genuinely
    proves nothing compares them. Co-reference proves only that one file
    mentions both -- never that it compares them. So the finding is the
    unenforced set, and a co-referenced claim is UNVERIFIED, never "safe".
"""

from __future__ import annotations

import ast
from pathlib import Path

from sync_claims.claims import Claim


def executable_references(path: Path) -> set[str]:
    """Names this file references in code. Docstrings and comments are not code."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.alias):
            out.add((node.asname or node.name).split(".")[-1])
    return out


def test_reference_sets(tests_root: Path) -> list[set[str]]:
    """One reference set per test file. Empty list means nothing was scanned."""
    return [executable_references(p) for p in sorted(tests_root.rglob("*.py"))]


# Named `test_reference_sets` per the required interface, but it is a helper,
# not a pytest test. Once a caller imports it into a test module, its
# module-level name matches pytest's default `test_*` collection and pytest
# tries to run it as a test (and fails: it takes a required `tests_root` arg,
# not a fixture). `__test__ = False` is pytest's documented escape hatch.
test_reference_sets.__test__ = False


def unenforced(claim_list: list[Claim], reference_sets: list[set[str]]) -> list[Claim]:
    """Claims no single test file references both halves of.

    Claims with no resolved target are excluded: there is nothing for a test to
    enforce them against, and counting them would inflate the finding list with
    items nobody can act on. They are reported in their own bucket instead.

    Module-level claims (`kind != "symbol"`) are excluded too, and for a
    sharper reason than "out of scope": `claim.symbol` for a module-level
    claim is the literal string `"<module>"`, which can never appear in a
    test's reference set (no test references a module by that name). So
    every RESOLVABLE module-level claim would become a permanent, unfixable
    finding -- measured on the real package, 213 findings with no filter
    versus 172 scoped, 41 of them module-level. The spec excludes
    module-level claims from triage by construction (a module has no single
    symbol a test can reference); this filter is what makes that true here
    rather than only in whichever caller remembers to pre-scope its input.
    `inventory()` already filters to `kind == "symbol"` before calling this,
    so the check is a no-op for the shipped caller today -- it exists for
    every future one that doesn't pre-scope so carefully, and matters
    concretely once C3 seeds a ratchet floor from a call here.
    """
    out: list[Claim] = []
    for claim in claim_list:
        if claim.target is None:
            continue
        if claim.kind != "symbol":
            continue
        if not any(claim.symbol in names and claim.target in names for names in reference_sets):
            out.append(claim)
    return out
