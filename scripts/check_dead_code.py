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
  python scripts/check_dead_code.py [pkg]           # module-level report (default: goldenmatch)
  python scripts/check_dead_code.py [pkg] --check    # exit 1 if uncovered orphans exist
  python scripts/check_dead_code.py [pkg] --symbols  # Phase 2a: bare-name symbol scan (low-FP)
  python scripts/check_dead_code.py [pkg] --scoped   # Phase 2b: scope-aware symbol resolver
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
            tree = ast.parse(f.read_text(encoding="utf-8-sig", errors="ignore"))
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
    """Modules imported by the test suite (and sibling scripts) are exercised (roots) even if
    no source imports them. `tests/`/`scripts/` live beside the package dir (../ from the
    {pkg}/{pkg} source root). AST-parsed, NOT regex: a `from {pkg}.core import _planner_json`
    names the SUBMODULE `{pkg}.core._planner_json` in the imported-name position, which a
    "dotted path after from/import" regex misses -- the same `from X import <submodule>`
    under-recording that made the codemap unsound. We emit both the module and each
    `module.name` candidate; the caller keeps only those that are real modules."""
    hit: set[str] = set()
    for sib in ("tests", "scripts"):
        d = src_root.parent / sib
        if not d.is_dir():
            continue
        for f in d.rglob("*.py"):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8-sig", errors="ignore"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == pkg or a.name.startswith(pkg + "."):
                            hit.add(a.name)
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    m = node.module or ""
                    if m == pkg or m.startswith(pkg + "."):
                        hit.add(m)
                        for a in node.names:      # `from pkg.sub import submodule` names a module
                            hit.add(f"{m}.{a.name}")
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


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCAN_EXT = {".py", ".rs", ".ts", ".tsx", ".mjs", ".cjs", ".mdx", ".md",
             ".yaml", ".yml", ".json", ".toml", ".sql", ".ipynb"}
