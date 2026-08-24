"""The canonical suite-package roster, in ONE place.

Before this module the same six-package list was hardcoded verbatim in four
scripts (`check_llms_counts`, `gen_api_surface`, `gen_suite_matrix`,
`check_docs_sections`) plus `REGISTRY` here and the `config_matrix` matrix in
`ci.yml` -- six copies of a list that changes whenever a package is added. The
duplication was not laziness: `derive_roster()` lived inside
`check_docs_consistency.py` and `REGISTRY` was unreachable from a stdlib-only
gate (importing it dragged in pydantic via the package `__init__`), so no shared
definition was importable. Both of those are fixed, so this module can exist.

Everything here is PURE STDLIB. The doc gates that consume it run on a bare
`setup-python` runner with no synced workspace; adding a third-party import here
would silently push them back to hardcoded copies.

Three distinct notions of "the roster" -- they are not interchangeable:

  DOCUMENTED     the packages that own a `docs-site/<pkg>/` section and a
                 generated config matrix. Derived from REGISTRY, so adding a
                 PackageSpec is the single edit that onboards a package.
  derive_roster  every distribution the repo actually PUBLISHES, read off the
                 `publish-*.yml` callers. Strictly larger than DOCUMENTED.
  DOCS_DEFERRED  published packages knowingly without a docs section, each with
                 a reason. The gap between the first two must be fully covered by
                 this map or `check_docs_consistency` fails.
"""
from __future__ import annotations

from pathlib import Path

from .registry import REGISTRY

# This module exists to BE imported -- it is the shared public surface that
# replaced six hardcoded copies of the package list, and nothing here is consumed
# in-module. Declaring `__all__` states that contract explicitly (and is why a
# static "unused global" reading of this file is wrong: DOCUMENTED alone has eight
# importers).
__all__ = [
    "DOCS_DEFERRED",
    "DOCUMENTED",
    "NON_DIST_STEMS",
    "README_DEFERRED",
    "derive_roster",
    "publish_stems",
]

ROOT = Path(__file__).resolve().parent.parent.parent
PY_PKGS = ROOT / "packages" / "python"

#: Packages with a docs-site section + generated config matrix, in REGISTRY order.
DOCUMENTED: tuple[str, ...] = tuple(REGISTRY)

#: Stems that are NOT a per-distribution PyPI/npm publisher.
NON_DIST_STEMS = frozenset({"containers", "mcp"})


def publish_stems() -> list[str]:
    return sorted(
        p.name[len("publish-"):-len(".yml")]
        for p in (ROOT / ".github" / "workflows").glob("publish-*.yml")
    )


def derive_roster() -> tuple[list[str], list[str], list[str]]:
    """Return (core_pypi, ext_pypi, npm) derived from publish-*.yml callers.

    ``core_pypi``  -- publishers backed by a ``packages/python/<stem>`` directory
                      (the distribution packages that get a README row + nav group).
    ``ext_pypi``   -- the remaining PyPI publishers (SQL / rust-extension extras
                      like goldenmatch-duckdb / -embed / -pg). Reported, not gated
                      structurally -- they live inside parent docs, not their own
                      nav group.
    ``npm``        -- ``publish-<pkg>-js.yml`` stems.
    """
    core: list[str] = []
    ext: list[str] = []
    npm: list[str] = []
    for stem in publish_stems():
        if stem in NON_DIST_STEMS:
            continue
        if stem.endswith("-native"):
            continue  # compiled extras tracked separately; not a doc-nav surface
        if stem.endswith("-js"):
            npm.append(stem[: -len("-js")])
        elif (PY_PKGS / stem).is_dir():
            core.append(stem)
        else:
            ext.append(stem)
    return sorted(set(core)), sorted(set(ext)), sorted(set(npm))


# Published CORE distributions that knowingly have no `docs-site/<pkg>/` section.
#
# This map is the point of the exercise. The nav check used to read "every roster
# package that HAS a docs-site/<pkg>/ directory must appear in the nav" -- which a
# package with no directory passes trivially, so the check could not fail for the
# packages it most needed to catch. Inverting it (every CORE package needs a
# section OR an entry here) turns silence into a decision that has to be written
# down, the same way parity/<pkg>.yaml makes an uncovered scorer declare itself.
#
# EVERY ENTRY BELOW IS PROVISIONAL and needs a product-scope call: onboard, or
# keep the deferral with a real reason. They are recorded as deferred so the
# inverted check can land green rather than blocking on that decision; the
# generator can now scaffold a config-matrix page, so onboarding one is
# "add a PackageSpec, run make docs, write the intro prose".
DOCS_DEFERRED: dict[str, str] = {
    "golden-suite": (
        "TODO(decide): one-line meta-package (whole suite + native). Arguably needs "
        "an install/overview page rather than a full section."
    ),
    "goldencheck-types": (
        "TODO(decide): shared type definitions consumed by goldencheck; may belong "
        "inside the goldencheck section rather than its own."
    ),
    "goldengraph": (
        "TODO(decide): KG engine, published to PyPI and npm, currently documented "
        "only via goldenmatch-kg prose."
    ),
    "goldenmatch-kg": (
        "TODO(decide): knowledge-graph surface over goldenmatch; has an llms.txt but "
        "no docs-site section."
    ),
    "goldensuite-mcp": (
        "TODO(decide): the suite-wide MCP aggregator. Ships an agent-manifest copy "
        "and an llms.txt; a section would duplicate per-package MCP docs."
    ),
}


# Published CORE distributions with no row in the root README's package-overview
# table (no `packages/python/<pkg>/README.md` link anywhere in it).
#
# The old check tested `pkg.lower() in readme.lower()` over the WHOLE file, which
# every package passes on an incidental mention -- "goldenmatch" alone appears
# scores of times, so the check could not fail for the headline package no matter
# what the README said. Requiring a LINK to the package README is the assertion
# the substring test was reaching for.
#
# EVERY ENTRY BELOW IS PROVISIONAL, same as DOCS_DEFERRED: onboard, or keep the
# deferral with a real reason.
README_DEFERRED: dict[str, str] = {
    "goldencheck-types": (
        "TODO(decide): shared type definitions consumed by goldencheck; arguably an "
        "implementation detail of that row rather than its own."
    ),
    "goldensuite-mcp": (
        "TODO(decide): the suite-wide MCP aggregator. The README covers MCP per "
        "package; a dedicated row may or may not earn its place."
    ),
}
