#!/usr/bin/env python3
"""Generate (or check) the per-package native-acceleration docs from the loaders.

  python scripts/gen_native_docs.py --write            # regenerate all pages
  python scripts/gen_native_docs.py --check            # exit 1 if any page is stale
  python scripts/gen_native_docs.py --write -p infermap  # one package

Each ``docs-site/<pkg>/native.mdx`` is rendered from that package's
``_native_loader.py`` — the ``_COMPONENT_SYMBOLS`` map (component -> kernel
symbol(s)), the ``_GATED_ON`` byte-exact sign-off set, and the ``_FALLBACK_ONLY``
known-divergent set — so the reference-mode gate and the page cannot drift. The
loader is the single source of truth; CI runs ``--check`` (wired into
``check_docs_consistency.py`` and the ``docs`` path filter), mirroring the
``gen_lint_docs.py`` / config-linter pattern.

Parsing is static (``ast`` only, stdlib) so the generator runs in the docs job
without importing the packages or building any wheel.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs-site"

# --- per-package metadata (source of truth: the loaders + pyproject extras) ---
PACKAGES: dict[str, dict[str, str]] = {
    "goldenmatch": {
        "product": "GoldenMatch",
        "loader": "packages/python/goldenmatch/goldenmatch/core/_native_loader.py",
        "env": "GOLDENMATCH_NATIVE",
        "pip_extra": "goldenmatch[native]",
        "wheel": "goldenmatch-native",
        "in_tree_mod": "goldenmatch._native",
        "wheel_mod": "goldenmatch_native._native",
        "crate": "packages/rust/extensions/native",
        "extra_note": (
            "The Fellegi-Sunter block-scoring path has its own gate, "
            "`GOLDENMATCH_FS_NATIVE` (default-on; the Rust rapidfuzz path is the "
            "reference, `=0` forces the pure-Python fallback)."
        ),
    },
    "goldenflow": {
        "product": "GoldenFlow",
        "loader": "packages/python/goldenflow/goldenflow/core/_native_loader.py",
        "env": "GOLDENFLOW_NATIVE",
        "pip_extra": "goldenflow[native]",
        "wheel": "goldenflow-native",
        "in_tree_mod": "goldenflow._native",
        "wheel_mod": "goldenflow_native._native",
        "crate": "packages/rust/extensions/native-flow",
        "extra_note": "",
    },
    "infermap": {
        "product": "InferMap",
        "loader": "packages/python/infermap/infermap/_native_loader.py",
        "env": "INFERMAP_NATIVE",
        "pip_extra": "infermap[native]",
        "wheel": "infermap-native",
        "in_tree_mod": "infermap._native",
        "wheel_mod": "infermap_native._native",
        "crate": "packages/rust/extensions/infermap-native",
        "extra_note": "",
    },
}


# --- static extraction ------------------------------------------------------
def _lit(node: ast.AST):
    """Evaluate a literal-ish node, including ``frozenset({...})`` / ``set({...})``
    wrappers the loaders use (which ``ast.literal_eval`` rejects)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Set):
        return {_lit(e) for e in node.elts}
    if isinstance(node, ast.Tuple):
        return tuple(_lit(e) for e in node.elts)
    if isinstance(node, ast.List):
        return [_lit(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {_lit(k): _lit(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("frozenset", "set"):
        return set(_lit(node.args[0])) if node.args else set()
    raise ValueError(f"unsupported node {ast.dump(node)}")


def _extract(loader_path: Path, required: set[str], optional: set[str]) -> dict:
    names = required | optional
    tree = ast.parse(loader_path.read_text(encoding="utf-8"))
    out: dict = {}
    for node in tree.body:  # module-level assignments only
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in names:
                    out[tgt.id] = _lit(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in names and node.value is not None:
                out[node.target.id] = _lit(node.value)
    missing = required - out.keys()
    if missing:
        raise SystemExit(f"::error::{loader_path}: could not extract {sorted(missing)}")
    return out


def _symbols_str(sym) -> str:
    """Render a component's symbol(s) as inline code (str or tuple/list)."""
    if isinstance(sym, (tuple, list)):
        return ", ".join(f"`{s}`" for s in sym)
    return f"`{sym}`"


# --- rendering --------------------------------------------------------------
def render(pkg: str) -> str:
    meta = PACKAGES[pkg]
    data = _extract(
        ROOT / meta["loader"],
        required={"_COMPONENT_SYMBOLS", "_GATED_ON"},
        optional={"_FALLBACK_ONLY"},
    )
    components: dict = data["_COMPONENT_SYMBOLS"]
    gated: set = data["_GATED_ON"]
    fallback: set = data.get("_FALLBACK_ONLY", set())
    env = meta["env"]
    loader_rel = meta["loader"].split("packages/python/")[-1]

    kw = f'["{pkg}", "native", "rust", "arrow", "pyo3", "performance", "reference-mode"]'
    header = (
        "---\n"
        'title: "Native acceleration"\n'
        f'description: "{meta["product"]}\'s optional compiled Rust/Arrow runtime — '
        f"which components run native, the `{env}` gate, and how parity is enforced. "
        'Generated from the native loader; do not edit by hand."\n'
        f"keywords: {kw}\n"
        "---\n\n"
        "{/* GENERATED FILE — do not edit. Source of truth: "
        f"{loader_rel}. Regenerate: python scripts/gen_native_docs.py --write */}}\n\n"
    )

    body: list[str] = [
        f"{meta['product']} is pure-Python by default. An optional compiled kernel "
        f"(Rust + PyO3/Arrow, crate `{meta['crate']}`) accelerates the CPU-heavy "
        "components below. Under reference-mode the compiled path is the reference "
        "implementation and pure-Python is the byte-identical fallback — output is "
        "the same either way; native only changes wall-clock.\n",
        f"```bash\npip install {meta['pip_extra']}\n```\n",
        "## The gate\n",
        f"One env var, `{env}`, read in `{loader_rel}`:\n\n"
        f"- `{env}=auto` (default, or unset) — run native for any component whose "
        "kernel symbol is present on the loaded wheel, except the known-divergent "
        "components below.\n"
        f"- `{env}=0` — force the pure-Python fallback everywhere.\n"
        f"- `{env}=1` — require native; raise if the kernel isn't importable (the "
        "CI parity lane).\n",
        "The kernel is discovered two ways, in order: the in-tree build "
        f"`{meta['in_tree_mod']}` (local dev / parity lane), then the distributed "
        f"`{meta['wheel_mod']}` wheel (`pip install {meta['pip_extra']}`). When "
        "neither is importable, every path runs pure-Python unchanged.\n",
    ]
    if meta["extra_note"]:
        body.append(f"{meta['extra_note']}\n")

    body.append("## Components\n")
    body.append(
        "Each component maps to the native kernel symbol(s) its `auto` call site "
        "invokes (the *floor* symbol first — a component is native-capable when "
        "**any** listed symbol is present, so an older wheel stays wheel-skew "
        "safe). A ✓ in **Parity-signed** marks a component that cleared the "
        "byte-exact sign-off recorded in `_GATED_ON`.\n"
    )
    body.append("| Component | Kernel symbol(s) | Parity-signed |")
    body.append("|---|---|---|")
    for comp, sym in components.items():
        mark = "✓" if comp in gated else ""
        body.append(f"| `{comp}` | {_symbols_str(sym)} | {mark} |")
    body.append("")

    if fallback:
        listed = ", ".join(f"`{c}`" for c in sorted(fallback))
        body.append(
            f"**Kept pure-Python under `auto` ({env} reference-mode):** {listed}. "
            "These carry (or could bind) a native symbol that is known to diverge "
            "from the pure-Python reference, so they stay on the fallback path "
            f"until their parity battery is green — reachable only via `{env}=1`.\n"
        )

    body.append("## How parity stays honest\n")
    body.append(
        "A component joins the native path only after a parity test proves its "
        "kernel is byte-identical (or integer-exact) to the pure-Python reference. "
        f"CI runs a `{env}=1` lane that builds the wheel and asserts native == "
        "pure-Python, and `scripts/check_native_symbols.py` reconciles the host's "
        "kernel references against the crate's `wrap_pyfunction!` exports so a "
        "referenced-but-unregistered symbol fails loudly. Because output is "
        "identical with or without the wheel, toggling the gate never changes a "
        "result — only speed.\n"
    )

    return header + "\n".join(body).rstrip() + "\n"


def _page_path(pkg: str) -> Path:
    return DOCS / pkg / "native.mdx"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="regenerate the page(s)")
    ap.add_argument("--check", action="store_true", help="exit 1 if any page is stale")
    ap.add_argument("-p", "--package", choices=[*PACKAGES, "all"], default="all")
    args = ap.parse_args(argv)

    pkgs = list(PACKAGES) if args.package == "all" else [args.package]

    if args.write:
        for pkg in pkgs:
            p = _page_path(pkg)
            p.write_text(render(pkg), encoding="utf-8", newline="\n")
            print(f"wrote {p.relative_to(ROOT)}")
        return 0

    if args.check:
        stale = []
        for pkg in pkgs:
            p = _page_path(pkg)
            if not p.exists() or p.read_text(encoding="utf-8") != render(pkg):
                stale.append(str(p.relative_to(ROOT)))
        if stale:
            print(
                "::error::native docs stale vs the loaders: "
                + ", ".join(stale)
                + ". Run: python scripts/gen_native_docs.py --write",
                file=sys.stderr,
            )
            return 1
        print(f"native docs are current: {', '.join(pkgs)}")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