_SCAN_SKIP = {".git", "node_modules", "target", "dist", "build", ".venv",
              "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "htmlcov"}


def _repo_text_files():
    for p in REPO.rglob("*"):
        if not p.is_file() or p.suffix not in _SCAN_EXT:
            continue
        if any(part in _SCAN_SKIP for part in p.parts):
            continue
        yield p


def analyze_symbols(pkg: str) -> dict:
    """Phase 2: module-level (top-level) `def`/`class` symbols referenced NOWHERE in the
    whole repo -- code, string literals, tests, other languages, docs. A symbol whose total
    repo-wide identifier occurrences do not exceed its definition count has zero references.

    Architecture-aware without special-casing: because the scan counts occurrences inside
    STRING LITERALS and across ALL file types, names that are exported (`__all__` string),
    string-dispatched (MCP/A2A/scorer registries), FFI-exported (Rust/TS mirrors), or
    referenced only by tests are all automatically kept alive. The one form the occurrence
    count can't see is DECORATOR registration (`@app.command`, `@router.get`, `@pytest.fixture`,
    pydantic validators), so decorated defs/classes are excluded from suspicion up front."""
    src_root = _src_root(pkg)
    files = [
        p for p in src_root.rglob("*.py")
        if "tests" not in p.relative_to(src_root).parts
        and not p.name.startswith("test_") and p.name != "conftest.py"
    ]
    # candidate top-level symbols: {name: [(module_file, lineno), ...]}
    cands: dict[str, list[tuple[str, int]]] = {}
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        rel = str(f.relative_to(REPO))
        for node in tree.body:  # top-level only
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.decorator_list:            # decorator may register out-of-band
                    continue
                if node.name.startswith("__") and node.name.endswith("__"):
                    continue
                cands.setdefault(node.name, []).append((rel, node.lineno))
    names = set(cands)

    # repo-wide occurrence count, restricted to candidate names (cheap)
    counts: dict[str, int] = dict.fromkeys(names, 0)
    for p in _repo_text_files():
        try:
            text = p.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        for tok in _IDENT.findall(text):
            if tok in counts:
                counts[tok] += 1

    dead = []
    for name, defs in cands.items():
        if counts[name] <= len(defs):  # occurrences never exceed the definition(s) => unreferenced
            for (file, line) in defs:
                dead.append((name, file, line))
    dead.sort(key=lambda t: (t[1], t[2]))
    return {"pkg": pkg, "n_symbols": len(cands), "dead": dead}


def _attr_chain(node: ast.AST) -> list[str] | None:
    """Leftmost-first dotted names of a Name/Attribute load chain (`a.b.c` -> [a,b,c])."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return None


def analyze_symbols_scoped(pkg: str) -> dict:
    """Phase 2b: SCOPE-AWARE unused top-level symbols. Resolves every Name/Attribute load
    through each file's import bindings to the concrete (module, symbol) it references -- so a
    dead `def score` in module X is no longer masked by an unrelated `score` token elsewhere.

    A top-level def/class M.S is a candidate if no resolved use targets it and it isn't
    referenced as a bare STRING literal anywhere in the package source (that keep-alive covers
    __all__ exports + dispatch registries + FFI/cross-lang mirrors, without the whole-repo
    prose masking of Phase 2a). Decorated defs excluded (out-of-band registration)."""
    src_root = _src_root(pkg)
    known_modules, _ = build_graph_ast(pkg)  # source-only module set

    def is_src(p: Path) -> bool:
        parts = p.relative_to(src_root).parts
        return "tests" not in parts and not p.name.startswith("test_") and p.name != "conftest.py"

    # candidate defs (source only): {(module, name): (file, line)}
    src_files = [p for p in src_root.rglob("*.py") if is_src(p)]
    defs: dict[tuple[str, str], tuple[str, int]] = {}
    top_names: dict[str, set[str]] = {}
    for f in src_files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        mod = _module_name(f, src_root, pkg)
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                if node.decorator_list or (node.name.startswith("__") and node.name.endswith("__")):
                    continue
                defs[(mod, node.name)] = (str(f.relative_to(REPO)), node.lineno)
        top_names[mod] = names

    # resolve uses across source, in-package tests, the sibling test suite, AND the sibling
    # scripts/ dir. The real test suite lives at <pkg>/tests (a SIBLING of the <pkg>/<pkg>
    # source root, not under it), and package-local <pkg>/scripts/ holds maintenance / codegen
    # / byte-parity-corpus generators that are legitimate consumers of reference helpers (e.g.
    # goldenflow's `_double_metaphone_*_py` are used only by scripts/gen_identifiers_corpus.py).
    # A source-only scan falsely flags every symbol consumed only by a test or a script.
    all_py = list(src_root.rglob("*.py"))
    for sib in ("tests", "scripts"):
        d = src_root.parent / sib
        if d.is_dir():
            all_py += list(d.rglob("*.py"))
    used: set[tuple[str, str]] = set()
    for f in all_py:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        # external test files are not package modules (no same-module / relative-import
        # resolution applies -- they import the package absolutely)
        in_tree = src_root in f.parents
        this_mod = _module_name(f, src_root, pkg) if in_tree else None
        pkg_of = (this_mod if f.name == "__init__.py" else this_mod.rsplit(".", 1)[0]) if this_mod else pkg
        # local name -> SET of targets. Set-valued (not scalar) because one file can bind the
        # SAME local name to different modules in different branches -- CLI dispatch does
        # `from .mcp.server import run_server` in one command and `from .a2a.server import
        # run_server` in another. A scalar map lets the second overwrite the first, so a bare
        # `run_server()` credits only the last binding and the other is falsely flagged. We
        # can't tell which branch runs, so a use marks EVERY candidate binding used.
        sym_binds: dict[str, set[tuple[str, str]]] = {}   # local -> {(module, orig), ...}
        mod_binds: dict[str, set[str]] = {}               # local -> {module, ...}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == pkg or a.name.startswith(pkg + "."):
                        mod_binds.setdefault(a.asname or a.name.split(".")[0], set()).add(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base_parts = pkg_of.split(".")
                    base = ".".join(base_parts[: len(base_parts) - (node.level - 1)])
                    m = f"{base}.{node.module}" if node.module else base
                else:
                    m = node.module or ""
                if not (m == pkg or m.startswith(pkg + ".")):
                    continue
                for a in node.names:
                    local = a.asname or a.name
                    if f"{m}.{a.name}" in known_modules:      # from pkg import submodule
                        mod_binds.setdefault(local, set()).add(f"{m}.{a.name}")
                    else:                                      # from module import symbol
                        sym_binds.setdefault(local, set()).add((m, a.name))
        # walk loads
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in sym_binds:
                    used.update(sym_binds[node.id])
                elif node.id in top_names.get(this_mod, ()):   # same-module use
                    used.add((this_mod, node.id))
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                chain = _attr_chain(node)
                if not chain:
                    continue
                base = chain[0]
                if base in mod_binds and len(chain) >= 2:
                    used.update((mod, chain[1]) for mod in mod_binds[base])
                elif base in sym_binds and len(chain) >= 2:
                    used.update(sym_binds[base])               # attr on an imported symbol => symbol used
                elif base == pkg:                              # goldenmatch.a.b.SYM
                    for i in range(len(chain) - 1, 0, -1):
                        mod = ".".join(chain[:i])
                        if mod in known_modules:
                            used.add((mod, chain[i]))
                            break

    # string-literal keep-alive, scoped to PACKAGE SOURCE (dispatch/registry/__all__/FFI names)
    string_names: set[str] = set()
    for f in src_files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.isidentifier():
                    string_names.add(node.value)

    strong, dynamic = [], []
    for (mod, name), (file, line) in sorted(defs.items()):
        if (mod, name) in used:
            continue
        (dynamic if name in string_names else strong).append((mod, name, file, line))
    return {"pkg": pkg, "n_defs": len(defs), "strong": strong, "dynamic": dynamic}


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    check = "--check" in argv
    pkg = args[0] if args else "goldenmatch"

    if "--scoped" in argv or "--symbols2" in argv:
        s = analyze_symbols_scoped(pkg)
        print(f"== dead-code (symbol-level, SCOPE-AWARE) :: {pkg} ==")
        print(f"undecorated top-level def/class={s['n_defs']}  "
              f"unused_resolved={len(s['strong']) + len(s['dynamic'])}  "
              f"(strong={len(s['strong'])}  string-referenced={len(s['dynamic'])})")
        if s["strong"]:
            print("\n-- STRONG candidates (no resolved use, no bare-string reference) --")
            for mod, name, file, line in s["strong"]:
                print(f"  {mod}.{name}\n      {file}:{line}")
        if s["dynamic"]:
            print("\n-- string-referenced (unused by resolver, but name appears as a source "
                  "string literal -- likely dispatch/registry/__all__/FFI; verify before cutting) --")
            for mod, name, file, line in s["dynamic"]:
                print(f"  {mod}.{name}\n      {file}:{line}")
        print("\nnote: HIGHER-recall / higher-FP pass -- resolves Name/Attribute loads through each "
              "file's import bindings, so a dead `def score` is no longer masked by unrelated `score` "
              "tokens. FP sources: decorator-free registration via getattr/globals(), reflection, and "
              "cross-language (Rust/TS) callers that don't mirror the name as a string. Classify "
              "survivors into parity/dead_code/<pkg>.yaml.")
        return 0

    if "--symbols" in argv:
        s = analyze_symbols(pkg)
        print(f"== dead-code (symbol-level) :: {pkg} ==")
        print(f"top-level def/class symbols (undecorated)={s['n_symbols']}  "
              f"unreferenced_anywhere={len(s['dead'])}")
        for name, file, line in s["dead"]:
            print(f"  {name}\n      {file}:{line}")
        print("\nnote: LOW-recall / low-FP pass -- flags only symbols whose bare name occurs "
              "NOWHERE else in the repo (code+strings+tests+other langs+docs). A dead symbol "
              "whose name collides with any unrelated token is masked; higher recall needs a "
              "scope-aware AST reference resolver (Phase 2b).")
        return 0

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
