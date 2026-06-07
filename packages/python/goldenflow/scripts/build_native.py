#!/usr/bin/env python3
"""Build the optional `goldenflow._native` acceleration extension (in-tree dev).

Compiles the PyO3 crate at `packages/rust/extensions/native-flow` (abi3) against
the *current* interpreter and drops the artifact at
`packages/python/goldenflow/goldenflow/_native.abi3.so` so `import
goldenflow._native` resolves — the first path tried by
`goldenflow.core._native_loader`. Mirrors goldenmatch's scripts/build_native.py.

Run via the project interpreter, e.g.:
    python packages/python/goldenflow/scripts/build_native.py

Best-effort: prints a clear message and exits non-zero if cargo is missing or
the build fails, but never leaves a half-written artifact. GoldenFlow works fine
without the extension (pure-Python paths); this only enables the native phone
path (and only when GOLDENFLOW_NATIVE opts in — see the loader).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

# packages/python/goldenflow/scripts/build_native.py -> repo root is 4 up.
REPO = Path(__file__).resolve().parents[4]
CRATE = REPO / "packages" / "rust" / "extensions" / "native-flow"
PKG = REPO / "packages" / "python" / "goldenflow" / "goldenflow"
DEST = PKG / "_native.abi3.so"


def main() -> int:
    if shutil.which("cargo") is None:
        print("ERROR: cargo not found on PATH; cannot build the native ext.", file=sys.stderr)
        return 1
    if not CRATE.exists():
        print(f"ERROR: native crate not found at {CRATE}", file=sys.stderr)
        return 1

    env = dict(os.environ)
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
