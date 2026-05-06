"""Build the web UI and stage it inside the package for wheel inclusion.

Usage:
    python scripts/build_web.py

Run from anywhere — paths are resolved relative to this file. Invokes
`pnpm install --frozen-lockfile` and `pnpm build` in `web/frontend/`,
then mirrors the resulting `dist/` into `goldenmatch/web/static/` (which
hatch's force-include block picks up at `hatch build` time).

CI / release sequence:

    python scripts/build_web.py && hatch build

Promoting this to a hatch custom build hook is a deliberate v2 follow-up;
the script + docs route is the simpler MVP and easier to debug when the
frontend toolchain hiccups.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
FRONTEND = PKG / "web" / "frontend"
STATIC = PKG / "goldenmatch" / "web" / "static"


def main() -> int:
    if not FRONTEND.exists():
        print(f"frontend missing at {FRONTEND}", file=sys.stderr)
        return 1

    # Windows ships pnpm as pnpm.cmd — subprocess.run on a bare "pnpm" arg
    # without shell=True can't find it. Resolve via PATH first.
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print("pnpm not found on PATH (install pnpm@9 or run under corepack)", file=sys.stderr)
        return 3

    subprocess.run([pnpm, "install", "--frozen-lockfile"], cwd=FRONTEND, check=True)
    subprocess.run([pnpm, "build"], cwd=FRONTEND, check=True)

    # Wipe prior contents (except .gitkeep) before mirroring fresh dist.
    if STATIC.exists():
        for child in STATIC.iterdir():
            if child.name == ".gitkeep":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    STATIC.mkdir(parents=True, exist_ok=True)

    dist = FRONTEND / "dist"
    if not dist.exists():
        print(f"frontend dist missing at {dist}", file=sys.stderr)
        return 2
    for item in dist.iterdir():
        target = STATIC / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    print(f"staged {sum(1 for _ in STATIC.rglob('*') if _.is_file())} files at {STATIC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
