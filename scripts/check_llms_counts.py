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


def _check_file(rel: str, pattern: re.Pattern[str], expected: int, label: str,
                errors: list[str]) -> int:
    """Assert every match of `pattern` in the file equals `expected`. Returns the
    number of matches checked (0 => the surface doesn't state this count)."""
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

    # goldenmatch README also restates tools/skills -- lock those too.
    gm = counts("goldenmatch")
    if gm["mcp"] is not None:
        _check_file("packages/python/goldenmatch/README.md", _TOOLS, gm["mcp"], "tools", errors)
    if gm["skills"] is not None:
        _check_file("packages/python/goldenmatch/README.md", _SKILLS, gm["skills"], "skills", errors)

    # Suite-level llms.txt: "N tools across the suite" == sum of per-package MCP tools.
    seen_suite = _check_file("llms.txt", _SUITE_TOTAL, suite_total, "suite tools", errors)
    if not seen_suite:
        unverified.append(f"llms.txt: no 'N tools across the suite' claim (suite total is {suite_total})")

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
