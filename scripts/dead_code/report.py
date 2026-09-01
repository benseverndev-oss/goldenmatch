"""Intersect the dead-code signals and report candidates with their evidence.

A module is a candidate only when the static signal AND the runtime signal
agree, it is not registry-live, and it is not allowlisted. With no coverage
file the runtime signal is None -- unknown -- and no module is a candidate.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from dead_code.allowlist import load_allowlist
from dead_code.liveness import live_modules
from dead_code.static import all_modules, unimported_modules


def _uncovered_modules(coverage_xml: Path) -> set[str]:
    """Modules with zero covered lines in the combined coverage report."""
    root = ET.parse(coverage_xml).getroot()
    out: set[str] = set()
    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        hits = sum(1 for line in cls.iter("line") if int(line.get("hits", "0")) > 0)
        if hits == 0:
            mod = filename.replace("/", ".").replace("\\", ".")
            if mod.endswith(".py"):
                mod = mod[:-3]
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            out.add(mod)
    return out


def candidates(coverage_xml: Path | None) -> list[dict]:
    live = live_modules()
    allowed = load_allowlist()
    static = unimported_modules() - live - allowed

    if coverage_xml is None:
        # Runtime evidence unknown. Report nothing: one signal is not proof.
        return []

    uncovered = _uncovered_modules(coverage_xml)
    return [{"module": m, "static": True, "runtime": True} for m in sorted(static & uncovered)]


def public_export_inventory() -> list[str]:
    """Modules that are unimported internally but MAY be public API.

    Reported only. Deleting published public API is out of scope for phase A:
    api_parity spans six packages, so a public symbol is a cross-surface
    contract rather than one deletion.
    """
    live = live_modules()
    return sorted(
        m
        for m in unimported_modules() - live
        if m.count(".") == 1  # top-level package submodule: most likely public
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage-xml", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    found = candidates(args.coverage_xml)
    inventory = public_export_inventory()

    if args.json:
        print(json.dumps({"candidates": found, "public_inventory": inventory}, indent=2))
        return 0

    print(f"{len(all_modules())} modules known, {len(found)} candidates\n")
    for c in found:
        print(f"  {c['module']}  (static: no importer, runtime: 0 covered lines)")
    if args.coverage_xml is None:
        print("  no --coverage-xml given: runtime signal unknown, reporting nothing")
    print(f"\npublic-export inventory (reported only): {len(inventory)}")

    from dead_code.other_langs import (
        unused_rust_deps,
        unused_ts_exports,
        unwired_rust_exports,
    )

    for label, items in (
        ("unused rust deps", unused_rust_deps()),
        ("unwired rust exports", unwired_rust_exports()),
        ("unused ts exports", unused_ts_exports()),
    ):
        print(f"\n{label}: {len(items)}")
        for item in items[:40]:
            print(f"  - {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
