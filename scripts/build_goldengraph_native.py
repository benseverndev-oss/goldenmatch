#!/usr/bin/env python3
"""Build the ``goldengraph._native`` knowledge-graph engine extension.

Compiles the PyO3 crate at `packages/rust/extensions/goldengraph-native` (abi3)
against the *current* interpreter and drops the artifact at
`packages/python/goldengraph/goldengraph/_native.abi3.so` so `import
goldengraph._native` resolves.

Run via the project interpreter, e.g.:
    uv run python scripts/build_goldengraph_native.py

Unlike the profiling packages, goldengraph is native-authoritative (the store /
resolution engine is Rust-only, no pure-Python fallback), so this build is a hard
prerequisite for the engine -- but the script itself is best-effort: it prints a
clear message and exits non-zero if cargo is missing or the build fails, and never
leaves a half-written artifact. Mirrors scripts/build_analysis_native.py.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CRATE = REPO / "packages" / "rust" / "extensions" / "goldengraph-native"
PKG = REPO / "packages" / "python" / "goldengraph" / "goldengraph"
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
    if "--offline" in sys.argv:
        cmd.append("--offline")
    print(f"building: {' '.join(cmd)}  (PYO3_PYTHON={sys.executable})")
    proc = subprocess.run(cmd, cwd=CRATE, env=env)
    if proc.returncode != 0:
        print("ERROR: cargo build failed.", file=sys.stderr)
        return proc.returncode

    built = CRATE / "target" / "release" / "lib_native.so"
    if not built.exists():
        # macOS produces .dylib; normalize to the .so name CPython loads.
        dylib = CRATE / "target" / "release" / "lib_native.dylib"
        if dylib.exists():
            built = dylib
        else:
            print(f"ERROR: build artifact not found at {built}", file=sys.stderr)
            return 1

    tmp = DEST.with_suffix(".so.tmp")
    shutil.copy2(built, tmp)
    os.replace(tmp, DEST)  # atomic; never a half-written .so
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    print(f"installed: {DEST}  (interpreter EXT_SUFFIX={suffix})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
