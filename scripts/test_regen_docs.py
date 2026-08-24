"""Gate for the derived-docs entry point itself (scripts/regen_docs.py).

`regen_docs.py` is a hand-maintained registry of hand-maintained registries:
WRITE_STEPS lists the generators, GENERATED_PATHS lists what they write, and
nothing checked either. Both rot the same silent way -- a new generator lands
outside the entry point, or a generator starts writing a path the drift probe
does not watch, and `make docs` quietly stops covering it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from regen_docs import GENERATED_PATHS, PROSE_CHECKS, WRITE_STEPS, _drifted_paths

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _step_scripts() -> set[str]:
    return {step[0] for step in WRITE_STEPS}


def test_every_write_capable_generator_is_a_write_step():
    """A generator outside WRITE_STEPS is one `make docs` silently does not run.

    Scoped to scripts that actually expose `--write`; the golden-fixture emitters
    (gen_simhash_golden, gen_sketch_golden, ...) write test fixtures, not docs, and
    take no such flag, so they exclude themselves.
    """
    missing = []
    for path in sorted(SCRIPTS.glob("gen_*.py")) + [SCRIPTS / "agent_codemap.py"]:
        if "--write" not in path.read_text(encoding="utf-8"):
            continue
        rel = f"scripts/{path.name}"
        if rel not in _step_scripts():
            missing.append(rel)
    assert not missing, (
        "generator(s) support --write but are not in regen_docs.WRITE_STEPS, so "
        f"`make docs` does not run them: {missing}"
    )


def test_generated_paths_all_resolve():
    """A typo'd pathspec matches nothing and silently narrows the drift probe.

    `:(glob)` magic is easy to get subtly wrong (and a PLAIN pathspec's `*` crosses
    `/`, which is why the magic is there at all), so assert each entry names real
    tracked files rather than trusting it by eye.
    """
    empty = []
    for spec in GENERATED_PATHS:
        out = subprocess.run(
            ["git", "ls-files", "--", spec],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout.strip()
        if not out:
            empty.append(spec)
    assert not empty, f"GENERATED_PATHS entries match no tracked file: {empty}"


def test_prose_checks_never_write():
    """PROSE_CHECKS is the report-only tail; a --write there would be invisible."""
    for check in PROSE_CHECKS:
        assert "--check" in check, f"PROSE_CHECKS entry is not a --check: {check}"
        assert "--write" not in check, f"PROSE_CHECKS entry writes: {check}"


def _tree_is_clean() -> bool:
    return not _drifted_paths()


@pytest.mark.skipif(
    not _tree_is_clean(),
    reason="working tree already has uncommitted generated-doc changes",
)
def test_drift_probe_detects_a_modified_tracked_file():
    page = ROOT / "docs-site" / "suite-matrix.mdx"
    original = page.read_bytes()
    try:
        page.write_bytes(original + b"\ndrift\n")
        assert _drifted_paths(), "a modified generated page was not detected as drift"
    finally:
        page.write_bytes(original)
    assert not _drifted_paths(), "tree not restored after the modification probe"


@pytest.mark.skipif(
    not _tree_is_clean(),
    reason="working tree already has uncommitted generated-doc changes",
)
def test_drift_probe_detects_a_brand_new_untracked_file():
    """The Phase-1 regression: `git diff` sees only TRACKED changes.

    A generator emitting a page that has never been committed -- the "onboard a
    package" case -- left the gate green with the file sitting uncommitted. The
    probe is `git status --porcelain` for exactly this.
    """
    scratch = ROOT / "docs-site" / "reference" / "_drift_probe.mdx"
    assert not scratch.exists()
    try:
        scratch.write_text("probe\n", encoding="utf-8")
        drifted = _drifted_paths()
        assert any("_drift_probe" in line for line in drifted), (
            f"a brand-new generated page was not detected as drift: {drifted}"
        )
    finally:
        scratch.unlink(missing_ok=True)
    assert not _drifted_paths(), "tree not restored after the untracked probe"
