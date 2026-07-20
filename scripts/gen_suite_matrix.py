#!/usr/bin/env python3
"""Generate the repo-level SUITE SURFACE MATRIX -- the cross-package view that no
per-package gate shows: capability counts side by side, Python<->TypeScript
disjointedness, and auto-detected gaps/asymmetries.

Everything is derived from the same sources the per-package gates already trust:
  - live introspection (MCP tools, __all__ exports, A2A skills) via check_llms_counts;
  - the CLI surface + cross-language partition from parity/<pkg>.yaml;
  - file presence for the recipes / TS-port columns.

The generated block (between the markers) is rendered from those sources, so
`--check` fails if docs-site/suite-matrix.mdx drifts from the real suite. This is
the capstone of the config-matrix arc: one place that tracks inconsistency,
disjointedness, and gaps ACROSS packages.

Run:
  python scripts/gen_suite_matrix.py --write    # regenerate the page
  python scripts/gen_suite_matrix.py --check     # CI drift gate
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MARKER_START = "{/* suite-matrix:generated:start -- DO NOT EDIT. Regenerate: python scripts/gen_suite_matrix.py --write */}"
MARKER_END = "{/* suite-matrix:generated:end */}"

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs-site" / "suite-matrix.mdx"
PARITY = ROOT / "parity"
PKG_DIR = ROOT / "packages" / "python"
PKGS = ["goldenmatch", "goldencheck", "goldenflow", "goldenpipe", "goldenanalysis", "infermap"]


# --- Live introspection (mirrors check_llms_counts; inlined so this capstone has no
#     cross-gate import dependency) --------------------------------------------------
def _mcp_tools(pkg: str) -> int | None:
    try:
        mod = importlib.import_module(f"{pkg}.mcp.server")
    except Exception:
        return None
    tools = getattr(mod, "TOOLS", None)
    return len(tools) if tools is not None else None


def _exports(pkg: str) -> int | None:
    try:
        mod = importlib.import_module(pkg)
    except Exception:
        return None
    names = getattr(mod, "__all__", None)
    return len(names) if names else None


def _a2a_skills(pkg: str) -> int | None:
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
# The cross-language surfaces the parity manifests partition, in render order.
# (blocking_strategies joins once the api_parity manifest declares it.)
PARITY_SURFACES = ["mcp_tools", "cli_commands", "a2a_skills", "scorers", "transforms"]


def _load_parity(pkg: str) -> dict:
    import yaml

    path = PARITY / f"{pkg}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def _yn(flag: bool) -> str:
    return "Yes" if flag else "—"


def _cli_count(manifest: dict) -> int | None:
    s = manifest.get("cli_commands")
    if not s:
        return None
    return len(s.get("shared", [])) + len(s.get("python_only", []))


def render_block() -> str:
    parity = {p: _load_parity(p) for p in PKGS}
    lines: list[str] = [MARKER_START, ""]

    # 1. Per-package surface.
    lines += [
        "## Per-package surface",
        "",
        "Live counts (introspected from each package) side by side. `MCP` / `Exports` /"
        " `A2A` are the same numbers the llms.txt/README gate locks; `CLI` is the Python"
        " command count from the parity manifest.",
        "",
        "| Package | MCP tools | Exports | A2A skills | CLI commands | Recipes | TS port |",
        "|---|---|---|---|---|---|---|",
    ]
    suite_mcp = 0
    for p in PKGS:
        mcp, exp, skills = _mcp_tools(p), _exports(p), _a2a_skills(p)
        suite_mcp += mcp or 0
        cli = _cli_count(parity[p])
        has_recipes = (ROOT / "docs-site" / p / "recipes.mdx").exists()
        has_ts = (ROOT / "packages" / "typescript" / p).is_dir()
        lines.append(
            f"| `{p}` | {mcp if mcp is not None else '—'} | {exp if exp is not None else '—'} "
            f"| {skills if skills is not None else '—'} | {cli if cli is not None else '—'} "
            f"| {_yn(has_recipes)} | {_yn(has_ts)} |"
        )
    lines.append(f"| **suite** | **{suite_mcp}** | | | | | |")
    lines.append("")

    # 2. Cross-language parity (the disjointedness).
    lines += [
        "## Cross-language parity (Python ↔ TypeScript)",
        "",
        "Per surface: values shared by both languages vs each language's exclusives. A"
        " non-zero **Python-only** or **TS-only** is a *declared* cross-language gap"
        " (`parity/<pkg>.yaml`), not drift — but it is where the two ports diverge.",
        "",
        "| Package | Surface | Shared | Python-only | TS-only |",
        "|---|---|---|---|---|",
    ]
    for p in PKGS:
        m = parity[p]
        for surf in PARITY_SURFACES:
            body = m.get(surf)
            if not body:
                continue
            sh, po, to = len(body.get("shared", [])), len(body.get("python_only", [])), len(body.get("ts_only", []))
            lines.append(f"| `{p}` | {surf} | {sh} | {po} | {to} |")
    lines.append("")

    # 3. Auto-detected gaps & asymmetries.
    lines += ["## Gaps & asymmetries (auto-detected)", ""]
    gaps: list[str] = []

    no_recipes = [p for p in PKGS if not (ROOT / "docs-site" / p / "recipes.mdx").exists()]
    if no_recipes:
        gaps.append(f"**No recipes page:** {', '.join(f'`{p}`' for p in no_recipes)} "
                    "(thinner / auto-driven config surfaces).")

    no_a2a = [p for p in PKGS if _a2a_skills(p) is None]
    if no_a2a:
        gaps.append(f"**No A2A skills surface:** {', '.join(f'`{p}`' for p in no_a2a)}.")

    # Surfaces where a language holds exclusives -- the disjoint parts, spelled out.
    for p in PKGS:
        m = parity[p]
        for surf in PARITY_SURFACES:
            body = m.get(surf)
            if not body:
                continue
            po, to = sorted(body.get("python_only", [])), sorted(body.get("ts_only", []))
            if po:
                gaps.append(f"`{p}` **{surf}** — Python-only: {', '.join(f'`{x}`' for x in po)}.")
            if to:
                gaps.append(f"`{p}` **{surf}** — TS-only: {', '.join(f'`{x}`' for x in to)}.")

    lines += [f"- {g}" for g in gaps]
    lines += ["", MARKER_END]
    return "\n".join(lines)


def _compose(block: str) -> str:
    intro = (
        "---\n"
        'title: "Suite surface matrix"\n'
        'description: "Cross-package view of the Golden Suite: capability counts, '
        'Python/TypeScript parity, and auto-detected gaps — generated from introspection '
        'and the parity manifests, gated in CI."\n'
        "---\n\n"
        "The per-package [config matrices](/goldenmatch/config-matrix) each describe one "
        "package. This is the **cross-package** view: how the six packages line up, where "
        "the Python and TypeScript ports diverge, and which capabilities are missing where. "
        "Everything below the line is generated from live introspection + the parity "
        "manifests (`scripts/gen_suite_matrix.py`) and verified in CI, so it can't drift.\n\n"
    )
    return intro + block + "\n"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    block = render_block()
    if mode == "--write":
        PAGE.write_text(_compose(block), encoding="utf-8")
        print(f"wrote {PAGE.relative_to(ROOT)}")
        return 0
    if mode == "--check":
        current = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        fresh = _compose(block)
        if current != fresh:
            import difflib

            diff = difflib.unified_diff(
                current.splitlines(), fresh.splitlines(),
                fromfile="committed", tofile="live", lineterm="",
            )
            print("suite-matrix.mdx is STALE vs the live suite surface. "
                  "Regenerate: python scripts/gen_suite_matrix.py --write")
            print("\n".join(list(diff)[:40]))
            return 1
        print("suite-matrix.mdx OK: matches the live cross-package surface.")
        return 0
    print(f"unknown mode {mode!r} (use --write / --check)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
