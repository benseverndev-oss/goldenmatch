#!/usr/bin/env python3
"""Enforce per-package version lockstep across every file that declares it.

The CLAUDE.md gotchas document this rule repeatedly ("bump in lockstep",
"version lives in THREE spots") but nothing enforced it -- and it drifted in
production: goldenflow shipped 1.1.x with ``pyproject.toml`` = 1.1.2 while
``goldenflow/__init__.py`` said 1.1.1 (fixed in 1.1.5). This is the gate.

For every package it discovers the version-bearing files and asserts they all
agree:
  - Python dist packages (``packages/python/<pkg>/``):
      pyproject.toml ``[project].version`` == ``<importdir>/__init__.py``
      ``__version__`` == ``server.json`` ``.version`` (+ nested ``packages[].version``)
  - Native maturin crates (``packages/rust/extensions/<crate>/`` with BOTH a
      Cargo.toml and a pyproject.toml):
      Cargo.toml ``[package].version`` == pyproject.toml ``[project].version``
      == any ``__init__.py`` ``__version__`` fallback under the crate.

Exit 1 (listing every drift) if any package is inconsistent; 0 otherwise.
Run: ``python scripts/check_version_consistency.py``
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_VERSION_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


def _pyproject_version(path: Path) -> str | None:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("project", {}).get("version")


def _cargo_version(path: Path) -> str | None:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("package", {}).get("version")


def _init_version(path: Path) -> str | None:
    m = _VERSION_RE.search(path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


# TS package-version declarations we enforce against package.json:
#   const VERSION = "x.y.z" / export const VERSION = ... (cli + api-server banners)
#   .version("x.y.z")                                    (commander CLI)
#   version: "x.y.z"                                     (A2A AgentCard + MCP serverInfo)
# Restricted to THREE-part semver so it never matches MCP `protocolVersion`
# ("2024-11-05"), two-part schema tags, or numeric wire-format versions.
_TS_VERSION_RE = re.compile(
    r"""(?:(?:export\s+)?const\s+VERSION\s*=\s*|\.version\(\s*|\bversion:\s*)["'](\d+\.\d+\.\d+)["']"""
)


def _package_json_version(path: Path) -> str | None:
    return json.loads(path.read_text(encoding="utf-8")).get("version")


def _ts_src_versions(src_dir: Path) -> list[tuple[str, str]]:
    """Every enforced version literal under a TS package's src/, labelled by
    file:line so a drift points at the exact spot to fix."""
    found: list[tuple[str, str]] = []
    for ts in sorted(src_dir.rglob("*.ts")):
        if ts.name.endswith((".test.ts", ".spec.ts", ".d.ts")):
            continue
        for i, line in enumerate(ts.read_text(encoding="utf-8").splitlines(), 1):
            m = _TS_VERSION_RE.search(line)
            if m is not None:
                found.append((f"{ts.relative_to(src_dir.parent)}:{i}", m.group(1)))
    return found


def _check(name: str, sources: list[tuple[str, str | None]], errors: list[str]) -> None:
    present = [(label, v) for label, v in sources if v is not None]
    distinct = {v for _, v in present}
    if len(distinct) > 1:
        detail = ", ".join(f"{label}={v}" for label, v in present)
        errors.append(f"{name}: version drift -> {detail}")


def main() -> int:
    errors: list[str] = []
    checked = 0

    # --- Python dist packages ---
    for pyproject in sorted((ROOT / "packages" / "python").glob("*/pyproject.toml")):
        pkg_dir = pyproject.parent
        sources: list[tuple[str, str | None]] = [("pyproject.toml", _pyproject_version(pyproject))]
        # Top-level import package __init__ (only the canonical one carries __version__).
        for init in sorted(pkg_dir.glob("*/__init__.py")):
            v = _init_version(init)
            if v is not None:
                sources.append((str(init.relative_to(pkg_dir)), v))
        server = pkg_dir / "server.json"
        if server.exists():
            data = json.loads(server.read_text(encoding="utf-8"))
            if "version" in data:
                sources.append(("server.json:.version", data["version"]))
            for i, entry in enumerate(data.get("packages", [])):
                if "version" in entry:
                    sources.append((f"server.json:packages[{i}].version", entry["version"]))
        _check(f"python/{pkg_dir.name}", sources, errors)
        checked += 1

    # --- Native maturin crates (Cargo.toml + pyproject.toml) ---
    for cargo in sorted((ROOT / "packages" / "rust" / "extensions").glob("*/Cargo.toml")):
        crate_dir = cargo.parent
        pyproject = crate_dir / "pyproject.toml"
        if not pyproject.exists():
            continue  # pure crate -- no Python version to keep in lockstep
        cargo_v = _cargo_version(cargo)
        if cargo_v is None:
            continue  # virtual/workspace manifest with no [package]
        sources = [("Cargo.toml", cargo_v), ("pyproject.toml", _pyproject_version(pyproject))]
        for init in sorted(crate_dir.glob("**/__init__.py")):
            v = _init_version(init)
            if v is not None:
                sources.append((str(init.relative_to(crate_dir)), v))
        _check(f"native/{crate_dir.name}", sources, errors)
        checked += 1

    # --- TypeScript packages (package.json + src version literals) ---
    # The Python/Rust gates above never saw TS, so cli.ts / api-server / A2A
    # AgentCard / MCP serverInfo versions drifted from package.json unnoticed.
    for pkgjson in sorted((ROOT / "packages" / "typescript").glob("*/package.json")):
        pkg_dir = pkgjson.parent
        pkg_v = _package_json_version(pkgjson)
        if pkg_v is None:
            continue
        sources = [("package.json", pkg_v)]
        src = pkg_dir / "src"
        if src.is_dir():
            sources.extend(_ts_src_versions(src))
        _check(f"ts/{pkg_dir.name}", sources, errors)
        checked += 1

    if errors:
        print(f"Version consistency check FAILED ({len(errors)} of {checked} packages drifted):")
        for e in errors:
            print(f"  - {e}")
        print("\nBump every version-bearing file for the package in lockstep (see the")
        print("package's CLAUDE.md). This is the gate goldenflow 1.1.x lacked.")
        return 1

    print(f"Version consistency OK: {checked} packages, all files in lockstep.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
