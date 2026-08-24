"""Gate for the canonical package roster (scripts/config_matrix/roster.py).

The roster exists to kill six verbatim copies of the same package list. That only
holds if nothing quietly grows a seventh, and if the deferral maps stay honest --
both are the kind of thing that rots silently, which is what this pins.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_matrix.registry import REGISTRY
from config_matrix.roster import (
    DOCS_DEFERRED,
    DOCUMENTED,
    README_DEFERRED,
    derive_roster,
)

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def test_documented_is_derived_from_the_registry():
    """Adding a PackageSpec must be the ONE edit that onboards a package."""
    assert DOCUMENTED == tuple(REGISTRY)


def test_roster_importable_without_pydantic():
    """The stdlib-only doc gates import this on a bare setup-python runner.

    `config_matrix/__init__.py` re-exports the pydantic-dependent render half
    lazily to keep that true. An eager re-export would make check_docs_consistency
    and check_docs_sections uninstallable in their own CI jobs -- which is exactly
    why they each had a hardcoded copy of the roster before.
    """
    probe = (
        f"import sys; sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "sys.modules['pydantic'] = None\n"
        "from config_matrix.roster import DOCUMENTED, derive_roster\n"
        "assert DOCUMENTED and derive_roster()\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr


# Every per-package job matrix in ci.yml, and what it is allowed to be. A matrix
# that should equal the roster is a copy of it and drifts like one; a deliberate
# SUBSET is fine but has to say so here.
_PACKAGE_MATRICES: dict[str, str] = {
    "api_parity": "roster",   # gates every documented package's cross-language surface
    "native_symbols": "subset",  # only packages that ship a native kernel
}


def _ci_package_matrices() -> dict[str, set[str]]:
    """job name -> the package set in its `package: [...]` matrix."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    job = None
    for line in ci.splitlines():
        m = re.match(r"^  ([a-z_][a-z0-9_]*):\s*$", line)
        if m:
            job = m.group(1)
        m = re.match(r"^\s*package:\s*\[([^\]]+)\]\s*$", line)
        if m and job:
            out[job] = {p.strip() for p in m.group(1).split(",") if p.strip()}
    return out


def test_ci_package_matrices_are_declared_and_track_the_roster():
    """A hardcoded per-package job matrix is another copy of the roster.

    The `config_matrix` job's 6-leg matrix used to be one; that job was merged into
    `docs_regen`, which covers every package in ONE process, so the list is gone.
    Two matrices remain and they are NOT the same kind of thing: `api_parity` gates
    every documented package (so it must equal the roster), while `native_symbols`
    is a deliberate subset (only packages shipping a native kernel). Anything new
    has to declare which it is rather than quietly becoming a stale copy.
    """
    found = _ci_package_matrices()
    undeclared = sorted(set(found) - set(_PACKAGE_MATRICES))
    assert not undeclared, (
        f"ci.yml grew a per-package matrix in job(s) {undeclared}. Declare it in "
        "_PACKAGE_MATRICES as 'roster' or 'subset' -- an undeclared one is a "
        "hardcoded roster copy waiting to drift."
    )
    for job, kind in _PACKAGE_MATRICES.items():
        if job not in found:
            continue  # job removed; nothing to drift
        pkgs = found[job]
        if kind == "roster":
            assert pkgs == set(DOCUMENTED), (
                f"ci.yml `{job}` matrix {sorted(pkgs)} != roster {sorted(DOCUMENTED)}"
            )
        else:
            assert pkgs <= set(DOCUMENTED), (
                f"ci.yml `{job}` matrix names non-roster package(s): "
                f"{sorted(pkgs - set(DOCUMENTED))}"
            )


def test_no_script_rehardcodes_the_roster():
    """Guard against a seventh copy of the list appearing."""
    offenders = []
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name in {"test_roster.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\[[^\]\n]*\"goldenmatch\"[^\]\n]*\]", text):
            literal = m.group(0)
            names = set(re.findall(r'"([a-z-]+)"', literal))
            # A hardcoded roster is a literal naming most of the suite. A short
            # list naming two or three packages is a legitimate local subset.
            if len(names & set(DOCUMENTED)) >= 5:
                offenders.append(f"{path.name}: {literal[:70]}")
    assert not offenders, (
        "hardcoded roster copies found -- import DOCUMENTED from "
        "config_matrix.roster instead:\n  " + "\n  ".join(offenders)
    )


def test_deferrals_are_live_and_complete():
    """Every published-but-undocumented package is declared, and no entry is stale."""
    core, _, _ = derive_roster()
    docs_site = ROOT / "docs-site"

    undocumented = {p for p in core if not (docs_site / p).is_dir()}
    assert undocumented == set(DOCS_DEFERRED), (
        f"DOCS_DEFERRED is out of step: undeclared={sorted(undocumented - set(DOCS_DEFERRED))}, "
        f"stale={sorted(set(DOCS_DEFERRED) - undocumented)}"
    )

    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    unlinked = {p for p in core if f"packages/python/{p}/readme.md" not in readme}
    assert unlinked == set(README_DEFERRED), (
        f"README_DEFERRED is out of step: undeclared={sorted(unlinked - set(README_DEFERRED))}, "
        f"stale={sorted(set(README_DEFERRED) - unlinked)}"
    )


def test_every_deferral_carries_a_reason():
    for name, reason in {**DOCS_DEFERRED, **README_DEFERRED}.items():
        assert len(reason.strip()) > 30, f"{name}: deferral reason is too thin to review"


def test_documented_roster_is_actually_published():
    """A PackageSpec for a package no publisher ships is docs for a phantom dist."""
    core, _, _ = derive_roster()
    assert set(DOCUMENTED) <= set(core), sorted(set(DOCUMENTED) - set(core))


def test_all_matches_the_public_surface():
    """`__all__` is the module's stated contract; keep it honest.

    Added in response to a CodeQL "unused global variable" finding on DOCUMENTED.
    The finding is a false positive -- the constant has eight importers -- but the
    module genuinely had no declared export list, and the suggested workaround (a
    no-op `_ = DOCUMENTED` read inside derive_roster) would have been a line whose
    only purpose is to fool static analysis. This asserts the real thing instead.
    """
    import config_matrix.roster as mod

    declared = set(mod.__all__)
    public = {
        n for n in vars(mod)
        if not n.startswith("_")
        and n not in {"annotations", "Path", "REGISTRY", "ROOT", "PY_PKGS"}
    }
    assert declared == public, (
        f"__all__ is out of step: missing={sorted(public - declared)}, "
        f"stale={sorted(declared - public)}"
    )
