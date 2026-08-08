#!/usr/bin/env python3
"""Tier-1 documentation-lockstep gate: the single required doc-consistency check.

This is the umbrella entry point for "are all the structural doc surfaces in
lockstep with the published-package roster?". It is deterministic, stdlib-only,
and exits non-zero on any FAIL so it can gate CI without flaking on clean PRs.

It does SIX things (see ``.claude/doc-surfaces.md`` for the surface inventory):

1. Runs the two existing doc gates as subprocesses so there is one command for
   "all doc gates": ``check_version_consistency.py`` and
   ``sync_readme_callouts.py --check``.

2. roster-matrix: derive the canonical published-package roster from the
   ``publish-<pkg>.yml`` (PyPI) and ``publish-<pkg>-js.yml`` (npm) workflow
   callers -- the same authoritative set ``scripts/suite_download_badges.py``
   tracks -- and cross-check the two agree. Then assert each roster package's
   NAME is present in the structural surfaces it is expected to have:
     - every PyPI roster package name appears in the root ``README.md``;
     - every roster package that has a ``docs-site/<pkg>/`` page directory also
       appears in the ``docs-site/docs.json`` navigation.
   (server.json / llms.txt presence is reported, not asserted -- those surfaces
   only exist for a subset of packages, and version_consistency already gates
   server.json content.)

3. docs-nav integrity: ``docs.json`` parses; every page referenced in the nav
   resolves to an existing ``.mdx`` under ``docs-site/``; every ``.mdx`` under a
   ``docs-site/<group>/`` directory is referenced somewhere in the nav (orphan
   detection). Fully deterministic, high value.

4. changelog<->version: for each ``packages/python/<pkg>/CHANGELOG.md`` parse the
   most-recent RELEASED version heading (Keep-a-Changelog ``## [X.Y.Z]`` or the
   bare ``## X.Y.Z (date)`` variant; ``unreleased`` headings are skipped) and
   assert it equals the package's ``pyproject.toml`` version. Packages whose
   CHANGELOG has no versioned heading are REPORTED, not failed.

5. install-claims-resolve: scan the flagship/aggregate surfaces (root
   ``README.md``, the goldenmatch package README + ``llms.txt``) for first-party
   ``pip install <pkg>`` / ``npm i <pkg>`` commands and assert each names a
   PUBLISHED distribution (``PYPI_PACKAGES`` / ``NPM_PACKAGES`` from
   ``suite_download_badges.py``, plus an explicit unpublished allowlist). This is
   the guard for the ``pip install goldenmatch-kg`` 404 class of bug -- a homepage
   install line for a not-yet-published package.

6. aggregate-badge roster: every ``publish-<pkg>.yml`` PyPI publisher (minus
   documented exceptions) is in ``PYPI_PACKAGES`` and vice versa; same for
   ``publish-<pkg>-js.yml`` <-> ``NPM_PACKAGES``; and the README pepy.tech
   ``?q=`` download-badge link lists exactly ``PYPI_PACKAGES`` so the clicked-
   through total matches the linked packages.

``--check`` (the default) only reports + exits 1 on drift. ``--fix`` performs the
narrow MECHANICAL reconciliations that are safe to automate (adding a missing
roster package as a docs-nav ``SQL extensions`` entry is NOT auto-fixable -- that
needs human prose -- so --fix is limited to nothing today; reconciliations on the
current tree were done by hand in the lockstep branch). It is kept as a stub so
the flag exists and future mechanical fixes have a home.

Run: ``python scripts/check_docs_consistency.py``
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DOCS_SITE = ROOT / "docs-site"
PY_PKGS = ROOT / "packages" / "python"

_VERSIONED_HEADING_RE = re.compile(
    r"^##\s+\[?(?P<ver>\d+\.\d+\.\d+(?:[.\-][0-9A-Za-z.]+)?)\]?", re.MULTILINE
)
# A heading whose label/date marks it as not-yet-released. We skip these and look
# for the next released heading below it.
_UNRELEASED_RE = re.compile(r"unreleased", re.IGNORECASE)


class Result:
    def __init__(self) -> None:
        self.checks: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


# --------------------------------------------------------------------------- #
# 1. Run the existing gates as subprocesses
# --------------------------------------------------------------------------- #
def run_subgate(res: Result, name: str, argv: list[str]) -> None:
    proc = subprocess.run(
        [sys.executable, *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    ok = proc.returncode == 0
    detail = ""
    if not ok:
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        detail = tail[-1] if tail else f"exit {proc.returncode}"
    res.record(name, ok, detail)


# --------------------------------------------------------------------------- #
# Roster derivation
# --------------------------------------------------------------------------- #
def _publish_stems() -> list[str]:
    return sorted(
        p.name[len("publish-"):-len(".yml")]
        for p in (ROOT / ".github" / "workflows").glob("publish-*.yml")
    )


# Stems that are NOT a per-distribution PyPI/npm publisher.
_NON_DIST_STEMS = {"containers", "mcp"}


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
    for stem in _publish_stems():
        if stem in _NON_DIST_STEMS:
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


def _badge_tokens() -> set[str] | None:
    """All quoted suite-package-shaped tokens in suite_download_badges.py.

    We scan the whole file rather than the bracket span: the PYPI_PACKAGES list
    carries inline comments containing ``goldenmatch[native]`` etc., so a naive
    ``\\[ .*? \\]`` capture stops early. Every real entry is a quoted token, so a
    file-wide quoted-token scan is robust and still catches "added a core
    package but forgot to add it to the badge totals".
    """
    badge = SCRIPTS / "suite_download_badges.py"
    if not badge.exists():
        return None
    text = badge.read_text(encoding="utf-8")
    return {
        t for t in re.findall(r'["\']([A-Za-z][\w-]+)["\']', text)
        if t.startswith(("golden", "infermap"))
    }


# --------------------------------------------------------------------------- #
# docs.json helpers
# --------------------------------------------------------------------------- #
def _iter_nav_pages(node: object, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _iter_nav_pages(v, out)
    elif isinstance(node, list):
        for v in node:
            _iter_nav_pages(v, out)


def _collect_page_refs(node: object, out: list[str]) -> None:
    """Collect only strings that are PAGE references (inside a ``pages`` array).

    Mintlify nav: a group/tab is a dict with a ``pages`` list whose entries are
    either a page-slug string or a nested group dict. Labels live under ``tab`` /
    ``group`` keys and are deliberately NOT collected here.
    """
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "pages" and isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        out.append(item)
                    else:
                        _collect_page_refs(item, out)
            else:
                _collect_page_refs(val, out)
    elif isinstance(node, list):
        for v in node:
            _collect_page_refs(v, out)


def _iter_nav_groups(node: object, out: list[str]) -> None:
    if isinstance(node, dict):
        if "group" in node and isinstance(node["group"], str):
            out.append(node["group"])
        for v in node.values():
            _iter_nav_groups(v, out)
    elif isinstance(node, list):
        for v in node:
            _iter_nav_groups(v, out)


def load_docs_json() -> dict | None:
    path = DOCS_SITE / "docs.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 2. roster matrix
# --------------------------------------------------------------------------- #
def check_roster_matrix(res: Result) -> None:
    core, ext, npm = derive_roster()
    badge_tokens = _badge_tokens()

    # Cross-check: every CORE roster package must be tracked by the badge script
    # (the documented suite-totals single-source-of-truth). A core package the
    # workflows publish but the badge script forgot is real drift.
    if badge_tokens is None:
        res.record("roster cross-check vs badge script", True, "(badge script absent -- skipped)")
    else:
        missing = sorted(set(core) - badge_tokens)
        res.record(
            "roster cross-check vs badge script",
            not missing,
            f"core packages published but not in suite_download_badges totals: {missing}"
            if missing
            else "",
        )

    # README presence: every CORE roster package name must appear in README.md.
    # (Extension extras goldenmatch-{duckdb,embed,pg} are reported below, not gated
    #  -- pg ships as a GitHub-release tarball and isn't an overview-table row.)
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    missing_readme = [p for p in core if p.lower() not in readme]
    res.record(
        "roster -> README.md presence",
        not missing_readme,
        f"missing from README package overview: {missing_readme}" if missing_readme else "",
    )
    ext_missing = [p for p in ext if p.lower() not in readme]
    if ext_missing:
        res.record(
            "extension extras README presence (report-only)",
            True,
            f"reported, not gated -- extension distributions not named in README: {ext_missing}",
        )

    # docs-nav presence: roster packages that have a docs-site/<pkg>/ directory
    # must appear in the docs.json navigation (group name or page path).
    docs = load_docs_json()
    if docs is None:
        res.record("roster -> docs.json nav presence", False, "docs-site/docs.json missing")
        return
    groups: list[str] = []
    pages: list[str] = []
    _iter_nav_groups(docs.get("navigation"), groups)
    _iter_nav_pages(docs.get("navigation"), pages)
    nav_blob = " ".join(g.lower() for g in groups) + " " + " ".join(p.lower() for p in pages)
    have_docs_dir = [p for p in core if (DOCS_SITE / p).is_dir()]
    missing_nav = [p for p in have_docs_dir if p.lower() not in nav_blob]
    res.record(
        "roster -> docs.json nav presence",
        not missing_nav,
        f"package has docs-site/<pkg>/ dir but no nav reference: {missing_nav}"
        if missing_nav
        else "",
    )


# --------------------------------------------------------------------------- #
# 3. docs-nav integrity
# --------------------------------------------------------------------------- #
def check_docs_nav_integrity(res: Result) -> None:
    docs = load_docs_json()
    if docs is None:
        res.record("docs.json parses", False, "docs-site/docs.json missing")
        return
    res.record("docs.json parses", True, "")

    # The nav-page walker collects every string in the navigation tree: real
    # page refs AND group/tab label strings ("Documentation", "GoldenMatch").
    # Page refs live in a `"pages": [...]` array; labels live under `"tab"` /
    # `"group"` keys. Walk the structure key-aware so we test ONLY page refs for
    # file existence (a page ref with no matching .mdx is a broken link).
    page_refs: list[str] = []
    _collect_page_refs(docs.get("navigation"), page_refs)

    referenced: set[str] = set()
    broken: list[str] = []
    for ref in page_refs:
        if (DOCS_SITE / f"{ref}.mdx").exists():
            referenced.add(ref)
        else:
            broken.append(ref)
    res.record(
        "docs.json nav -> every page resolves to an .mdx",
        not broken,
        f"nav page refs with no matching .mdx: {broken}" if broken else "",
    )

    # Orphan detection: every .mdx under docs-site/<group>/ must be referenced.
    orphans: list[str] = []
    for mdx in sorted(DOCS_SITE.rglob("*.mdx")):
        rel = mdx.relative_to(DOCS_SITE)
        parts = rel.parts
        # Skip snippet/partial conventions if they ever appear.
        if any(p in {"snippets", "_partials"} for p in parts) or rel.name.startswith("_"):
            continue
        slug = rel.with_suffix("").as_posix()  # nav refs use forward slashes; str() breaks on Windows
        if slug not in referenced:
            orphans.append(slug)
    res.record(
        "docs-site .mdx orphan detection",
        not orphans,
        f"unreferenced .mdx pages (add to docs.json nav or delete): {orphans}"
        if orphans
        else "",
    )


# --------------------------------------------------------------------------- #
# 4. changelog <-> version
# --------------------------------------------------------------------------- #
def _latest_released_changelog_version(text: str) -> str | None:
    """Return the most-recent RELEASED version from a Keep-a-Changelog file.

    Skips headings marked 'unreleased'. Returns None if no versioned heading.
    """
    for m in _VERSIONED_HEADING_RE.finditer(text):
        # Grab the rest of the heading line to test for an 'unreleased' marker.
        line_end = text.find("\n", m.start())
        line = text[m.start(): line_end if line_end != -1 else len(text)]
        if _UNRELEASED_RE.search(line):
            continue
        return m.group("ver")
    return None


def check_changelog_versions(res: Result) -> None:
    mismatches: list[str] = []
    reported_no_version: list[str] = []
    for pyproject in sorted(PY_PKGS.glob("*/pyproject.toml")):
        pkg = pyproject.parent.name
        changelog = pyproject.parent / "CHANGELOG.md"
        if not changelog.exists():
            continue
        proj_ver = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("version")
        if proj_ver is None:
            continue
        cl_ver = _latest_released_changelog_version(changelog.read_text(encoding="utf-8"))
        if cl_ver is None:
            reported_no_version.append(pkg)
            continue
        if cl_ver != proj_ver:
            mismatches.append(f"{pkg}: CHANGELOG top released={cl_ver} != pyproject={proj_ver}")
    detail = ""
    if mismatches:
        detail = "; ".join(mismatches)
    if reported_no_version:
        note = f"(reported, not failed -- no versioned heading: {reported_no_version})"
        detail = f"{detail} {note}".strip()
    res.record("CHANGELOG top version == pyproject version", not mismatches, detail)


# --------------------------------------------------------------------------- #
# 5/6 shared: the authoritative published-package rosters
# --------------------------------------------------------------------------- #
def _badge_package_lists() -> tuple[list[str], list[str]] | None:
    """Import (PYPI_PACKAGES, NPM_PACKAGES) from the badge script.

    Those lists are the documented single source of truth for the suite download
    totals; the install-claim and aggregate-link gates check the docs against
    them. The badge script is stdlib-only with all execution under ``__main__``,
    so importing it has no side effects. Returns None if it can't be imported
    (treated as a skip, never a hard failure).
    """
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    try:
        from suite_download_badges import NPM_PACKAGES, PYPI_PACKAGES
    except Exception:
        return None
    return list(PYPI_PACKAGES), list(NPM_PACKAGES)


# --------------------------------------------------------------------------- #
# 5. install-claims-resolve
# --------------------------------------------------------------------------- #
# Flagship/aggregate surfaces whose FIRST-PARTY install commands must name a
# PUBLISHED distribution. Scoped deliberately: a not-yet-released package's OWN
# README may carry an aspirational `pip install <self>` line, so per-package
# READMEs are out of scope -- this gates the homepage + the AI-facing llms.txt,
# where a broken cross-package install claim actively misleads.
_INSTALL_CLAIM_SURFACES = (
    "README.md",
    "packages/python/goldenmatch/README.md",
    "packages/python/goldenmatch/goldenmatch/llms.txt",
)
# First-party distributions documented but intentionally NOT yet published. Add a
# name here (with review) to keep an install line for a pre-release package;
# empty by default so a stray claim fails loudly.
_UNPUBLISHED_INSTALL_ALLOWLIST: set[str] = set()

_PIP_INSTALL_RE = re.compile(r"pip install\s+(?P<args>[^\n`]+)")
_NPM_INSTALL_RE = re.compile(r"(?:npm (?:install|i)|pnpm (?:add|install))\s+(?P<args>[^\n`]+)")
_SHELL_SPLIT_RE = re.compile(r"&&|\|\||[;|]")


def _is_first_party(pkg: str) -> bool:
    return pkg.startswith("golden") or pkg.startswith("infermap")


def _install_pkgs(args: str) -> list[str]:
    """Bare distribution names from the FIRST shell segment of an install command.

    Stops at the first shell operator (so ``pip install goldenmatch && goldenmatch
    dedupe ...`` yields only ``goldenmatch``), skips flags / paths / VCS / URLs,
    and strips quotes, ``[extras]``, and version specifiers.
    """
    segment = _SHELL_SPLIT_RE.split(args, maxsplit=1)[0]
    segment = segment.split("#", 1)[0]  # drop a trailing inline `# comment`
    pkgs: list[str] = []
    for raw in segment.split():
        tok = raw.strip("'\"")
        if not tok or tok.startswith("-"):
            continue
        if "/" in tok or tok.startswith((".", "git+", "http")):
            continue  # editable path / VCS / URL install
        tok = re.split(r"[\[<>=!~ ]", tok, maxsplit=1)[0]  # drop [extras] + version pin
        if tok:
            pkgs.append(tok.lower())
    return pkgs


def check_install_claims(res: Result) -> None:
    lists = _badge_package_lists()
    if lists is None:
        res.record("install claims resolve to a published dist", True,
                   "(badge script not importable -- skipped)")
        return
    pypi = set(lists[0]) | _UNPUBLISHED_INSTALL_ALLOWLIST
    npm = set(lists[1]) | _UNPUBLISHED_INSTALL_ALLOWLIST
    bad: list[str] = []
    for rel in _INSTALL_CLAIM_SURFACES:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for m in _PIP_INSTALL_RE.finditer(text):
            for pkg in _install_pkgs(m.group("args")):
                if _is_first_party(pkg) and pkg not in pypi:
                    bad.append(f"{rel}: `pip install {pkg}` (not a published PyPI dist)")
        for m in _NPM_INSTALL_RE.finditer(text):
            for pkg in _install_pkgs(m.group("args")):
                if _is_first_party(pkg) and pkg not in npm:
                    bad.append(f"{rel}: `npm install {pkg}` (not a published npm dist)")
    res.record(
        "install claims resolve to a published dist",
        not bad,
        "; ".join(sorted(set(bad))) if bad else "",
    )


# --------------------------------------------------------------------------- #
# 6. aggregate-badge roster
# --------------------------------------------------------------------------- #
_PEPY_RE = re.compile(r"pepy\.tech/projects\?q=(?P<q>[A-Za-z0-9+._-]+)")
# Published distributions deliberately NOT counted in the suite download totals.
# goldenmatch-pg ships as GitHub-release tarballs (no pypistats download API),
# so it has a publish workflow but is excluded from PYPI_PACKAGES on purpose.
# er-matcher's publish-er-matcher.yml uploads a quantized GGUF *model* to a
# GitHub Release (not a PyPI/npm distribution -- no pypistats download API), so
# like goldenmatch-pg it has a publish workflow but is excluded from the download
# badge totals on purpose. (goldenmatch-hnsw published its first release 2026-07-29
# and graduated into PYPI_PACKAGES + the README pepy list.)
_PYPI_PUBLISH_BADGE_EXCEPTIONS = {"goldenmatch-pg", "infermap-native", "er-matcher"}


def check_aggregate_badges(res: Result) -> None:
    lists = _badge_package_lists()
    if lists is None:
        res.record("aggregate badge roster", True, "(badge script not importable -- skipped)")
        return
    pypi_set, npm_set = set(lists[0]), set(lists[1])

    # (a) Bidirectional publisher <-> badge-roster coverage. A new publish-*.yml
    #     added without updating the badge totals (or vice versa) is real drift.
    pypi_pub = {s for s in _publish_stems()
                if s not in _NON_DIST_STEMS and not s.endswith("-js")}
    npm_pub = {s[: -len("-js")] for s in _publish_stems() if s.endswith("-js")}
    pypi_drift = (pypi_pub - _PYPI_PUBLISH_BADGE_EXCEPTIONS) ^ pypi_set
    res.record(
        "PyPI publishers <-> badge PYPI_PACKAGES",
        not pypi_drift,
        f"symmetric-diff (add to PYPI_PACKAGES, add a publish-*.yml, or to "
        f"EXCEPTIONS): {sorted(pypi_drift)}" if pypi_drift else "",
    )
    npm_drift = npm_pub ^ npm_set
    res.record(
        "npm publishers <-> badge NPM_PACKAGES",
        not npm_drift,
        f"symmetric-diff: {sorted(npm_drift)}" if npm_drift else "",
    )

    # (b) The README aggregate-download badge LINK (pepy.tech ?q=) must list
    #     exactly PYPI_PACKAGES so the clicked-through page matches the total.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = _PEPY_RE.search(readme)
    if m is None:
        res.record("README pepy.tech ?q= link == PYPI_PACKAGES", False,
                   "no pepy.tech/projects?q= link found in README.md")
        return
    link_set = {p for p in m.group("q").split("+") if p}
    link_drift = link_set ^ pypi_set
    res.record(
        "README pepy.tech ?q= link == PYPI_PACKAGES",
        not link_drift,
        f"link/roster symmetric-diff (update the README ?q= list): {sorted(link_drift)}"
        if link_drift else "",
    )


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 8. Agent discoverability: every distributable surface ships an llms.txt
# --------------------------------------------------------------------------- #
# The problem this gates: an agent that meets a Golden Suite package as an
# INSTALLED ARTIFACT -- site-packages/goldenmatch/, node_modules/goldenmatch/, a
# SQL connection, a checked-out action -- has no repo to consult, so it infers
# behaviour by reading the implementation. Shipping llms.txt INSIDE the artifact
# is what turns that into a lookup. Docs that only exist in the repo do not help.
#
# The rule is per-ecosystem because "inside the artifact" means a different path
# in each one, and each was verified against a real built artifact:
#   * Python wheel   -- inside the importable dir (hatchling ships the package dir)
#   * maturin wheel  -- inside python/<module>/ (maturin copies the whole tree)
#   * npm            -- package root, and listed in `files`
#   * action / dbt   -- package root (both are consumed as a checked-out tree)
#   * pgrx extension -- crate root, compiled in via include_str!
#
# Deferrals are DECLARED, not silent, mirroring the parity gate's
# `scorer_kernels_deferred` convention: reason prefixed `declined --` (will not)
# or `deferred --` (not yet). A new distributable with neither an llms.txt nor a
# deferral entry FAILS -- that is the whole point, so the next package added does
# not quietly reintroduce the gap.
_LLMS_DEFERRED: dict[str, str] = {
    "packages/rust/extensions/datafusion-udf": (
        "declined -- pure-Rust maturin layout with no Python package dir to ship a "
        "file in, and no publish workflow: it is a CI feasibility gate, not a "
        "distributed artifact"
    ),
    "packages/rust/extensions/graph-layout": (
        "declined -- standalone demo binary (publish = false), not a distribution"
    ),
}


def _maturin_module_dir(pkg_dir: Path, cfg: dict) -> Path | None:
    """Where a maturin wheel's importable dir lives in the source tree."""
    maturin = cfg.get("tool", {}).get("maturin", {})
    src = maturin.get("python-source")
    if not src:
        return None  # pure-Rust layout: nowhere to put a file
    module = maturin.get("module-name", "").split(".")[0]
    if not module:
        return None
    return pkg_dir / src / module


