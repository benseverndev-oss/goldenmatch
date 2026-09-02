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


def _class_module_name(filename: str) -> str:
    """A coverage `<class filename=...>` attribute, normalized to a module name."""
    mod = normalize(filename).replace("/", ".")
    if mod.endswith(".py"):
        mod = mod[:-3]
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    return mod


def _uncovered_modules(coverage_xml: Path) -> set[str]:
    """Modules with zero covered lines in the combined coverage report.

    Coverage emits a different `filename` shape depending on how the report was
    produced -- package-relative, repo-root-relative (with the doubled
    `packages/python/goldenmatch/goldenmatch/` nesting), or absolute. Route
    through coverage_paths.normalize() first so this always compares against
    the same `goldenmatch/...`-rooted shape static.py's module names imply --
    see scripts/coverage_paths.py's docstring for why the naive form silently
    matches nothing against real CI coverage.

    A `<class>` with NO `<line>` children at all (a bare `__init__.py`, or a
    module whose only content is a docstring -- coverage.py doesn't count a
    docstring as an executable line) is excluded here rather than counted as
    uncovered. Such a module was never measured, so "zero lines with hits > 0"
    is trivially true of it for having nothing to hit, not for having gone
    unexecuted -- absence of evidence is not evidence of deadness. Reading it
    the naive way is exactly the bug that made every package `__init__.py` a
    false-positive dead-code candidate: 14 of the first real CI run's 14
    candidates were this. See _no_measurable_lines_modules() for where the
    excluded set is surfaced instead of silently dropped.
    """
    root = ET.parse(coverage_xml).getroot()
    out: set[str] = set()
    for cls in root.iter("class"):
        lines = list(cls.iter("line"))
        if not lines:
            continue
        hits = sum(1 for line in lines if int(line.get("hits", "0")) > 0)
        if hits == 0:
            out.add(_class_module_name(cls.get("filename") or ""))
    return out


def _no_measurable_lines_modules(coverage_xml: Path) -> set[str]:
    """Modules set aside by _uncovered_modules() for having no `<line>` entries.

    Companion to _uncovered_modules()'s guard: named separately so the report
    can disclose how many modules were excluded for lack of measurable lines,
    the same way candidacy_scope() discloses the goldenmatch-only restriction.
    A silently smaller candidate set defeats the point of this phase just as
    much as a silently larger one.
    """
    root = ET.parse(coverage_xml).getroot()
    out: set[str] = set()
    for cls in root.iter("class"):
        if list(cls.iter("line")):
            continue
        out.add(_class_module_name(cls.get("filename") or ""))
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


def candidacy_scope(coverage_xml: Path | None = None) -> dict[str, int]:
    """Counts behind the narrowings this report applies, for CI output.

    Without the goldenmatch-only counts, a report showing zero
    goldencheck/goldenflow/goldenanalysis candidates is indistinguishable
    from "those packages are clean" -- they were never eligible for the
    two-signal test at all.

    When `coverage_xml` is given, also counts modules set aside for having no
    measurable lines (see _no_measurable_lines_modules()) -- without this, a
    report that dropped from 14 candidates to near-zero is indistinguishable
    from "13 modules turned out to be live" and "13 modules were silently
    excluded from the runtime signal", which is a very different claim.
    """
    static = _static_pool()
    eligible = _goldenmatch_eligible(static)
    scope = {
        "static_considered": len(static),
        "goldenmatch_eligible": len(eligible),
        "excluded_no_runtime_signal": len(static) - len(eligible),
    }
    if coverage_xml is not None:
        scope["excluded_no_measurable_lines"] = len(_no_measurable_lines_modules(coverage_xml))
    return scope


def candidates(coverage_xml: Path | None) -> list[dict]:
    static = _static_pool()

    if coverage_xml is None:
        # Runtime evidence unknown. Report nothing: one signal is not proof.
        return []

    eligible = _goldenmatch_eligible(static)
    uncovered = _uncovered_modules(coverage_xml)
    # "static" and "runtime" are invariants of candidacy, not per-item measurements:
    # a module only reaches this list when both signals already agree (it's in the
    # `eligible & uncovered` intersection), so the two keys are always True by
    # construction. They're kept in the schema for downstream consumers, but reading
    # them as evidence gathered for each entry would be wrong.
    return [{"module": m, "static": True, "runtime": True} for m in sorted(eligible & uncovered)]


# Human-readable reasons a signal reads NOT MEASURED, keyed by the same label
# main() prints it under. Kept beside the presentation code that uses them,
# not in other_langs.py -- the "why" here is about this CI run's environment,
# not a property of the tool functions themselves.
_OTHER_LANGS_NOT_MEASURED_REASON = {
    "unused rust deps": "cargo-machete not installed",
    "unwired rust exports": "check_native_symbols could not run for any package",
    "unused ts exports": "ts-prune not installed, or the package's exports map could not be read",
    "ts public-export inventory": (
        "ts-prune not installed, or the package's exports map could not be read"
    ),
}


def other_langs_report() -> dict[str, list[str] | None]:
    """The four other_langs signals, keyed by their report label.

    Each value is exactly what its function returned: None means NOT
    MEASURED, a list (possibly empty) means measured. Both main()'s text and
    --json branches read this so the two presentations can never disagree
    about which signals were actually measured.
    """
    from dead_code.other_langs import (
        ts_public_export_inventory,
        unused_rust_deps,
        unused_ts_exports,
        unwired_rust_exports,
    )

    return {
        "unused rust deps": unused_rust_deps(),
        "unwired rust exports": unwired_rust_exports(),
        "unused ts exports": unused_ts_exports(),
        "ts public-export inventory": ts_public_export_inventory(),
    }


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
    scope = candidacy_scope(args.coverage_xml)
    other_langs = other_langs_report()

    if args.json:
        other_langs_json = {
            label: (
                {"measured": True, "items": items}
                if items is not None
                else {
                    "measured": False,
                    "items": None,
                    "reason": _OTHER_LANGS_NOT_MEASURED_REASON[label],
                }
            )
            for label, items in other_langs.items()
        }
        print(
            json.dumps(
                {
                    "candidates": found,
                    "public_inventory": inventory,
                    "candidacy_scope": scope,
                    "other_langs": other_langs_json,
                },
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
    if args.coverage_xml is not None:
        print(
            f"{scope['excluded_no_measurable_lines']} additional modules set aside for "
            "having no measurable lines in coverage.xml (a bare __init__.py, or a "
            "docstring-only module) -- absence of evidence is not evidence of deadness\n"
        )
    if found:
        print(
            "every listed candidate satisfies both signals by definition -- "
            "static: no importer found; runtime: 0 covered lines\n"
        )
    for c in found:
        print(f"  {c['module']}")
    if args.coverage_xml is None:
        print("  no --coverage-xml given: runtime signal unknown, reporting nothing")
    print(f"\npublic-export inventory (reported only): {len(inventory)}")

    for label, items in other_langs.items():
        if items is None:
            print(f"\n{label}: NOT MEASURED ({_OTHER_LANGS_NOT_MEASURED_REASON[label]})")
            continue
        print(f"\n{label}: {len(items)}")
        for item in items[:40]:
            print(f"  - {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
