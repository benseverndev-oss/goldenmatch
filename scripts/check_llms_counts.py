#!/usr/bin/env python3
"""Gate: the AI-facing llms.txt + READMEs must state capability counts that match
the code.

MCP tool counts, ``__all__`` export counts, and A2A skill counts are introspected
from each package; every such number stated in the tracked surfaces is verified.
So adding an MCP tool or a public export can't silently leave the agent-facing
files lying. This replaces the drift that was guarded only by the release-time
"did you run the docs sweep?" reminder (`check_docs_sweep.py`) -- which let
goldenmatch's llms.txt sit at "54 tools / ~101 exports" while the code had 78 and
200, with the three near-duplicate copies disagreeing with each other.

Contract: within a tracked surface, every match of a count pattern (`N tools`,
`~N exports`, `N skills`) must equal the introspected value for that package. A
count with no introspection source (e.g. a package whose A2A skills aren't a
`_SKILLS` list) is reported as UNVERIFIED, never silently passed.

Run: python scripts/check_llms_counts.py   (needs the packages importable)
"""
from __future__ import annotations

import ast
import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = ROOT / "packages" / "python"
PKGS = ["goldenmatch", "goldencheck", "goldenflow", "goldenpipe", "goldenanalysis", "infermap"]

_TOOLS = re.compile(r"(\d+)\s+tools\b")
_EXPORTS = re.compile(r"~?(\d+)\s+exports\b")
_SKILLS = re.compile(r"(\d+)\s+skills\b")
_SUITE_TOTAL = re.compile(r"(\d+)\+?\s+tools across the suite")


def mcp_tools(pkg: str) -> int | None:
    try:
        mod = importlib.import_module(f"{pkg}.mcp.server")
    except Exception:
        return None
    tools = getattr(mod, "TOOLS", None)
    return len(tools) if tools is not None else None


def exports(pkg: str) -> int | None:
    try:
        mod = importlib.import_module(pkg)
    except Exception:
        return None
    names = getattr(mod, "__all__", None)
    return len(names) if names else None


