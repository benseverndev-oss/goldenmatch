"""Smoke test: the package imports and reports its version."""

from __future__ import annotations


def test_import_and_version() -> None:
    import tomllib
    from pathlib import Path

    import goldenanalysis

    # NOT a hardcoded literal. A literal has to be hand-edited at every release
    # and tests nothing -- it restates the version rather than checking a
    # relationship. (The goldenpipe twin of this assertion failed the
    # 2026-08-15 repo-wide cut; this one passed only because goldenanalysis was
    # already bumped, so it was one release away from the same break.)
    #
    # The relationship worth asserting is that __version__ tracks
    # pyproject.toml, the drift scripts/check_version_consistency.py exists to
    # catch repo-wide.
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert goldenanalysis.__version__ == declared
