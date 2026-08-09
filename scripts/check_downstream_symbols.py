#!/usr/bin/env python3
"""Reconcile out-of-workspace consumers' references to `goldenmatch` against what
goldenmatch actually exports.

WHY THIS EXISTS
---------------
A handful of packages are deliberately EXCLUDED from the uv workspace (heavy or
conflicting dep trees) and install `goldenmatch` from monorepo SOURCE on their own
CI lane. Those lanes are path-filtered on the CONSUMER's paths, so a goldenmatch
change does not trigger them -- and goldenmatch is the most-churned package in the
repo. The result is a one-way break nobody sees.

That is not hypothetical. `goldenmatch` replaced its FAISS ANN backend with native
HNSW, deleting `core.ann_blocker._HAS_FAISS`. Three goldengraph fixtures did
`monkeypatch.setattr(ann_blocker, "_HAS_FAISS", False)`; monkeypatch raises on a
missing attribute, the fixtures were autouse, and 20 tests errored. The lane did
not run for 15 days -- it had no reason to -- and the break surfaced only when an
unrelated PR happened to touch a goldengraph path.

This gate is the PR-time half of the fix (the other half is a nightly schedule on
those lanes, which catches behavioural breaks a symbol scan cannot see). It runs on
the always-on lane, so the PR that deletes a depended-on symbol fails immediately.

HOW
---
Consumer references are DERIVED by AST, never hand-maintained -- a hand-kept list
is the thing that rots. Three reference shapes are recognised:

  1. ``from goldenmatch.x import a, b``      -> module importable, `a`/`b` exist
  2. ``import goldenmatch.x``                -> module importable
  3. ``monkeypatch.setattr(m, "NAME", ...)`` -> `NAME` exists on the module `m`
     is bound to. This is shape (3) specifically because it is invisible to the
     other two: the import succeeds and only the patch target is missing.

Resolution imports goldenmatch for real (this runs on a lane that has it), so it
reflects what a consumer would actually get.

FAIL (exit 1) on a reference goldenmatch does not satisfy. Exit 0 otherwise.

Run from the repo root:  python scripts/check_downstream_symbols.py
"""

from __future__ import annotations

import argparse
import ast
import importlib
import pathlib
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent
UPSTREAM = "goldenmatch"

# Packages OUTSIDE the uv workspace that install goldenmatch from source. Each has
# its own path-filtered lane, so none of them re-runs on a goldenmatch change.
# Add a package here when it starts consuming goldenmatch from source.
CONSUMERS = (
    "packages/python/goldengraph",
    "packages/python/goldenmatch-kg",
    "packages/python/goldenmatch/benchmarks/er-kg-bench",
)

# Attributes a consumer may reference that goldenmatch is NOT obliged to keep.
# Empty today, and it should stay that way: an entry here means a consumer is
# reaching for something upstream does not promise. Prefer fixing the consumer.
ALLOW: dict[str, str] = {}


@dataclass(frozen=True)
class Ref:
    """One consumer -> goldenmatch reference, with where it came from."""

    module: str  # dotted goldenmatch module, e.g. "goldenmatch.core.ann_blocker"
    attr: str | None  # attribute required on it, or None for "module must import"
    where: str  # "<relative path>:<lineno>"
    kind: str  # from-import | import | monkeypatch


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> goldenmatch module, for `import goldenmatch.x as y` forms.

    Needed to resolve shape (3): the monkeypatch target is a local alias, and we
    have to know which goldenmatch module it points at.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == UPSTREAM or a.name.startswith(UPSTREAM + "."):
                    aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom):
            # `from goldenmatch.core import ann_blocker` binds a MODULE name too.
            if node.module and (
                node.module == UPSTREAM or node.module.startswith(UPSTREAM + ".")
            ):
                for a in node.names:
                    aliases.setdefault(a.asname or a.name, f"{node.module}.{a.name}")
    return aliases


