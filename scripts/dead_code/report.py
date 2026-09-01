"""Intersect the dead-code signals and report candidates with their evidence.

A module is a candidate only when the static signal AND the runtime signal
agree, it is not registry-live, and it is not allowlisted. With no coverage
file the runtime signal is None -- unknown -- and no module is a candidate.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from dead_code.allowlist import load_allowlist
from dead_code.liveness import live_modules
from dead_code.static import all_modules, unimported_modules

# report.py sits in scripts/dead_code/, so scripts/ (where coverage_paths.py
# lives) is the parent's parent.
sys.path.insert(0, str(Path(__file__).parent.parent))

from coverage_paths import normalize  # noqa: E402


def _uncovered_modules(coverage_xml: Path) -> set[str]:
    """Modules with zero covered lines in the combined coverage report.

    Coverage emits a different `filename` shape depending on how the report was
    produced -- package-relative, repo-root-relative (with the doubled
    `packages/python/goldenmatch/goldenmatch/` nesting), or absolute. Route
    through coverage_paths.normalize() first so this always compares against
    the same `goldenmatch/...`-rooted shape static.py's module names imply --
    see scripts/coverage_paths.py's docstring for why the naive form silently
    matches nothing against real CI coverage.
    """
    root = ET.parse(coverage_xml).getroot()
    out: set[str] = set()
    for cls in root.iter("class"):
        filename = normalize(cls.get("filename") or "")
        hits = sum(1 for line in cls.iter("line") if int(line.get("hits", "0")) > 0)
        if hits == 0:
            mod = filename.replace("/", ".")
            if mod.endswith(".py"):
                mod = mod[:-3]
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            out.add(mod)
    return out


def _static_pool() -> set[str]:
    """Statically unimported, live-free, un-allowlisted modules, all six packages."""
    return unimported_modules() - live_modules() - load_allowlist()


def _goldenmatch_eligible(static: set[str]) -> set[str]:
    """Restrict a static pool to the modules a runtime signal can ever reach.

    The combined coverage.xml this detector consumes is goldenmatch's alone
    (`source = ["goldenmatch"]` in .github/workflows/ci.yml, and
    coverage_paths.normalize() is itself goldenmatch-only by construction), so
    only a goldenmatch module can ever have a runtime signal. A module from
    any other package is therefore OUT OF SCOPE for the two-signal test --
    excluded for lack of a runtime signal, NOT because it was checked and
    found clean. Filtering here (rather than relying on that exclusion to
    fall out implicitly from normalize()'s internals) makes the restriction a
    deliberate, visible part of candidates() -- see main()'s candidacy_scope
    printout, which is how a CI reader sees this without reading source.
    """
    return {m for m in static if m.startswith("goldenmatch.")}


def candidacy_scope() -> dict[str, int]:
    """Counts behind the goldenmatch-only restriction, for CI output.

    Without this, a report showing zero goldencheck/goldenflow/goldenanalysis
    candidates is indistinguishable from "those packages are clean" -- they
    were never eligible for the two-signal test at all.
    """
    static = _static_pool()
    eligible = _goldenmatch_eligible(static)
    return {
        "static_considered": len(static),
        "goldenmatch_eligible": len(eligible),
        "excluded_no_runtime_signal": len(static) - len(eligible),
    }


def candidates(coverage_xml: Path | None) -> list[dict]:
    static = _static_pool()

    if coverage_xml is None:
        # Runtime evidence unknown. Report nothing: one signal is not proof.
        return []

    eligible = _goldenmatch_eligible(static)
    uncovered = _uncovered_modules(coverage_xml)
    return [{"module": m, "static": True, "runtime": True} for m in sorted(eligible & uncovered)]


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
    scope = candidacy_scope()

    if args.json:
        print(
            json.dumps(
                {"candidates": found, "public_inventory": inventory, "candidacy_scope": scope},
                indent=2,
            )
        )
        return 0

    print(f"{len(all_modules())} modules known, {len(found)} candidates\n")
    print(
        f"candidacy scope: {scope['static_considered']} static candidates considered, "
        f"{scope['goldenmatch_eligible']} goldenmatch-scoped and eligible for the runtime "
        f"signal, {scope['excluded_no_runtime_signal']} excluded for lack of any runtime "
        "signal (the combined coverage.xml is goldenmatch's alone -- excluded means OUT "
        "OF SCOPE, not clean)\n"
    )
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
