"""TypeScript and Rust dead-surface candidates.

Rust symbol removal is bounded to exports that check_native_symbols already
flags as unwired: cargo-machete reasons about DEPENDENCIES, not functions, so
Rust internals stay out of scope for phase A.

Every public function here returns `list[str] | None`: `None` means NOT
MEASURED (the tool could not be run, or its invocation itself failed), a list
-- possibly empty -- means the tool ran and that is what it found. Collapsing
those two into the same `[]` is the exact defect class this whole detector
exists to catch, and it shipped inside the detector itself: with
cargo-machete and ts-prune absent from the CI runner, "unused rust deps: 0"
and "unused ts exports: 0" read as clean results when nothing had actually
been measured. See report.py's other_langs_report() for how the distinction
surfaces in the report.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent.parent


class ToolRun(NamedTuple):
    stdout: str
    returncode: int


def _run(cmd: list[str], cwd: Path | None = None) -> ToolRun | None:
    """Run a tool, returning None when the executable itself is absent, cannot
    be launched, or times out. A `ToolRun` otherwise -- even a nonzero return
    code is a real, measured result: cargo-machete in particular exits 1 to
    signal "found unused dependencies", not "failed to run"."""
    try:
        proc = subprocess.run(cmd, cwd=cwd or REPO, capture_output=True, text=True, timeout=600)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return ToolRun(proc.stdout, proc.returncode)


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


def unused_rust_deps() -> list[str] | None:
    """Crate dependencies nothing uses, per cargo-machete.

    None means NOT MEASURED. A successful cargo-machete run always emits
    explanatory text on stdout, whether it found something (the per-crate
    dependency listing) or not ("found no unused dependencies") -- so blank
    stdout only happens when the invocation itself failed: `cargo` absent
    entirely (`_run` returns None), or `cargo` present without the `machete`
    subcommand installed (cargo exits nonzero with nothing on stdout). Blank
    stdout is therefore the tell used here, not the return code -- cargo-
    machete repurposes that to mean "found something" (1) vs "clean" (0),
    the opposite of the usual "nonzero means broken" convention.
    """
    result = _run(["cargo", "machete", "--with-metadata"])
    if result is None or not result.stdout.strip():
        return None
    return _parse_machete(result.stdout)


def unwired_rust_exports() -> list[str] | None:
    """Kernel exports with no host reference, per check_native_symbols.

    None means NOT MEASURED for every package (check_native_symbols could not
    be run at all -- effectively unreachable since it's a bundled script
    invoked with the same Python interpreter running this code, but handled
    for honesty rather than assumed). A single package's own failure (no
    native-symbol registry entry, or zero scanned references -- both written
    to stderr with blank stdout) does not sink the whole signal: the other
    packages still measure and report, matching the tolerance the original
    per-package loop already had.

    Note: The parser is spot-checked, not fixture-tested — verified against real
    check_native_symbols output (5 goldencheck entries matched exactly, 39 items
    total), unlike `_parse_machete`, which has a committed fixture test.
    """
    found: list[str] = []
    measured_any = False
    for pkg in ("goldenmatch", "goldenflow", "goldencheck", "infermap", "goldenanalysis"):
        result = _run(["python", "scripts/check_native_symbols.py", pkg])
        if result is None or not result.stdout.strip():
            continue
        measured_any = True
        in_block = False
        for line in result.stdout.splitlines():
            if line.startswith("unwired"):
                in_block = True
                continue
            if in_block:
                if line.startswith("  - "):
                    found.append(f"{pkg}:{line[4:].strip()}")
                else:
                    in_block = False
    if not measured_any:
        return None
    return sorted(set(found))


def unused_ts_exports() -> list[str] | None:
    """Exported TypeScript symbols with no importer, per ts-prune.

    Invoked via `pnpm dlx ts-prune` rather than `pnpm exec ts-prune`: ts-prune
    is not a declared devDependency of the TS package, so `pnpm exec` (which
    only resolves locally-installed binaries) always failed with "command not
    found" -- silently, because that failure surfaced as ordinary nonzero-exit,
    empty-stdout output, indistinguishable downstream from a genuinely clean
    run. `pnpm dlx` fetches the package on demand instead, so the invocation
    itself succeeds whether or not ts-prune has ever been installed.

    None means NOT MEASURED. Unlike cargo-machete and check_native_symbols, a
    genuinely clean ts-prune run ALSO produces empty stdout (it prints one
    line per unused export and nothing at all when there are none), with exit
    code 0 -- so blank stdout cannot be the failure signal here without
    misreporting every real "zero findings" run as NOT MEASURED. The exit
    code is used instead: `pnpm dlx` exits 0 whenever ts-prune itself ran,
    regardless of what it found; a nonzero exit means the invocation failed
    (package fetch/network failure, or no runnable ts-prune at all).
    """
    ts_root = REPO / "packages" / "typescript" / "goldenmatch"
    if not ts_root.exists():
        return None
    result = _run(["pnpm", "dlx", "ts-prune"], cwd=ts_root)
    if result is None or result.returncode != 0:
        return None
    return sorted(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and "(used in module)" not in line
    )
