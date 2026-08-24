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
count with no introspection source -- a package whose A2A skills aren't a
`_SKILLS` list, or one whose MCP server / package root will not IMPORT in this
environment -- is reported as UNVERIFIED, never silently passed.

The unverified case is load-bearing, not a nicety. `mcp_tools()` swallows an
ImportError and returns None; that used to skip the package with no output while
still summing its tools into the suite total as zero, so the only symptom was the
SUITE-TOTAL check failing with a short number -- an error whose remediation text
told you to edit llms.txt DOWN to the broken value. The suite total is now
REFUSED outright when any package fails to introspect.

`--write` rewrites the stated numbers in place; `regen_docs.py` runs it as a
generator step, so these counts are no longer "hand-authored prose a checker
merely complains about". Two rewrite contracts:
every gated match is rewritten, in the llms.txt and the READMEs alike. Genuine
per-category sub-counts are DECLARED in `_SUBCOUNT_ALLOW` (one entry repo-wide)
and are skipped by both the check and the rewrite -- the older "any value below
the total is a sub-count" ceiling rule was what let goldenmatch's README sit at
"95 tools available" against a code total of 97.

Run: python scripts/check_llms_counts.py [--write]   (needs the packages importable)
"""
from __future__ import annotations

import argparse
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


# The site is served under a /docs prefix, so the path AFTER that prefix is what
# maps to a source file. The prefix is required, not optional: a URL without it
# 404s (see scripts/check_docs_links.py, which gates that repo-wide).
_DOCS_LINK = re.compile(r"https://docs\.bensevern\.dev/docs/([A-Za-z0-9/_-]+)")
_DEAD_DOMAIN = re.compile(r"https://[A-Za-z0-9.-]*\.github\.io/[A-Za-z0-9/_-]*")


def doc_link_errors() -> list[str]:
    """Network-free link check for the llms.txt: the canonical docs domain is
    `docs.bensevern.dev` (docs.json), so a cited `docs.bensevern.dev/docs/<path>` must
    map to a real `docs-site/<path>.mdx`, and the old per-package `*.github.io`
    mkdocs sites (dead post-fold) must not be referenced. Catches link rot without
    a flaky network call."""
    errors: list[str] = []
    files = [ROOT / "llms.txt"] + [ROOT / f"packages/python/{p}/{p.replace('-', '_')}/llms.txt" for p in PKGS]
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


def _sub_group1(text: str, pattern: re.Pattern[str], new: int,
                predicate) -> tuple[str, int]:
    """Replace capture group 1 (the digits) of every match satisfying `predicate`.

    Rewrites only the number, so surrounding prose -- the `~` on `~200 exports`,
    the `+` on `139+ tools across the suite`, the spacing -- survives untouched.
    """
    out: list[str] = []
    last = 0
    n = 0
    for m in pattern.finditer(text):
        if not predicate(int(m.group(1)), m):
            continue
        start, end = m.span(1)
        out.append(text[last:start])
        out.append(str(new))
        last = end
        n += 1
    out.append(text[last:])
    return "".join(out), n


def _exact_surface(rel: str, pattern: re.Pattern[str], expected: int, label: str,
                   errors: list[str], edits: list[str], write: bool) -> int:
    """Every stated count in `rel` must equal `expected`, bar declared exceptions.

    Applies to the llms.txt (a clean total, no sub-counts) AND to the package
    READMEs, whose "N tools available" sentences and comparison-table cells are
    all restatements of the same total. Lines named in `_SUBCOUNT_ALLOW` are
    skipped -- both by the check and by `--write`, so a declared sub-count is
    never silently rewritten to the total.

    Returns the number of gated matches seen (0 => the surface doesn't state it).
    """
    path = ROOT / rel
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8")
    skip = _allowed_spans(rel, text)

    def gated(m: re.Match[str]) -> bool:
        return not any(lo <= m.start() < hi for lo, hi in skip)

    values = [int(m.group(1)) for m in pattern.finditer(text) if gated(m)]
    if not values:
        return 0
    wrong = [v for v in values if v != expected]
    if not wrong:
        return len(values)

    if write:
        stale = set(wrong)
        new_text, n = _sub_group1(
            text, pattern, expected,
            lambda v, m: gated(m) and v != expected,
        )
        path.write_text(new_text, encoding="utf-8", newline="\n")
        edits.append(f"{rel}: {sorted(stale)} -> {expected} {label} ({n} occurrence(s))")
    else:
        for v in sorted(set(wrong)):
            errors.append(f"{rel}: states {v} {label}, code has {expected}")
    return len(values)


# The ONLY legitimate per-category sub-counts in the tracked READMEs. Everything
# else stating "N tools" is a claim about the TOTAL and is gated strictly.
#
# The previous model had this backwards: it allowed ANY value <= the total as a
# "sub-count", on the theory that READMEs mix totals with per-group figures. In
# practice they barely do -- there is exactly one such figure in the whole repo --
# and the allowance was hiding a live bug: goldenmatch's README said "95 tools
# available" while the code had 97, and the ceiling rule passed it because 97
# appeared elsewhere and 95 < 97. A stale SECOND statement of the total is
# indistinguishable from a sub-count under a pure ceiling rule.
#
# Declared exceptions instead, in the spirit of parity/<pkg>.yaml's `*_deferred:`
# maps: an uncovered count must be named with a reason, never left silent. Keyed
# by tracked path -> (substring identifying the line, why it is not the total).
_SUBCOUNT_ALLOW: dict[str, tuple[tuple[str, str], ...]] = {
    "packages/python/goldencheck/README.md": (
        ("\u251c\u2500\u2500 mcp",
         "source-tree diagram annotating the mcp/ package directory, not a total"),
    ),
}


def _allowed_spans(rel: str, text: str) -> list[tuple[int, int]]:
    """Character spans of lines carrying a declared sub-count exception."""
    spans: list[tuple[int, int]] = []
    markers = _SUBCOUNT_ALLOW.get(rel, ())
    if not markers:
        return spans
    pos = 0
    for line in text.splitlines(keepends=True):
        if any(marker in line for marker, _ in markers):
            spans.append((pos, pos + len(line)))
        pos += len(line)
    return spans


def run(write: bool) -> int:
    errors: list[str] = []
    unverified: list[str] = []
    edits: list[str] = []
    suite_total = 0
    unintrospectable: list[str] = []

    for pkg in PKGS:
        c = counts(pkg)
        llms = f"packages/python/{pkg}/{pkg.replace('-', '_')}/llms.txt"
        readme = f"packages/python/{pkg}/README.md"

        # An unintrospectable surface is REPORTED, never silently skipped. Before
        # this, `mcp_tools()` swallowing an ImportError returned None and the whole
        # package was passed over with no output at all -- and its tool count still
        # summed into `suite_total` as 0, so the only symptom was the suite-total
        # check failing with a number that told you to edit llms.txt DOWN to the
        # broken value. The docstring promised UNVERIFIED here; only `skills`
        # implemented it.
        for surface, value in (("MCP tools", c["mcp"]), ("__all__ exports", c["exports"])):
            if value is None:
                unintrospectable.append(f"{pkg}: {surface}")
                unverified.append(
                    f"{pkg}: {surface} could not be introspected -- every count "
                    f"stated in {llms} and {readme} is UNCHECKED"
                )

        if c["mcp"] is not None:
            suite_total += c["mcp"]
            _exact_surface(llms, _TOOLS, c["mcp"], "tools", errors, edits, write)
            _exact_surface(readme, _TOOLS, c["mcp"], "tools", errors, edits, write)
        if c["exports"] is not None:
            _exact_surface(llms, _EXPORTS, c["exports"], "exports", errors, edits, write)
            _exact_surface(readme, _EXPORTS, c["exports"], "exports", errors, edits, write)

        # A2A skills -- only where introspectable.
        path = ROOT / llms
        stated_skills = len(_SKILLS.findall(path.read_text(encoding="utf-8"))) if path.exists() else 0
        if c["skills"] is not None:
            _exact_surface(llms, _SKILLS, c["skills"], "skills", errors, edits, write)
            _exact_surface(readme, _SKILLS, c["skills"], "skills", errors, edits, write)
        elif stated_skills:
            unverified.append(f"{pkg}: llms.txt states a skill count but no _SKILLS "
                              "introspection source -- not gated")

    # Suite-level llms.txt: "N tools across the suite" == sum of per-package MCP
    # tools. REFUSED outright when any package failed to introspect: the sum would
    # silently be short by that package's tools, and the resulting error message
    # names a total that is wrong in the other direction.
    if unintrospectable:
        errors.append(
            "suite total REFUSED: "
            + ", ".join(sorted(unintrospectable))
            + " could not be introspected, so the sum is not a real total. Fix the "
              "import (usually a missing extra in this environment) before trusting "
              "any count here -- do NOT edit llms.txt to match the short sum."
        )
    else:
        seen_suite = _exact_surface("llms.txt", _SUITE_TOTAL, suite_total, "suite tools",
                                    errors, edits, write)
        if not seen_suite:
            unverified.append(
                f"llms.txt: no 'N tools across the suite' claim (suite total is {suite_total})")

    # Doc links in the llms.txt resolve to real pages (and no dead github.io sites).
    errors.extend(doc_link_errors())

    for u in unverified:
        print(f"UNVERIFIED: {u}")
    for e in edits:
        print(f"rewrote {e}")

    if errors:
        print("\nllms/README count gate FAILED:" if not write
              else "\nllms/README counts could NOT be fully rewritten:")
        for e in errors:
            print(f"  - {e}")
        if not write:
            print("\nRun `python scripts/check_llms_counts.py --write` to rewrite the "
                  "mechanical counts, or fix the code if a count is wrong.")
        return 1

    if write:
        print(f"llms/README counts written: {len(edits)} edit(s); suite total {suite_total} tools.")
    else:
        print(f"llms/README count gate OK: verified per-package MCP/export/skill counts "
              f"+ suite total ({suite_total} tools).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify (or rewrite) stated capability counts.")
    ap.add_argument("--write", action="store_true",
                    help="Rewrite the stated counts in place (run by scripts/regen_docs.py).")
    ap.add_argument("--check", action="store_true",
                    help="Report drift and exit 1 (the default).")
    args = ap.parse_args(argv)
    return run(write=args.write)


if __name__ == "__main__":
    sys.exit(main())