def a2a_skills(pkg: str) -> int | None:
    # Count the advertised A2A skills WITHOUT importing the module (the a2a server
    # pulls aiohttp). Two source shapes are recognized: a top-level `_SKILLS = [...]`
    # list (goldenmatch), or a `"skills": [...]` list inside the agent-card dict
    # literal (goldenflow, goldenpipe). Packages whose skills aren't a static list
    # (goldencheck) return None and are reported UNVERIFIED, never silently passed.
    path = PKG_DIR / pkg / pkg / "a2a" / "server.py"
    if not path.exists():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            if any(isinstance(t, ast.Name) and t.id == "_SKILLS" for t in node.targets):
                return len(node.value.elts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "skills" and isinstance(val, ast.List):
                    return len(val.elts)
    return None


def counts(pkg: str) -> dict[str, int | None]:
    return {"mcp": mcp_tools(pkg), "exports": exports(pkg), "skills": a2a_skills(pkg)}


_DOCS_LINK = re.compile(r"https://docs\.bensevern\.dev/([A-Za-z0-9/_-]+)")
_DEAD_DOMAIN = re.compile(r"https://[A-Za-z0-9.-]*\.github\.io/[A-Za-z0-9/_-]*")


def doc_link_errors() -> list[str]:
    """Network-free link check for the llms.txt: the canonical docs domain is
    `docs.bensevern.dev` (docs.json), so a cited `docs.bensevern.dev/<path>` must
    map to a real `docs-site/<path>.mdx`, and the old per-package `*.github.io`
    mkdocs sites (dead post-fold) must not be referenced. Catches link rot without
    a flaky network call."""
    errors: list[str] = []
    files = [ROOT / "llms.txt"] + [ROOT / f"packages/python/{p}/llms.txt" for p in PKGS]
    for path in files:
        if not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for dead in sorted(set(_DEAD_DOMAIN.findall(text))):
            errors.append(f"{rel}: references dead github.io docs site: {dead}")
        for sub in sorted(set(_DOCS_LINK.findall(text))):
            if not (ROOT / "docs-site" / f"{sub}.mdx").exists():
                errors.append(f"{rel}: docs link /{sub} has no docs-site/{sub}.mdx")
    return errors


def _check_file(rel: str, pattern: re.Pattern[str], expected: int, label: str,
                errors: list[str]) -> int:
    """Assert every match of `pattern` in the file equals `expected`. Returns the
    number of matches checked (0 => the surface doesn't state this count). Use for
    surfaces where the count appears only as a clean total (the llms.txt) and for
    export counts (never sub-counted)."""
    path = ROOT / rel
    if not path.exists():
        return 0
    seen = 0
    for m in pattern.finditer(path.read_text(encoding="utf-8")):
        seen += 1
        got = int(m.group(1))
        if got != expected:
            errors.append(f"{rel}: states {got} {label}, code has {expected}")
    return seen


def _check_total_presence(rel: str, pattern: re.Pattern[str], expected: int,
                          label: str, errors: list[str]) -> int:
    """Sub-count-aware check for surfaces (READMEs) that mix the grand total with
    per-category sub-counts (e.g. goldencheck's "7 tools" for one group vs the "19
    tools" total). The canonical total MUST appear at least once, and no stated
    count may EXCEED it (a sub-count is always <= total; a number above it is drift
    up, and a wrong total leaves the real total absent)."""
    path = ROOT / rel
    if not path.exists():
        return 0
    values = [int(m.group(1)) for m in pattern.finditer(path.read_text(encoding="utf-8"))]
    if not values:
        return 0
    if expected not in values:
        errors.append(f"{rel}: no '{expected} {label}' total found "
                      f"(states {sorted(set(values))}); code has {expected}")
    for v in values:
        if v > expected:
            errors.append(f"{rel}: states {v} {label}, exceeds the code total of {expected}")
    return len(values)


def main() -> int:
    errors: list[str] = []
    unverified: list[str] = []
    suite_total = 0

    for pkg in PKGS:
        c = counts(pkg)
        if c["mcp"] is not None:
            suite_total += c["mcp"]
        llms = f"packages/python/{pkg}/llms.txt"

        # MCP tool count -- llms.txt (local + remote mentions).
        if c["mcp"] is not None:
            _check_file(llms, _TOOLS, c["mcp"], "tools", errors)
        # Export count.
        if c["exports"] is not None:
            _check_file(llms, _EXPORTS, c["exports"], "exports", errors)
        # A2A skills -- only where introspectable.
        stated_skills = 0
        path = ROOT / llms
        if path.exists():
            stated_skills = len(_SKILLS.findall(path.read_text(encoding="utf-8")))
        if c["skills"] is not None:
            _check_file(llms, _SKILLS, c["skills"], "skills", errors)
        elif stated_skills:
            unverified.append(f"{pkg}: llms.txt states a skill count but no _SKILLS "
                              "introspection source -- not gated")

        # Package README -- exports are strict (never sub-counted); tool/skill
        # totals use the sub-count-aware presence+ceiling rule, because READMEs
        # mix the grand total with per-category sub-counts.
        readme = f"packages/python/{pkg}/README.md"
        if c["exports"] is not None:
            _check_file(readme, _EXPORTS, c["exports"], "exports", errors)
        if c["mcp"] is not None:
            _check_total_presence(readme, _TOOLS, c["mcp"], "tools", errors)
        if c["skills"] is not None:
            _check_total_presence(readme, _SKILLS, c["skills"], "skills", errors)

    # Suite-level llms.txt: "N tools across the suite" == sum of per-package MCP tools.
    seen_suite = _check_file("llms.txt", _SUITE_TOTAL, suite_total, "suite tools", errors)
    if not seen_suite:
        unverified.append(f"llms.txt: no 'N tools across the suite' claim (suite total is {suite_total})")

    # Doc links in the llms.txt resolve to real pages (and no dead github.io sites).
    errors.extend(doc_link_errors())

    for u in unverified:
        print(f"UNVERIFIED: {u}")
    if errors:
        print("\nllms/README count gate FAILED:")
        for e in errors:
            print(f"  - {e}")
        print("\nUpdate the stated numbers to match the code (or the code, if the "
              "count is wrong).")
        return 1
    print(f"llms/README count gate OK: verified per-package MCP/export/skill counts "
          f"+ suite total ({suite_total} tools).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
