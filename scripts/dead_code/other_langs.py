"""TypeScript and Rust dead-surface candidates.

Rust symbol removal is bounded to exports that check_native_symbols already
flags as unwired: cargo-machete reasons about DEPENDENCIES, not functions, so
Rust internals stay out of scope for phase A.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def _run(cmd: list[str], cwd: Path | None = None) -> str | None:
    """Run a tool, returning None when it is absent or fails."""
    try:
        proc = subprocess.run(cmd, cwd=cwd or REPO, capture_output=True, text=True, timeout=600)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout


def _parse_machete(output: str) -> list[str]:
    """Parse cargo-machete --with-metadata output to extract unused dependency names.

    Cargo-machete outputs a section header (crate name + Cargo.toml path) followed
    by tab-indented dependency names. This function extracts all dependency names.
    """
    found: list[str] = []
    for line in output.splitlines():
        # Dependencies are tab-indented; section headers are not.
        # Strip leading tab and any trailing whitespace.
        if line.startswith("\t"):
            dep = line.lstrip("\t").strip()
            if dep:
                found.append(dep)
    return sorted(set(found))


def unused_rust_deps() -> list[str]:
    """Crate dependencies nothing uses, per cargo-machete."""
    out = _run(["cargo", "machete", "--with-metadata"])
    if not out:
        return []
    return _parse_machete(out)


def unwired_rust_exports() -> list[str]:
    """Kernel exports with no host reference, per check_native_symbols."""
    found: list[str] = []
    for pkg in ("goldenmatch", "goldenflow", "goldencheck", "infermap", "goldenanalysis"):
        out = _run(["python", "scripts/check_native_symbols.py", pkg])
        if not out:
            continue
        in_block = False
        for line in out.splitlines():
            if line.startswith("unwired"):
                in_block = True
                continue
            if in_block:
                if line.startswith("  - "):
                    found.append(f"{pkg}:{line[4:].strip()}")
                else:
                    in_block = False
    return sorted(set(found))


def unused_ts_exports() -> list[str]:
    """Exported TypeScript symbols with no importer, per ts-prune.

    Note: The parser is UNVERIFIED — ts-prune is not installed in this environment,
    so an empty result may indicate no findings or that the parser has never run.
    """
    ts_root = REPO / "packages" / "typescript" / "goldenmatch"
    if not ts_root.exists():
        return []
    out = _run(["pnpm", "exec", "ts-prune"], cwd=ts_root)
    if not out:
        return []
    return sorted(
        line.strip() for line in out.splitlines() if line.strip() and "(used in module)" not in line
    )
