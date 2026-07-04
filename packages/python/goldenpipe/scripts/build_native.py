#!/usr/bin/env python3
"""Build the optional `goldenpipe._native` planner-kernel binding (in-tree dev).

Compiles the PyO3 crate at `packages/rust/extensions/goldenpipe-native` (abi3)
against the *current* interpreter and drops the artifact next to the goldenpipe
package so `import goldenpipe._native` resolves — the first path tried by
`goldenpipe.core._native_loader`. Mirrors goldenflow/goldenmatch's build_native.py.

Run via the project interpreter:
    python packages/python/goldenpipe/scripts/build_native.py

Best-effort: prints a clear message and exits non-zero if cargo is missing or the
build fails, never leaving a half-written artifact. GoldenPipe works fine without
it (pure-Python planner); this only wires the native REFERENCE for the parity gate.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

# packages/python/goldenpipe/scripts/build_native.py -> repo root is 4 up.
REPO = Path(__file__).resolve().parents[4]
CRATE = REPO / "packages" / "rust" / "extensions" / "goldenpipe-native"
PKG = REPO / "packages" / "python" / "goldenpipe" / "goldenpipe"


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

    rel = CRATE / "target" / "release"
    # cdylib name is `_native`: _native.dll (win), lib_native.so (linux), lib_native.dylib (mac).
    if (rel / "_native.dll").exists():
        built, dest = rel / "_native.dll", PKG / "_native.pyd"
    elif (rel / "lib_native.so").exists():
        built, dest = rel / "lib_native.so", PKG / "_native.abi3.so"
    elif (rel / "lib_native.dylib").exists():
        built, dest = rel / "lib_native.dylib", PKG / "_native.abi3.so"
    else:
        print(f"ERROR: build artifact not found under {rel}", file=sys.stderr)
        return 1

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    shutil.copy2(built, tmp)
    os.replace(tmp, dest)  # atomic; never a half-written ext
    print(f"installed: {dest}  (interpreter EXT_SUFFIX={sysconfig.get_config_var('EXT_SUFFIX')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
