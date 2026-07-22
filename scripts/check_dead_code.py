#!/usr/bin/env python3
"""Architecture-aware dead-code gate (Phase 1: module-level orphans).

Spec: docs/superpowers/specs/2026-07-22-arch-aware-dead-code-detection.md

In a Rust + Arrow-native, fused-compute codebase, "never runs by default" is NOT
"dead": pure-language fallbacks, parity oracles, and default-OFF opt-in kernels are
dormant-but-load-bearing. Off-the-shelf tools delete exactly those. This gate instead
computes REACHABILITY from the repo's declared surfaces + FFI/ABI exports and reports
only modules reachable from none of them -- then requires each survivor to be deleted
or classified into a `dead_code_deferred` map (mirroring `scorer_kernels_deferred`).

Phase 1 works at MODULE granularity over the committed AST import graph
(`docs/agent-codemap.json`, whose walk captures lazy/function-level imports too), so it
is naturally immune to the fallback/oracle trap: a fallback *module* is still imported;
only a *branch* inside it is dormant. Symbol-level analysis is Phase 2.

Usage:
  python scripts/check_dead_code.py [pkg]           # report (default pkg: goldenmatch)
  python scripts/check_dead_code.py [pkg] --check    # exit 1 if uncovered orphans exist
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CODEMAP = REPO / "docs" / "agent-codemap.json"

# Entry-surface hubs invoked from OUTSIDE the import graph (console_scripts, server
# bootstraps, plugin discovery). A module reached from any of these is live. Matched as
# a substring of the module dotted-path; `{pkg}` is filled per package.
_ENTRY_HUBS = (
    "{pkg}",                 # top __init__ -- the public API surface (__all__ re-exports)
    "{pkg}.__main__",
    "{pkg}.cli.main",        # console_scripts: {pkg} = {pkg}.cli.main:app
    "{pkg}.mcp.server",      # MCP stdio/http server bootstrap
    "{pkg}.a2a.server",      # A2A agent server bootstrap
    "{pkg}.api.server",      # REST server bootstrap
    "{pkg}.web.app",         # FastAPI app factory
    "{pkg}.plugins.registry",
    "{pkg}.plugins.builtin",  # entry-point-discovered plugin classes
)


def _load_pkg(pkg: str) -> dict:
    data = json.loads(CODEMAP.read_text())
    pkgs = data["packages"]
    if pkg not in pkgs:
        sys.exit(f"error: package {pkg!r} not in agent-codemap.json (have: {sorted(pkgs)})")
    return pkgs[pkg]


def _src_root(pkg: str) -> Path:
    """The `<pkg>/<pkg>/` source dir from the codemap's `root` field."""
    return REPO / _load_pkg(pkg)["root"]


