#!/usr/bin/env python3
"""Generate the CAPABILITY MATRIX table in docs-site/reference/api-surface.mdx.

The api-surface page is mostly hand-written prose, but its capability matrix
(per-package versions + MCP-tool counts) is pure drift-bait -- it sat stale by
whole major versions until this gate. Only the marker-delimited TABLE is
generated, and from STATIC sources so the block is byte-identical in every
environment (the suite-matrix lesson: a live `import <pkg>.mcp.server` needs the
[mcp] extra and flaps between the dev box and CI):

  - Python version -> packages/python/<pkg>/pyproject.toml  [project].version
  - TypeScript ver -> packages/typescript/<pkg>/package.json  .version
  - MCP tool count -> parity/<pkg>.yaml  mcp_tools (shared + python_only) -- the
                      same env-stable, api_parity-gated source suite-matrix uses
  - A2A present    -> parity/<pkg>.yaml  a2a_skills present
  - CLI / REST / Native-SQL -> the editorial META map below (a package gaining a
                      REST service or a native wheel is a rare, reviewable event;
                      one place in code beats a number buried in prose)

`--check` also asserts the two inline figures that share the table's static source
-- the GoldenMatch "**N MCP tools**" line and the coverage note "vs N Python" --
so those can't drift from it. Two other inline numbers stay hand-maintained on
purpose: the GoldenFlow "N built-in transforms" count (its registry is populated
lazily, so a live `list_transforms()` flaps by import order -- the gen_suite_matrix
live-import trap) and the note's "TS tools" figure (no static source without a
built dist). Gating those would make the check non-deterministic; they are left as
prose.

Run:
  python scripts/gen_api_surface.py --write    # regenerate the table block
  python scripts/gen_api_surface.py --check     # CI drift gate
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MARKER_START = (
    "{/* api-surface-matrix:generated:start -- DO NOT EDIT. "
    "Regenerate: python scripts/gen_api_surface.py --write */}"
)
MARKER_END = "{/* api-surface-matrix:generated:end */}"

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs-site" / "reference" / "api-surface.mdx"
PARITY = ROOT / "parity"

PKGS = ["goldenmatch", "goldencheck", "goldenflow", "goldenpipe", "goldenanalysis", "infermap"]
DISPLAY = {
    "goldenmatch": "GoldenMatch",
    "goldencheck": "GoldenCheck",
    "goldenflow": "GoldenFlow",
    "goldenpipe": "GoldenPipe",
    "goldenanalysis": "GoldenAnalysis",
    "infermap": "InferMap",
}

# Editorial columns -- change rarely, reviewed here rather than buried in the mdx.
#   rest:   ships a long-running REST HTTP service (the `<pkg> serve` service-shaped
#           packages); GoldenAnalysis / InferMap are libraries + CLI + MCP only.
#   native: the "Native / SQL" cell text. Update when a package gains/loses a native
#           wheel, WASM core, or SQL extension (a `<pkg>-native` crate + publish
#           workflow, a `-core` WASM, or the Postgres/DuckDB surfaces).
META = {
    "goldenmatch": {"rest": True, "native": "wheel · Postgres · DuckDB"},
    "goldencheck": {"rest": True, "native": "wheel"},
    "goldenflow": {"rest": True, "native": "wheel"},
    "goldenpipe": {"rest": True, "native": "core (WASM)"},
    "goldenanalysis": {"rest": False, "native": "core (WASM)"},
    "infermap": {"rest": False, "native": "wheel"},
}


def _load_parity(pkg: str) -> dict:
    import yaml

    path = PARITY / f"{pkg}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def _py_version(pkg: str) -> str | None:
    path = ROOT / "packages" / "python" / pkg / "pyproject.toml"
    if not path.exists():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _ts_version(pkg: str) -> str | None:
    path = ROOT / "packages" / "typescript" / pkg / "package.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("version")


def _mcp(pkg: str) -> int:
    body = _load_parity(pkg).get("mcp_tools") or {}
    return len(body.get("shared", [])) + len(body.get("python_only", []))


def _a2a(pkg: str) -> bool:
    body = _load_parity(pkg).get("a2a_skills") or {}
    return (len(body.get("shared", [])) + len(body.get("python_only", []))) > 0


def render_table() -> str:
    rows = [
        "| Package | Python (PyPI) | TypeScript (npm) | CLI | MCP tools | REST | A2A | Native / SQL |",
        "|---------|:-------------:|:----------------:|-----|:---------:|:----:|:---:|--------------|",
    ]
    for p in PKGS:
        rest = "✓" if META[p]["rest"] else "—"
        a2a = "✓" if _a2a(p) else "—"
        rows.append(
            f"| [{DISPLAY[p]}](#{p}) | `{_py_version(p)}` | `{_ts_version(p)}` | `{p}` "
            f"| {_mcp(p)} | {rest} | {a2a} | {META[p]['native']} |"
        )
    return "\n".join(rows)


def render_block() -> str:
    return f"{MARKER_START}\n{render_table()}\n{MARKER_END}"


def _splice(page: str, block: str) -> str:
    if MARKER_START not in page or MARKER_END not in page:
        raise SystemExit(
            f"{PAGE.relative_to(ROOT)} is missing the generated-table markers. "
            "Add both marker comments around the capability-matrix table first."
        )
    i = page.index(MARKER_START)
    j = page.index(MARKER_END) + len(MARKER_END)
    return page[:i] + block + page[j:]


def check() -> list[str]:
    """Return a list of drift problems (empty == the page is current)."""
    page = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
    problems: list[str] = []

    if MARKER_START not in page or MARKER_END not in page:
        return ["capability-matrix generated markers are missing from the page"]

    current = page[page.index(MARKER_START) : page.index(MARKER_END) + len(MARKER_END)]
    if current != render_block():
        problems.append("the capability-matrix table block is stale vs the manifests")

    # Inline figures derived from the SAME static source as the table (the parity
    # MCP count), so they can't drift from it. Two other inline numbers -- the
    # GoldenFlow transform count and the note's "TS tools" figure -- are left
    # hand-maintained ON PURPOSE: neither has an env-stable static source (the
    # goldenflow transform *registry* is populated lazily, so `list_transforms()`
    # returns a different count depending on which submodules a process has
    # imported -- 113 on a base import, more after deep imports -- and gating on a
    # live import would flap by test order, the very trap gen_suite_matrix avoids;
    # the TS-tools count needs a built dist). They stay prose.
    mcp_gm = _mcp("goldenmatch")
    if f"**{mcp_gm} MCP tools**" not in page:
        problems.append(f"the inline GoldenMatch MCP-tools figure != {mcp_gm}")
    if f"vs {mcp_gm} Python" not in page:
        problems.append(f"the coverage-note 'vs N Python' MCP figure != {mcp_gm}")

    return problems


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    if mode == "--write":
        page = PAGE.read_text(encoding="utf-8")
        PAGE.write_text(_splice(page, render_block()), encoding="utf-8")
        print(f"wrote {PAGE.relative_to(ROOT)}")
        return 0
    if mode == "--check":
        problems = check()
        if problems:
            print(
                f"{PAGE.relative_to(ROOT)} capability matrix is STALE vs the live surface. "
                "Regenerate the table with: python scripts/gen_api_surface.py --write "
                "(and update any flagged inline figure)."
            )
            for p in problems:
                print(f"  - {p}")
            return 1
        print("api-surface.mdx OK: capability matrix matches the live surface.")
        return 0
    print(f"unknown mode {mode!r} (use --write / --check)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
