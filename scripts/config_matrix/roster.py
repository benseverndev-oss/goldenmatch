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
    "EXT_DOCS_DEFERRED",
    "EXT_README_DEFERRED",
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
# CURRENTLY EMPTY -- every published core package has a docs-site section. The five
# that were provisionally deferred when the inverted check landed (golden-suite,
# goldencheck-types, goldengraph, goldenmatch-kg, goldensuite-mcp) were onboarded
# instead. Keep it that way: a new published package should get a section, and an
# entry here needs a real reason, not a TODO.
DOCS_DEFERRED: dict[str, str] = {}


# Published CORE distributions with no link from the root README.
#
# The old check tested `pkg.lower() in readme.lower()` over the WHOLE file, which
# every package passes on an incidental mention -- "goldenmatch" alone appears
# scores of times, so the check could not fail for the headline package no matter
# what the README said. Requiring a LINK to the package README is the assertion
# the substring test was reaching for.
#
# CURRENTLY EMPTY -- goldencheck-types and goldensuite-mcp, provisionally deferred
# when the check landed, now have rows in the README's "Shared components" table.
README_DEFERRED: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# The EXTENSION tier
#
# `derive_roster()`'s `ext` list -- SQL/rust extras and standalone libraries -- had
# no gating at all: no docs check, and a README check that printed [INFO] while
# listing packages it had found missing. That is the same silence the CORE maps
# above were created to remove, one tier down. These two maps close it.
#
# Unlike the CORE maps, these are NOT provisional. Every entry states a real
# reason, because an extension genuinely can be documented inside a parent page
# instead of owning a section -- what it cannot be is undocumented by accident.
# --------------------------------------------------------------------------- #

#: Extension distributions with no `docs-site/<pkg>/` section of their own.
EXT_DOCS_DEFERRED: dict[str, str] = {
    "goldenmatch-duckdb": (
        "documented in docs-site/extensions/sql.mdx, which covers the DuckDB surface "
        "in situ; a separate section would split one SQL story across two pages"
    ),
    "goldenmatch-pg": (
        "documented in docs-site/extensions/sql.mdx alongside the DuckDB surface; "
        "ships as a GitHub-release tarball rather than a normal wheel"
    ),
    "goldenmatch-embed": (
        "documented in docs-site/extensions/sql.mdx as part of the SQL embedding "
        "surface it exists to serve"
    ),
    "goldenfuzz": (
        "standalone owned library (the rapidfuzz replacement); its package README is "
        "the doc surface and the root README links it from 'Owned libraries'"
    ),
    "goldenphonetic": (
        "standalone owned library (the jellyfish replacement); package README is the "
        "doc surface, linked from the root README's 'Owned libraries' table"
    ),
    "goldenmatch-hnsw": (
        "standalone owned library (the FAISS IndexHNSWFlat replacement); package "
        "README is the doc surface, linked from 'Owned libraries'"
    ),
    "goldenmatch-spark-jar": (
        "a JVM jar published for the Spark tier, not a Python-importable surface; "
        "the Spark story lives in the goldenmatch section"
    ),
    "er-matcher": (
        "publish-er-matcher.yml releases a quantized GGUF MODEL artifact, not a "
        "Python distribution -- there is no package here to document"
    ),
}

#: Extension distributions not named anywhere in the root README.
#:
#: Weaker than the CORE rule on purpose: CORE requires a LINK to
#: `packages/python/<pkg>/README.md`, but the owned libraries live under
#: `packages/rust/extensions/`, so a link-path rule would not generalise. A name
#: mention is the honest floor for this tier.
EXT_README_DEFERRED: dict[str, str] = {
    "goldenmatch-pg": (
        "ships as a GitHub-release tarball, not a pip install; the SQL extensions "
        "page is where a reader would look for it"
    ),
    "goldenmatch-spark-jar": (
        "a JVM build artifact consumed via the Spark tier's own setup, not something "
        "a reader installs from the README"
    ),
    "er-matcher": (
        "a GGUF model release, not an installable package; naming it in the package "
        "overview would imply a dist that does not exist"
    ),
}