def _module_name(path: Path, src_root: Path, pkg: str) -> str:
    rel = path.relative_to(src_root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([pkg, *parts]) if parts else pkg


def build_graph_ast(pkg: str) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """Build the module set + internal import graph with a DIRECT AST scan of the source
    tree -- NOT via agent-codemap.json, which under-records `from <pkg> import <submodule>`
    edges (verified: `from goldenmatch.core import sketch` etc. are absent from the codemap).
    Resolving imports needs the full module set first, so this is two passes."""
    src_root = _src_root(pkg)
    # Exclude in-package test code (some packages embed a `tests/` subpackage) -- it is not
    # shippable surface and its modules would false-flag as orphans.
    files = [
        p for p in src_root.rglob("*.py")
        if "tests" not in p.relative_to(src_root).parts
        and not p.name.startswith("test_")
        and p.name != "conftest.py"
    ]
    modules: dict[str, dict] = {}
    for f in files:
        name = _module_name(f, src_root, pkg)
        purpose = ""
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
            doc = ast.get_docstring(tree)
            purpose = (doc or "").strip().splitlines()[0] if doc else ""
        except SyntaxError:
            tree = None
        modules[name] = {"file": str(f.relative_to(REPO)), "purpose": purpose, "_tree": tree}
    known = set(modules)

    def resolve(target: str) -> str | None:
        """Map a dotted import target to the internal module it names (or its nearest
        internal package ancestor). `from a.b import c` may name module `a.b.c` OR symbol
        `c` of module `a.b` -- try the longer first."""
        if target in known:
            return target
        anc = ".".join(target.split(".")[:-1])
        return anc if anc in known else None

    graph: dict[str, set[str]] = {}
    for name, m in modules.items():
        edges: set[str] = set()
        tree = m.pop("_tree")
        if tree is None:
            graph[name] = edges
            continue
        pkg_of = name if m["file"].endswith("__init__.py") else name.rsplit(".", 1)[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if (r := resolve(a.name)):
                        edges.add(r)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative: from . / .. import X
                    base_parts = pkg_of.split(".")
                    up = node.level - 1
                    base = ".".join(base_parts[: len(base_parts) - up]) if up else pkg_of
                    mod = f"{base}.{node.module}" if node.module else base
                else:
                    mod = node.module or ""
                if not mod.startswith(pkg):
                    continue
                # each imported name may itself be a submodule (from pkg import submod)
                for a in node.names:
                    if (r := resolve(f"{mod}.{a.name}")):
                        edges.add(r)
                if (r := resolve(mod)):
                    edges.add(r)
        graph[name] = edges
    return modules, graph


def _ancestors(mod: str) -> list[str]:
    """`a.b.c` -> [`a`, `a.b`] -- Python imports every parent package to reach a submodule."""
    parts = mod.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def _scan_test_imports(pkg: str, src_root: Path) -> set[str]:
    """Modules imported by the test suite are exercised (roots) even if no source imports
    them. tests/ lives beside the package dir (../tests from the {pkg}/{pkg} source root)."""
    tests_dir = src_root.parent / "tests"
    if not tests_dir.is_dir():
        return set()
    hit: set[str] = set()
    pat = re.compile(rf"\b(?:from|import)\s+({re.escape(pkg)}(?:\.[A-Za-z0-9_]+)*)")
    for f in tests_dir.rglob("*.py"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pat.finditer(text):
            hit.add(m.group(1))
    return hit


def _load_deferred(pkg: str) -> dict[str, str]:
    """`{module: reason}` classification map at parity/dead_code/<pkg>.yaml (optional; a
    dedicated sibling file, mirroring parity/native_symbols/, so it doesn't perturb the
    api_parity gate). Reason convention: `surface --` (out-of-band runtime / dynamic
    dispatch / codegen) / `fallback --` (native-off reference) / `oracle --` (parity
    ground-truth) / `dead --` (scheduled for removal). Tiny hand-rolled reader -- no yaml dep."""
    pf = REPO / "parity" / "dead_code" / f"{pkg}.yaml"
    if not pf.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in pf.read_text().splitlines():
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        m = re.match(r"^([A-Za-z0-9_.]+):\s*(.+?)\s*$", raw)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def analyze(pkg: str) -> dict:
    src_root = _src_root(pkg)
    modules, graph = build_graph_ast(pkg)
    known = set(modules)

    # adjacency + ancestor packages of each import (Python loads parents to reach a submodule)
    adj: dict[str, set[str]] = {}
    for name, edges in graph.items():
        e = set(edges)
        for imp in list(edges):
            e.update(a for a in _ancestors(imp) if a in known)
        adj[name] = e

    # roots: entry hubs (present in the tree) + test-imported modules (+ ancestors)
    roots: set[str] = set()
    for hub in _ENTRY_HUBS:
        h = hub.format(pkg=pkg)
        if h in known:
            roots.add(h)
    test_roots = {t for t in _scan_test_imports(pkg, src_root) if t in known}
    roots |= test_roots
    for r in list(roots):
        roots.update(a for a in _ancestors(r) if a in known)

    # BFS reachability
    reachable: set[str] = set()
    stack = list(roots)
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(adj.get(cur, ()))
    # a package __init__ is live if any submodule is live (Python loads the parent)
    for name in list(reachable):
        reachable.update(a for a in _ancestors(name) if a in known)

    deferred = _load_deferred(pkg)
    orphans = sorted(known - reachable)
    uncovered = [o for o in orphans if o not in deferred]
    stale = sorted(d for d in deferred if d in reachable)          # deferred but now live
    phantom = sorted(d for d in deferred if d not in known)         # deferred but not a module

    return {
        "pkg": pkg,
        "total": len(known),
        "reachable": len(reachable),
        "roots": sorted(roots),
        "test_only_roots": sorted(test_roots - {r for r in roots if r in adj and adj[r]}),
        "orphans": orphans,
        "uncovered": uncovered,
        "deferred": deferred,
        "stale_deferrals": stale,
        "phantom_deferrals": phantom,
        "modules": modules,
    }


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    check = "--check" in argv
    pkg = args[0] if args else "goldenmatch"
    r = analyze(pkg)

    print(f"== dead-code (module-level) :: {pkg} ==")
    print(f"modules={r['total']}  reachable={r['reachable']}  "
          f"orphans={len(r['orphans'])}  uncovered={len(r['uncovered'])}  "
          f"deferred={len(r['deferred'])}")
    if r["orphans"]:
        print("\n-- orphan modules (reachable from no declared surface) --")
        for o in r["orphans"]:
            tag = "  [DEFERRED]" if o in r["deferred"] else ""
            print(f"  {o}{tag}")
            print(f"      {r['modules'][o]['file']}")
            purpose = (r['modules'][o].get('purpose') or '').strip().splitlines()[:1]
            if purpose:
                print(f"      → {purpose[0][:100]}")
    if r["stale_deferrals"]:
        print("\n-- STALE deferrals (now reachable; remove from dead_code_deferred) --")
        for s in r["stale_deferrals"]:
            print(f"  {s}")
    if r["phantom_deferrals"]:
        print("\n-- PHANTOM deferrals (not a real module) --")
        for s in r["phantom_deferrals"]:
            print(f"  {s}")

    ok = not r["uncovered"] and not r["stale_deferrals"] and not r["phantom_deferrals"]
    if check:
        if ok:
            print("\nOK: every orphan is classified in dead_code_deferred.")
            return 0
        print(f"\nFAIL: {len(r['uncovered'])} uncovered orphan(s) -- delete them or add a "
              f"dead_code_deferred entry (oracle-- / fallback-- / surface-- / dead--).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
