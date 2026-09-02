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

import json
import subprocess
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent.parent
TS_ROOT = REPO / "packages" / "typescript" / "goldenmatch"


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


def _ts_public_entry_files() -> set[str] | None:
    """Published entry-point source files, resolved from package.json's `exports` map.

    None means NOT MEASURED: package.json is missing, unreadable, or has no
    `exports` map -- distinct from "measured, package has zero public
    entries" (an empty set), which would silently reclassify every finding
    as actionable instead of leaving the split undetermined.

    Each exports target's `types` field names a `dist/<stem>.d.ts` file that
    tsup's `entry` map (tsup.config.ts) builds 1:1 from `src/<stem>.ts` --
    every entry in that map has its dist-relative key equal the src value's
    path with only the extension swapped, so the src file is derived by
    string substitution rather than by parsing tsup.config.ts itself: strip
    the `./dist/` prefix and `.d.ts` suffix, wrap the remainder in
    `src/<stem>.ts`. This walks the whole `exports` map, not one hardcoded
    subpath -- the package publishes 17 entries (the main barrel, the `core`
    and `node` barrels, and 14 single-file opt-in wasm/leaf subpaths under
    `./core/*`), and a hardcoded `src/node/index.ts` would silently miss the
    other 16, which is exactly the mistake this function exists to avoid.
    """
    pkg_path = TS_ROOT / "package.json"
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    exports = pkg.get("exports")
    if not isinstance(exports, dict):
        return None
    prefix, suffix = "./dist/", ".d.ts"
    entries: set[str] = set()
    for target in exports.values():
        if not isinstance(target, dict):
            continue
        types_path = target.get("types")
        if not (isinstance(types_path, str) and types_path.startswith(prefix)):
            continue
        if not types_path.endswith(suffix):
            continue
        stem = types_path[len(prefix) : -len(suffix)]
        entries.add(f"src/{stem}.ts")
    return entries


def _ts_prune_finding_file(line: str) -> str:
    """The file a ts-prune finding line names, normalized for comparison
    against `_ts_public_entry_files()`'s forward-slash `src/...` set.

    ts-prune emits platform-native separators -- backslash on Windows, where
    this detector was profiled (`.superpowers/sdd/2026-09-01-dead-code-audit-
    phase-a/tsprune-raw.txt`) -- and a leading separator (`\\src\\...` /
    `./src/...`) that a bare string-equality check would leave in place,
    silently failing to match every entry file.
    """
    left = line.split(" - ", 1)[0]
    path = left.rsplit(":", 1)[0].replace("\\", "/").lstrip("/")
    if path.startswith("./"):
        path = path[2:]
    return path


def _split_ts_prune_findings(stdout: str, entry_files: set[str]) -> tuple[list[str], list[str]]:
    """Partition raw ts-prune findings into (actionable, public_inventory).

    A finding tagged `(used in module)` has a real internal reference and is
    dropped entirely, same as the pre-split behavior. Of what remains, a
    finding in one of the package's published entry files (see
    `_ts_public_entry_files()`) is public API -- reported in the inventory
    bucket but never actioned in this phase, mirroring
    `report.public_export_inventory()` on the Python side. This catches more
    than the obvious `src/node/index.ts` barrel: `src/core/documentsWasm.ts`,
    `src/core/sketchWasm.ts` and `src/core/suggestWasm.ts` are THEMSELVES
    published entry points (`./core/documents-wasm`, `./core/sketch-wasm`,
    `./core/suggest-wasm`), so an export ts-prune flags there (e.g.
    `documentsWasmAvailable`) is public API too, even though it lives outside
    the obviously-huge barrel -- a naive "everything in src/node/index.ts is
    public, everything else is actionable" split would misclassify these
    three as deletable.
    """
    actionable: list[str] = []
    public_inventory: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or "(used in module)" in line:
            continue
        if _ts_prune_finding_file(line) in entry_files:
            public_inventory.append(line)
        else:
            actionable.append(line)
    return sorted(set(actionable)), sorted(set(public_inventory))


def _ts_prune_split() -> tuple[list[str], list[str]] | None:
    """Run ts-prune once and partition its findings. None means NOT MEASURED.

    Invoked via `pnpm dlx ts-prune` rather than `pnpm exec ts-prune`: ts-prune
    is not a declared devDependency of the TS package, so `pnpm exec` (which
    only resolves locally-installed binaries) always failed with "command not
    found" -- silently, because that failure surfaced as ordinary nonzero-exit,
    empty-stdout output, indistinguishable downstream from a genuinely clean
    run. `pnpm dlx` fetches the package on demand instead, so the invocation
    itself succeeds whether or not ts-prune has ever been installed.

    A genuinely clean ts-prune run produces empty stdout (it prints one line
    per unused export and nothing at all when there are none), with exit code
    0 -- so blank stdout cannot be the failure signal here without
    misreporting every real "zero findings" run as NOT MEASURED. The exit
    code is used instead: `pnpm dlx` exits 0 whenever ts-prune itself ran,
    regardless of what it found; a nonzero exit means the invocation failed
    (package fetch/network failure, or no runnable ts-prune at all).

    Also NOT MEASURED when ts-prune ran fine but `_ts_public_entry_files()`
    could not resolve the package's entry files: a finding cannot be
    correctly classified without that set, so nothing is reported rather than
    reporting every finding as actionable (silently including public API) or
    every finding as public (silently hiding real candidates).

    Called once per public function below (`unused_ts_exports()` and
    `ts_public_export_inventory()`), so a caller that wants both pays for two
    `pnpm dlx` invocations -- accepted for now to keep each public function
    independently callable and testable, matching the rest of this module.
    """
    if not TS_ROOT.exists():
        return None
    result = _run(["pnpm", "dlx", "ts-prune"], cwd=TS_ROOT)
    if result is None or result.returncode != 0:
        return None
    entry_files = _ts_public_entry_files()
    if entry_files is None:
        return None
    return _split_ts_prune_findings(result.stdout, entry_files)


def unused_ts_exports() -> list[str] | None:
    """Exported TypeScript symbols with no importer and no publishing excuse.

    This is the actionable half of ts-prune's output: findings already
    filtered to drop both `(used in module)` hits and anything reachable
    through the package's published `exports` map (see
    `ts_public_export_inventory()` for that half). Deleting published public
    API is out of scope for phase A -- see this repo's dead-code-audit design
    spec -- so conflating the two into one number (as this function used to)
    made the headline count dominated by exactly what the spec excludes: of
    an early raw run's 840 findings, 666 were the `src/node/index.ts` public
    barrel alone.

    None means NOT MEASURED.
    """
    split = _ts_prune_split()
    return None if split is None else split[0]


def ts_public_export_inventory() -> list[str] | None:
    """Exported TypeScript symbols ts-prune flags, that live in a published
    entry file (per package.json's `exports` map).

    Reported only, mirroring `report.public_export_inventory()` on the Python
    side: deleting published public API is out of scope for phase A, so an
    export here is not a deletion candidate, only a fact about the surface.

    None means NOT MEASURED.
    """
    split = _ts_prune_split()
    return None if split is None else split[1]
