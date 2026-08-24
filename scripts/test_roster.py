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


def test_ci_config_matrix_leg_matches_the_roster():
    """ci.yml's config_matrix job runs one leg per package, as a literal list.

    A package added to REGISTRY but not to that list gets a generated page nothing
    gates -- the drift class this whole battery exists to prevent.
    """
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    m = re.search(r"config_matrix:.*?matrix:\s*\n\s*package:\s*\[([^\]]+)\]", ci, re.S)
    assert m, "could not locate the config_matrix job's package matrix in ci.yml"
    legs = {p.strip() for p in m.group(1).split(",") if p.strip()}
    assert legs == set(DOCUMENTED), (
        f"ci.yml config_matrix legs {sorted(legs)} != roster {sorted(DOCUMENTED)}"
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