def _python_dist_targets() -> list[tuple[str, list[Path]]]:
    """(label, acceptable llms.txt paths) for every pyproject-backed distribution.

    Usually one path -- inside the importable dir, which is what a wheel ships.
    The dbt package is the exception: it is consumed BOTH as a dbt package (git
    clone, so the project root is what lands) and via pip, so its source of truth
    sits at the root and is force-included into the wheel. Either location counts.
    """
    targets: list[tuple[str, list[Path]]] = []
    for pyproject in sorted(
        list(PY_PKGS.glob("*/pyproject.toml"))
        + list((ROOT / "packages" / "rust" / "extensions").glob("*/pyproject.toml"))
        + list((ROOT / "packages" / "dbt").glob("*/pyproject.toml"))
    ):
        pkg_dir = pyproject.parent
        label = pkg_dir.relative_to(ROOT).as_posix()
        try:
            cfg = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except Exception:
            continue
        mod = _maturin_module_dir(pkg_dir, cfg)
        if mod is None:
            # Not maturin (or pure-Rust layout): find the importable dir by name.
            name = cfg.get("project", {}).get("name", pkg_dir.name)
            guess = pkg_dir / name.replace("-", "_")
            mod = guess if guess.is_dir() else None
        paths = [pkg_dir / "llms.txt"]
        if mod is not None:
            paths.insert(0, mod / "llms.txt")
        targets.append((label, paths))
    return targets


