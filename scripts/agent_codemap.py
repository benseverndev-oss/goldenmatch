"""Generate (or check) docs/agent-codemap.json -- a committed structural map of the
repo's Python source, for OTHER PEOPLE'S coding agents browsing the codebase.

The config manifest (docs/agent-manifest.json) answers "what can I configure / call".
This answers the other half an agent spends turns rediscovering: "what modules
exist, what does each do, what does each define, and how are they wired" -- so an
external agent orients without grepping the tree.

It is COMMITTED (a SessionStart hook would only help the maintainer's own machine,
not a stranger's agent) and CI-gated for drift, so it ships with the repo and can't
go stale. Built from a pure `ast` walk -- no package imports, no runtime env -- so
it is cheap and deterministic. The package list comes from the config-matrix
registry, so "which packages" stays single-sourced.

Regenerate:  python scripts/agent_codemap.py --write
Check (CI):  python scripts/agent_codemap.py --check
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_matrix.registry import REGISTRY  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID = "goldenmatch.agent-codemap/v1"
CODEMAP_PATH = "docs/agent-codemap.json"

# Import names of every mapped package -> used to keep only INTRA-REPO import edges
# (an agent tracing architecture cares about goldenmatch->goldencheck, not ->numpy).
_PKG_IMPORT_NAMES = frozenset(s.src_dirs[0].rsplit("/", 1)[-1] for s in REGISTRY.values())

_SKIP_DIR_PARTS = frozenset({"tests", "__pycache__", "test"})


def _module_name(file: Path, src_root: Path) -> str:
    """packages/.../goldenmatch/core/scorer.py -> 'goldenmatch.core.scorer'."""
    rel = file.relative_to(src_root.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _intra_repo_import(module: str | None) -> str | None:
    if not module:
        return None
    top = module.split(".", 1)[0]
    return module if top in _PKG_IMPORT_NAMES else None


def _resolve_relative(node: ast.ImportFrom, current: str) -> str | None:
    """`from ..core import x` inside pkg.a.b -> the absolute base module."""
    base = current.split(".")
    # level 1 = current package (drop the module itself); level 2 = one more up, etc.
    trimmed = base[: len(base) - node.level]
    if node.module:
        trimmed = trimmed + node.module.split(".")
    return ".".join(trimmed) if trimmed else None


def _analyze(file: Path, src_root: Path) -> dict | None:
    try:
        tree = ast.parse(file.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError):
        return None
    module = _module_name(file, src_root)
    defines: list[str] = []
    imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defines.append(node.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                hit = _intra_repo_import(a.name)
                if hit:
                    imports.add(hit)
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_relative(node, module) if node.level else node.module
            hit = _intra_repo_import(target)
            if hit and hit != module:
                imports.add(hit)
    doc = ast.get_docstring(tree)
    entry: dict = {"module": module, "file": file.relative_to(ROOT).as_posix()}
    if doc:
        entry["purpose"] = doc.strip().splitlines()[0].strip()
    if defines:
        entry["defines"] = sorted(defines)
    if imports:
        entry["imports"] = sorted(imports)
    return entry


def _walk_package(src_root: Path) -> list[dict]:
    modules: list[dict] = []
    for file in src_root.rglob("*.py"):
        if _SKIP_DIR_PARTS & set(file.parts):
            continue
        if file.name.startswith("test_") or file.name.endswith("_test.py") or file.name == "conftest.py":
            continue
        entry = _analyze(file, src_root)
        if entry:
            modules.append(entry)
    return sorted(modules, key=lambda m: m["module"])


def build_codemap() -> dict:
    packages = {}
    for name, spec in REGISTRY.items():
        src_root = ROOT / spec.src_dirs[0]
        modules = _walk_package(src_root) if src_root.exists() else []
        packages[name] = {
            "root": spec.src_dirs[0],
            "module_count": len(modules),
            "modules": modules,
        }
    return {
        "schema": SCHEMA_ID,
        "note": (
            "Structural map of the repo's Python source for coding agents. Generated "
            "from a static AST walk; DO NOT EDIT. Regenerate: python scripts/agent_codemap.py --write"
        ),
        "packages": packages,
    }


def codemap_json() -> str:
    return json.dumps(build_codemap(), indent=2, ensure_ascii=False) + "\n"


def _path() -> Path:
    return ROOT / CODEMAP_PATH


def write_codemap() -> Path:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(codemap_json(), encoding="utf-8", newline="\n")
    return p


def codemap_is_current() -> bool:
    p = _path()
    return p.exists() and p.read_text(encoding="utf-8") == codemap_json()


def main(argv: list[str]) -> int:
    if "--write" in argv:
        print(f"wrote {write_codemap()}")
        return 0
    if "--check" in argv:
        if codemap_is_current():
            print(f"OK    agent code map: {CODEMAP_PATH}")
            return 0
        print(f"STALE agent code map: {CODEMAP_PATH}", file=sys.stderr)
        print("::error::agent code map stale. Run: python scripts/agent_codemap.py --write",
              file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
