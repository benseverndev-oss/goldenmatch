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
  - Committed ``Cargo.lock`` files: every LOCAL crate a lock describes must be
      pinned at the version its ``Cargo.toml`` declares.

``Cargo.lock`` was the spot this gate did not look at, and it drifted on five
crates at once (goldenmatch-native pinned 0.1.21 against a declared 0.2.0, plus
analysis-native, goldencheck-native, goldengraph-native, goldenflow-native).
Bumping ``version`` in a Cargo.toml does not rewrite the lock, and nothing
forced a regeneration: every ``--locked`` in ``.github/workflows/`` is a
``cargo install`` of a TOOL, never a build of our own crates. So a stale pin
survives until a consumer builds with ``--locked`` and hard-errors. Five at
once is the tell that it was never checked, not that someone slipped.

This is a textual check on purpose. ``cargo metadata --locked`` proves the same
thing but needs a toolchain and a warm registry cache for every crate tree,
which is exactly why no lane runs it.

Exit 1 (listing every drift) if any package is inconsistent; 0 otherwise.
Run: ``python scripts/check_version_consistency.py``
"""
from __future__ import annotations

import json
import re
import subprocess
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


# A Cargo.lock `[[package]]` entry. Matched textually rather than via tomllib so
# the check stays independent of lock format version quirks.
_LOCK_ENTRY_RE = re.compile(r'^name = "([^"]+)"\nversion = "([^"]+)"', re.M)

# A lock describing no local crate would pass vacuously, so a scan that finds
# almost nothing means discovery broke rather than that the repo is clean.
# Same posture as check_workflow_yaml.py's file-count floor.
_MIN_TRACKED_LOCKS = 10


def _tracked_cargo_locks() -> list[Path]:
    """Committed Cargo.lock files. Untracked locks are build artefacts."""
    out = subprocess.run(
        ["git", "ls-files", "*Cargo.lock"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [ROOT / p for p in out]


def _local_crate_versions(lock_dir: Path) -> dict[str, tuple[str, Path]]:
    """Crates declared under ``lock_dir``: name -> (declared version, manifest)."""
    found: dict[str, tuple[str, Path]] = {}
    for toml in sorted(lock_dir.rglob("Cargo.toml")):
        if "target" in toml.parts:
            continue
        try:
            pkg = tomllib.loads(toml.read_text(encoding="utf-8")).get("package", {})
        except tomllib.TOMLDecodeError:
            continue
        name, version = pkg.get("name"), pkg.get("version")
        if name and version:
            found[name] = (version, toml)
    return found


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


_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
# A requirement is floored if it carries ANY version or URL constraint. "!="
# alone is deliberately NOT enough: it excludes a bad release without
# establishing a minimum, so the resolver may still pick something older than
# the symbols the code imports.
_FLOOR_RE = re.compile(r"(>=|==|~=|>|@|===)")


def _workspace_dist_names() -> dict[str, Path]:
    """Distribution name -> its pyproject, for every in-repo Python package."""
    out: dict[str, Path] = {}
    for pyproject in sorted((ROOT / "packages" / "python").glob("*/pyproject.toml")):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        name = data.get("project", {}).get("name")
        if name:
            out[name.lower().replace("_", "-")] = pyproject
    return out


def _check_internal_floors(errors: list[str]) -> int:
    """Every dependency on a SIBLING workspace package must carry a floor.

    infermap 0.7.0 shipped to PyPI unimportable: it declared a bare
    ``goldencheck-types`` while importing ``UNKNOWN_ROLE``, which only 0.3.0
    exports, so a clean resolve took 0.1.0 and ``import infermap`` raised.

    It stayed invisible in every environment that already had a newer
    goldencheck-types on the path -- which is every environment we develop and
    test in, because the workspace install satisfies siblings from local
    source. That is precisely why it needs a STATIC gate: no test run inside
    this repo can reproduce a fresh outside resolve.

    Scoped to INTERNAL dependencies on purpose. A third-party floor is a
    judgement call about a maintainer we do not control; a sibling floor is a
    fact about code in this repo, and a missing one is always a defect.
    """
    local = _workspace_dist_names()
    checked = 0
    for name, pyproject in sorted(local.items()):
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        groups: list[tuple[str, list]] = [("dependencies", project.get("dependencies") or [])]
        for extra, reqs in (project.get("optional-dependencies") or {}).items():
            groups.append(("optional-dependencies." + extra, reqs))
        for group, reqs in groups:
            for req in reqs:
                m = _REQ_NAME_RE.match(req.strip())
                if not m:
                    continue
                dep = m.group(1).lower().replace("_", "-")
                if dep not in local or dep == name:
                    continue
                if not _FLOOR_RE.search(req):
                    errors.append(
                        f"{pyproject.relative_to(ROOT)} [{group}]: {req!r} depends "
                        f"on workspace package {dep!r} with no version floor"
                    )
        checked += 1
    return checked


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

    # --- Committed Cargo.lock pins vs declared crate versions ---
    locks = _tracked_cargo_locks()
    if len(locks) < _MIN_TRACKED_LOCKS:
        print(
            f"ERROR: found only {len(locks)} tracked Cargo.lock files (expected "
            f">= {_MIN_TRACKED_LOCKS}). The scan is broken, not the repo.",
            file=sys.stderr,
        )
        return 2
    for lock in locks:
        pinned = dict(_LOCK_ENTRY_RE.findall(lock.read_text(encoding="utf-8")))
        for name, (declared, toml) in _local_crate_versions(lock.parent).items():
            # Only crates the lock actually describes; a lock legitimately omits
            # crates outside its own dependency graph.
            if name in pinned:
                _check(
                    f"cargo-lock/{toml.parent.relative_to(ROOT)}",
                    [
                        (str(toml.relative_to(ROOT)), declared),
                        (str(lock.relative_to(ROOT)), pinned[name]),
                    ],
                    errors,
                )
        checked += 1

    # --- Internal dependency floors (a different defect from lockstep drift) ---
    floor_errors: list[str] = []
    _check_internal_floors(floor_errors)
    if floor_errors:
        print(f"Internal dependency floor check FAILED ({len(floor_errors)}):")
        for e in floor_errors:
            print(f"  - {e}")
        print()
        print("A bare sibling dependency resolves to the OLDEST published version")
        print("for anyone installing from an index, while every workspace env")
        print("silently satisfies it from local source. Give it a >= floor at the")
        print("version that exports the symbols you import.")

    if errors:
        print(f"Version consistency check FAILED ({len(errors)} problem(s) across {checked} packages):")
        for e in errors:
            print(f"  - {e}")
        print("\nBump every version-bearing file for the package in lockstep (see the")
        print("package's CLAUDE.md). This is the gate goldenflow 1.1.x lacked.")
        return 1

    if floor_errors:
        return 1

    print(f"Version consistency OK: {checked} packages, all files in lockstep,")
    print("and every workspace-internal dependency carries a version floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
