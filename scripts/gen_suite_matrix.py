#!/usr/bin/env python3
"""Generate the repo-level SUITE SURFACE MATRIX -- the cross-package view that no
per-package gate shows: capability counts side by side, Python<->TypeScript
disjointedness, and auto-detected gaps/asymmetries.

Everything is derived from STATIC, import-free sources, so the generated block is
byte-identical in every environment (no dependency on which optional extras a
given env installed -- an early version live-imported `<pkg>.mcp.server` and
flapped between the dev box and CI, which lacks the [mcp] extra):
  - MCP tool + CLI counts and the cross-language partition from parity/<pkg>.yaml
    (`shared` + `python_only` = the Python count; api_parity keeps it live-accurate);
  - `__all__` export count and A2A skills via AST (no import);
  - file presence for the recipes / TS-port columns.

`--check` fails if docs-site/suite-matrix.mdx drifts from those sources. This is
the capstone of the config-matrix arc: one place that tracks inconsistency,
disjointedness, and gaps ACROSS packages.

Run:
  python scripts/gen_suite_matrix.py --write    # regenerate the page
  python scripts/gen_suite_matrix.py --check     # CI drift gate
"""
from __future__ import annotations

import ast
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


# --- Surface introspection. MCP + CLI counts come from the parity manifests
#     (static, env-stable, and gated by api_parity to match the live surface), NOT
#     a live `import <pkg>.mcp.server` -- that needs the [mcp] extra, which the CI
#     config_matrix sync does not install, so it would flap between environments.
#     Exports is the runtime `__all__` (base import, no extra); skills is AST-parsed.
def _py_count(manifest: dict, surface: str) -> int | None:
    body = manifest.get(surface)
    if not body:
        return None
    return len(body.get("shared", [])) + len(body.get("python_only", []))


def _exports(pkg: str) -> int | None:
    # AST-count the `__all__` literal (no import -> env-stable). A `*spread` element
    # counts as one, so this is the public-name count as written, which is what a
    # cross-package overview wants and never flaps by environment.
    init = PKG_DIR / pkg / pkg / "__init__.py"
    if not init.exists():
        return None
    try:
        tree = ast.parse(init.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                return len(node.value.elts)
    return None


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


def _surface_union(manifest: dict, surface: str) -> set[str]:
    body = manifest.get(surface) or {}
    return set(body.get("shared", [])) | set(body.get("python_only", [])) | set(body.get("ts_only", []))


def _substrate_lines(gm: dict) -> list[str]:
    """The compute-substrate view: the Rust/-core kernel is the REFERENCE, and every
    exposed scorer is classified by whether a kernel backs it (fast path) or it is a
    pure-language fallback. Anchored on the `scorer_kernels` parity surface."""
    kern = gm.get("scorer_kernels") or {}
    k_shared = set(kern.get("shared", []))
    k_py = set(kern.get("python_only", []))
    k_ts = set(kern.get("ts_only", []))
    exposed = _surface_union(gm, "scorers")
    fallback = sorted(exposed - k_shared - k_py - k_ts)

    def fmt(names):
        return ", ".join(f"`{n}`" for n in sorted(names)) or "—"

    total = len(exposed)
    backed = len(k_shared | k_py | k_ts)
    return [
        "## Compute substrate — the Rust `-core` kernel is the reference",
        "",
        "The Rust / Arrow-native / fused `-core` kernels are the source of truth for"
        " scoring; each language surface either dispatches to the kernel (the fast path)"
        " or runs a byte-identical pure-language **fallback**. This tracks which scorers"
        " a kernel actually backs vs which are fallback-only. Sources: Python"
        " `_NATIVE_SCORER_IDS` (arrow bucket kernel) and TS `WASM_COVERED_SCORERS`"
        " (`-core` WASM), gated as the `scorer_kernels` api_parity surface.",
        "",
        f"**{backed} of {total} scorers are kernel-backed** (the reference fast path); "
        f"the other {total - backed} are pure-language fallbacks with no `-core` kernel.",
        "",
        "| Substrate | Scorers |",
        "|---|---|",
        f"| Rust `-core` kernel — Python + TS | {fmt(k_shared)} |",
        f"| Rust `-core` kernel — Python arrow-native only (TS falls back) | {fmt(k_py)} |",
        f"| WASM `-core` kernel — TS only (Python falls back) | {fmt(k_ts)} |",
        f"| Language fallback — no `-core` kernel | {fmt(fallback)} |",
        "",
    ]


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
        mcp, exp, skills = _py_count(parity[p], "mcp_tools"), _exports(p), _a2a_skills(p)
        suite_mcp += mcp or 0
        cli = _py_count(parity[p], "cli_commands")
        has_recipes = (ROOT / "docs-site" / p / "recipes.mdx").exists()
        has_ts = (ROOT / "packages" / "typescript" / p).is_dir()
        lines.append(
            f"| `{p}` | {mcp if mcp is not None else '—'} | {exp if exp is not None else '—'} "
            f"| {skills if skills is not None else '—'} | {cli if cli is not None else '—'} "
            f"| {_yn(has_recipes)} | {_yn(has_ts)} |"
        )
    lines.append(f"| **suite** | **{suite_mcp}** | | | | | |")
    lines.append("")

    # 2. Compute substrate -- the Rust `-core` kernel as the reference.
    lines += _substrate_lines(parity["goldenmatch"])

    # 3. Cross-language parity (the disjointedness).
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
        'description: "Cross-package view of the Golden Suite: capability counts, the Rust '
        '-core compute substrate, Python/TypeScript parity, and auto-detected gaps — '
        'generated and gated in CI."\n'
        'keywords: ["golden suite", "suite matrix", "cross-package", "parity", "capabilities", "rust core", "reference"]\n'
        "---\n\n"
        "The per-package [config matrices](/goldenmatch/config-matrix) each describe one "
        "package. This is the **cross-package** view. The anchor is the **Rust / "
        "Arrow-native / fused `-core` kernel**: it is the reference implementation, and the "
        "Python and TypeScript surfaces are ports that either dispatch to it (the fast path) "
        "or run a byte-identical pure-language fallback. So the sections below read against "
        "that reference — how the packages line up, which scorers a kernel actually backs vs "
        "which are fallback-only, where the two language ports diverge, and what's missing "
        "where. Everything below the line is generated from the parity manifests + AST "
        "(`scripts/gen_suite_matrix.py`) and verified in CI, so it can't drift.\n\n"
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