def check_agent_pointers(res: Result) -> None:
    expected: list[tuple[str, list[Path]]] = list(_python_dist_targets())
    expected += [
        (p.parent.relative_to(ROOT).as_posix(), [p.parent / "llms.txt"])
        for p in sorted((ROOT / "packages" / "typescript").glob("*/package.json"))
    ]
    expected += [
        (p.parent.relative_to(ROOT).as_posix(), [p.parent / "llms.txt"])
        for p in sorted((ROOT / "packages" / "actions").glob("*/action.yml"))
    ]
    pg = ROOT / "packages" / "rust" / "extensions" / "postgres"
    if (pg / "goldenmatch_pg.control").is_file():
        expected.append((pg.relative_to(ROOT).as_posix(), [pg / "llms.txt"]))

    missing = [
        label
        for label, paths in expected
        if label not in _LLMS_DEFERRED and not any(p.is_file() for p in paths)
    ]
    res.record(
        "distributable surfaces ship llms.txt",
        not missing,
        f"no llms.txt inside the installed artifact (add one, or declare a reason "
        f"in _LLMS_DEFERRED): {missing}" if missing else "",
    )

    # A deferral for something that no longer exists is stale bookkeeping.
    stale = [k for k in _LLMS_DEFERRED if not (ROOT / k).is_dir()]
    res.record(
        "llms.txt deferrals are live",
        not stale,
        f"_LLMS_DEFERRED names paths that do not exist: {stale}" if stale else "",
    )

    # npm ships only what `files` lists, so presence on disk is not enough.
    npm_unshipped = []
    for pkg_json in sorted((ROOT / "packages" / "typescript").glob("*/package.json")):
        if not (pkg_json.parent / "llms.txt").is_file():
            continue  # already reported above
        try:
            files = json.loads(pkg_json.read_text(encoding="utf-8")).get("files")
        except Exception:
            continue
        if files is not None and "llms.txt" not in files:
            npm_unshipped.append(pkg_json.parent.name)
    res.record(
        "npm llms.txt listed in package.json files",
        not npm_unshipped,
        f"llms.txt exists but is not in `files`, so it is not published: {npm_unshipped}"
        if npm_unshipped
        else "",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", default=True,
                        help="Report drift and exit 1 if any (default).")
    parser.add_argument("--fix", action="store_true",
                        help="Apply the narrow mechanical reconciliations (none auto-fixable today).")
    args = parser.parse_args(argv)

    res = Result()

    run_subgate(res, "version_consistency (subprocess)",
                [str(SCRIPTS / "check_version_consistency.py")])
    run_subgate(res, "readme_callouts --check (subprocess)",
                [str(SCRIPTS / "sync_readme_callouts.py"), "--check"])
    run_subgate(res, "native_docs --check (subprocess)",
                [str(SCRIPTS / "gen_native_docs.py"), "--check"])
    check_roster_matrix(res)
    check_docs_nav_integrity(res)
    check_changelog_versions(res)
    check_install_claims(res)
    check_aggregate_badges(res)
    check_agent_pointers(res)

    if args.fix:
        print("--fix: no auto-fixable mechanical reconciliations pending; "
              "all current-tree drift was reconciled by hand. Running checks.\n")

    print("Documentation consistency checks:")
    width = max(len(n) for n, _, _ in res.checks)
    for name, ok, detail in res.checks:
        status = "PASS" if ok else "FAIL"
        line = f"  [{status}] {name.ljust(width)}"
        if detail:
            line += f"  -- {detail}"
        print(line)

    if not res.ok:
        print("\nDocs consistency FAILED. Reconcile the surfaces above (see "
              ".claude/doc-surfaces.md -> 'Automated gates').")
        return 1
    print("\nAll documentation consistency checks PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