def _refs_in(path: pathlib.Path) -> list[Ref]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []
    rel = path.relative_to(ROOT).as_posix()
    aliases = _module_aliases(tree)
    refs: list[Ref] = []

    for node in ast.walk(tree):
        # (1) from goldenmatch.x import a, b
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == UPSTREAM or mod.startswith(UPSTREAM + "."):
                for a in node.names:
                    if a.name == "*":
                        refs.append(Ref(mod, None, f"{rel}:{node.lineno}", "from-import"))
                    else:
                        refs.append(
                            Ref(mod, a.name, f"{rel}:{node.lineno}", "from-import")
                        )

        # (2) import goldenmatch.x
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == UPSTREAM or a.name.startswith(UPSTREAM + "."):
                    refs.append(Ref(a.name, None, f"{rel}:{node.lineno}", "import"))

        # (3) monkeypatch.setattr(<alias>, "NAME", ...) -- the _HAS_FAISS shape.
        elif isinstance(node, ast.Call):
            fn = node.func
            if (
                isinstance(fn, ast.Attribute)
                and fn.attr == "setattr"
                and isinstance(fn.value, ast.Name)
                and fn.value.id.endswith("monkeypatch")
                and len(node.args) >= 2
            ):
                target, name = node.args[0], node.args[1]
                if isinstance(target, ast.Name) and isinstance(name, ast.Constant):
                    mod = aliases.get(target.id)
                    if mod and isinstance(name.value, str):
                        refs.append(
                            Ref(mod, name.value, f"{rel}:{node.lineno}", "monkeypatch")
                        )
    return refs


def collect_refs(consumers: tuple[str, ...]) -> list[Ref]:
    refs: list[Ref] = []
    for c in consumers:
        base = ROOT / c
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            refs.extend(_refs_in(path))
    return refs


def unsatisfied(refs: list[Ref]) -> list[tuple[Ref, str]]:
    """Refs goldenmatch does not satisfy, as (ref, reason)."""
    problems: list[tuple[Ref, str]] = []
    cache: dict[str, object | None] = {}
    for ref in refs:
        if ALLOW.get(f"{ref.module}.{ref.attr}"):
            continue
        if ref.module not in cache:
            try:
                cache[ref.module] = importlib.import_module(ref.module)
            except Exception as exc:  # noqa: BLE001 - any import failure is a break
                cache[ref.module] = None
                problems.append((ref, f"module does not import ({type(exc).__name__})"))
                continue
        mod = cache[ref.module]
        if mod is None:
            problems.append((ref, "module does not import"))
            continue
        if ref.attr is None:
            continue
        if not hasattr(mod, ref.attr):
            # A submodule is a legitimate `from pkg import sub` target even when it
            # is not yet an attribute of the parent package.
            try:
                importlib.import_module(f"{ref.module}.{ref.attr}")
                continue
            except Exception:  # noqa: BLE001
                pass
            problems.append((ref, f"`{ref.attr}` does not exist on {ref.module}"))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--consumer",
        action="append",
        help="override the consumer list (repeatable, repo-relative)",
    )
    args = ap.parse_args(argv)
    consumers = tuple(args.consumer) if args.consumer else CONSUMERS

    present = [c for c in consumers if (ROOT / c).is_dir()]
    if not present:
        print("::error::no consumer packages found -- the scan is broken", file=sys.stderr)
        return 2

    refs = collect_refs(tuple(present))
    if not refs:
        # These packages exist BECAUSE they consume goldenmatch. Finding zero
        # references means the walk broke, not that the coupling vanished.
        print(
            "::error::found no goldenmatch references in any consumer -- the scan is broken",
            file=sys.stderr,
        )
        return 2

    problems = unsatisfied(refs)
    modules = {r.module for r in refs}
    print(
        f"downstream symbols: {len(refs)} reference(s) across {len(modules)} "
        f"{UPSTREAM} module(s), from {len(present)} consumer package(s)"
    )
    if problems:
        for ref, reason in problems:
            print(
                f"::error file={ref.where.split(':')[0]},line={ref.where.split(':')[1]}::"
                f"{ref.where}: {ref.kind} reference to `{ref.module}"
                f"{'.' + ref.attr if ref.attr else ''}` -- {reason}",
                file=sys.stderr,
            )
        print(
            f"\n{len(problems)} unsatisfied reference(s). A consumer outside the uv "
            f"workspace depends on this; its own CI lane will NOT catch it because "
            f"that lane is path-filtered on the consumer, not on {UPSTREAM}.",
            file=sys.stderr,
        )
        return 1
    print(f"  OK -- every reference resolves against the installed {UPSTREAM}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
