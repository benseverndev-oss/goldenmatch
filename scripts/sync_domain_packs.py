"""Sync domain pack YAMLs from the TS package into the Python vendored copy.

The TS package at ``packages/typescript/goldencheck-types/domains/`` is the
canonical source for the YAMLs (it ships them as runtime assets in the npm
package). The Python package vendors copies under
``packages/python/goldencheck-types/goldencheck_types/_domains/`` so that
sdists / wheels / external installs all carry the data without depending on
the monorepo layout at runtime.

Run this script whenever the TS YAMLs change. Pass ``--check`` in CI to
fail if the two copies have drifted instead of fixing.

    python scripts/sync_domain_packs.py          # mirror TS -> Python
    python scripts/sync_domain_packs.py --check  # fail if out of sync
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TS_SRC = ROOT / "packages" / "typescript" / "goldencheck-types" / "domains"
PY_DST = ROOT / "packages" / "python" / "goldencheck-types" / "goldencheck_types" / "_domains"


def _yaml_files(d: Path) -> set[str]:
    return {p.name for p in d.glob("*.yaml")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if Python copy is out of sync (CI mode)",
    )
    args = parser.parse_args()

    if not TS_SRC.exists():
        sys.stderr.write(f"TS source not found: {TS_SRC}\n")
        return 2

    PY_DST.mkdir(parents=True, exist_ok=True)
    ts_files = _yaml_files(TS_SRC)
    py_files = _yaml_files(PY_DST)

    drift: list[str] = []
    for name in sorted(ts_files):
        src = TS_SRC / name
        dst = PY_DST / name
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            drift.append(name)
            if not args.check:
                shutil.copy2(src, dst)
    extras = sorted(py_files - ts_files)
    for name in extras:
        drift.append(f"(stale) {name}")
        if not args.check:
            (PY_DST / name).unlink()

    if args.check:
        if drift:
            sys.stderr.write(
                "Domain pack drift detected:\n"
                + "\n".join(f"  - {d}" for d in drift)
                + "\nRun: python scripts/sync_domain_packs.py\n"
            )
            return 1
        print(f"OK -- {len(ts_files)} domain pack(s) in sync")
        return 0

    if drift:
        print(f"Synced {len(drift)} file(s):")
        for name in drift:
            print(f"  - {name}")
    else:
        print(f"OK -- already in sync ({len(ts_files)} file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
