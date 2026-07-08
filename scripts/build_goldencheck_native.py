#!/usr/bin/env python3
"""Build the optional `goldencheck._native` acceleration extension.

Compiles the PyO3 crate at `packages/rust/extensions/goldencheck-native` (abi3)
against the *current* interpreter and drops the artifact at
`packages/python/goldencheck/goldencheck/_native.abi3.so` so `import
goldencheck._native` resolves.

Run via the project interpreter, e.g.:
    uv run python scripts/build_goldencheck_native.py

Best-effort: prints a clear message and exits non-zero if cargo is missing or
the build fails, but never leaves a half-written artifact. The package works
fine without the extension (pure-Python fallback); this just enables the native
path. Mirrors scripts/build_native.py (the goldenmatch sibling).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CRATE = REPO / "packages" / "rust" / "extensions" / "goldencheck-native"
PKG = REPO / "packages" / "python" / "goldencheck" / "goldencheck"
DEST = PKG / "_native.abi3.so"


def main() -> int:
    if shutil.which("cargo") is None:
        print("ERROR: cargo not found on PATH; cannot build the native ext.", file=sys.stderr)
        return 1
    if not CRATE.exists():
        print(f"ERROR: native crate not found at {CRATE}", file=sys.stderr)
        return 1

    env = dict(os.environ)
    # Build against THIS interpreter so the abi3 artifact is importable here.
    env["PYO3_PYTHON"] = sys.executable

    cmd = ["cargo", "build", "--release"]
    # `--offline` only when explicitly requested (e.g. a sandbox with a warm
    # cargo cache but no crates.io network). CI has network + a cold cache on
    # first run, so it must be allowed to fetch -- default is online.
    if "--offline" in sys.argv:
        cmd.append("--offline")
    print(f"building: {' '.join(cmd)}  (PYO3_PYTHON={sys.executable})")
    proc = subprocess.run(cmd, cwd=CRATE, env=env)
    if proc.returncode != 0:
        print("ERROR: cargo build failed.", file=sys.stderr)
        return proc.returncode

    # Platform artifact name: Linux `lib_native.so`, macOS `lib_native.dylib`,
    # Windows `_native.dll`. Normalize to the .so name CPython loads (CI/Linux is
    # the real consumer; the Windows name lets this run locally for verification).
    release = CRATE / "target" / "release"
    candidates = [
        release / "lib_native.so",
        release / "lib_native.dylib",
        release / "_native.dll",
    ]
    built = next((p for p in candidates if p.exists()), None)
    if built is None:
        tried = ", ".join(str(p) for p in candidates)
        print(f"ERROR: build artifact not found (looked for: {tried})", file=sys.stderr)
        return 1

    tmp = DEST.with_suffix(".so.tmp")
    shutil.copy2(built, tmp)
    os.replace(tmp, DEST)  # atomic; never a half-written .so
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    print(f"installed: {DEST}  (from {built.name}, interpreter EXT_SUFFIX={suffix})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
