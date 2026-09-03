"""Coverage-based enforcement: does any single test EXECUTE both a claim's
claimant and its target, whether or not either name appears in that test's
own source?

The text-reference check (`enforcement.py`) is not sound as a negative --
a test can compare two functions without naming either, by reaching them
through a caller. C1 confirmed this five times over, including one function
in this exact scope (`core/scorer.py:_alias_score_matrix`, reached through
`_fuzzy_score_matrix`) and a whole module
(`core/survivorship/native.py`, reached through `build_survivorship_native`).

This module answers the execution question instead: for a given claim, did
one single test function run code inside BOTH the claimant's own definition
and the target's own definition. Function-level granularity, not file-level
-- file-level would just move the "co-occurrence is not comparison" problem
from text to runtime rather than narrowing it.

WHAT THIS DOES NOT PROVE. Co-execution is not proof of comparison. A test
could run both functions without ever comparing their outputs, if both fire
inside one integration-shaped test for unrelated reasons. This is a real,
accepted residual gap -- narrower than the text-reference problem, not
eliminated. See docs/superpowers/specs/2026-09-03-coverage-based-enforcement-
design.md's Being Wrong section.
"""

from __future__ import annotations

import ast
from pathlib import Path

_EMPTY_CONTEXT = ""


def function_spans(root: Path) -> dict[str, list[tuple[str, int, int]]]:
    """Every function/method in every `.py` file under `root`, with its line
    range. Keys are module paths relative to `root`, posix-separated.

    Deliberately general -- every function, not a naming-convention subset.
    `parity_coverage.py:_py_function_spans` looks similar but answers a
    narrower, unrelated question (only names ending `_py`, Companion A's
    scope); that function is module-private and this one is not a call to
    it, it is the same AST technique applied to a different question.
    """
    out: dict[str, list[tuple[str, int, int]]] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix()
        spans: list[tuple[str, int, int]] = []
        _collect_spans(tree, [], spans)
        if spans:
            out[rel] = spans
    return out


def _collect_spans(node: ast.AST, prefix: list[str], out: list[tuple[str, int, int]]) -> None:
    """Walk `node`'s direct children, recursing into class/function bodies so
    nested functions and methods get dotted names (`Widget.method`) and their
    OWN enclosing scope's line range is not what gets recorded for them."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = ".".join([*prefix, child.name])
            out.append((name, child.lineno, child.end_lineno or child.lineno))
            _collect_spans(child, [*prefix, child.name], out)
        elif isinstance(child, ast.ClassDef):
            _collect_spans(child, [*prefix, child.name], out)


def function_contexts(
    coverage_db: Path,
    root: Path,
    spans: dict[str, list[tuple[str, int, int]]],
) -> dict[tuple[str, str], frozenset[str]]:
    """(module_path, qualified_name) -> the dynamic test contexts that
    executed any line inside that function, read from a combined `.coverage`
    SQLite file with `dynamic_context = "test_function"` data in it.

    The empty-string context marks lines executed at import/collection time
    with no active test -- filtered out, or every function in a module would
    spuriously share that context with every other.
    """
    import coverage

    data = coverage.CoverageData(basename=str(coverage_db))
    data.read()

    line_contexts_by_file: dict[str, dict[int, set[str]]] = {}
    for measured in data.measured_files():
        rel = _relative_to_root(measured, root)
        if rel is None:
            continue
        line_contexts_by_file[rel] = data.contexts_by_lineno(measured)

    out: dict[tuple[str, str], frozenset[str]] = {}
    for module, functions in spans.items():
        line_contexts = line_contexts_by_file.get(module)
        if not line_contexts:
            continue
        for name, start, end in functions:
            ctxs: set[str] = set()
            for lineno in range(start, end + 1):
                ctxs.update(line_contexts.get(lineno, ()))
            ctxs.discard(_EMPTY_CONTEXT)
            if ctxs:
                out[(module, name)] = frozenset(ctxs)
    return out


def _relative_to_root(measured_path: str, root: Path) -> str | None:
    """`coverage.py` reports measured files with whatever path shape the run
    that produced them used (absolute, or relative to that run's CWD) -- not
    guaranteed to match `root`-relative posix paths. Returns None for a file
    outside `root` rather than raising, since a coverage run's `source`
    scope and this scan's `root` are configured independently and are not
    guaranteed identical."""
    try:
        return Path(measured_path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def coverage_enforced(
    claimant_key: tuple[str, str],
    target_key: tuple[str, str],
    contexts: dict[tuple[str, str], frozenset[str]],
) -> bool:
    """True when some single test's context appears for BOTH keys."""
    return bool(contexts.get(claimant_key, frozenset()) & contexts.get(target_key, frozenset()))
