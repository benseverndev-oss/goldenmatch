#!/usr/bin/env python3
"""Build the optional `goldenmatch._native` acceleration extension.

Compiles the PyO3 crate at `packages/rust/extensions/native` (abi3) against the
*current* interpreter and drops the artifact at
`packages/python/goldenmatch/goldenmatch/_native.abi3.so` so `import
goldenmatch._native` resolves.

Run via the project interpreter, e.g.:
    uv run python scripts/build_native.py

Best-effort: prints a clear message and exits non-zero if cargo is missing or the
build fails, but never leaves a half-written artifact. The package works fine
without the extension (pure-Python fallback); this just enables the native path.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CRATE = REPO / "packages" / "rust" / "extensions" / "native"
PKG = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"
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
    # first run, so it must be allowed to fetch — default is online.
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
