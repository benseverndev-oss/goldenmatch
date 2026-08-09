"""Unit tests for the CLAUDE.md path-reference gate.

Two properties matter: it must catch a path that exists nowhere (the rot class
that put 34 dead references in the tree), and it must NOT fire on the way these
docs are actually written -- tail-relative references to a sibling package's
files, placeholders, env vars, URLs.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_claude_refs as mod  # noqa: E402


@pytest.fixture
def md(tmp_path):
    """Write a CLAUDE.md and return the path-like references found in it."""

    def parse(body: str) -> dict[str, int]:
        p = tmp_path / "CLAUDE.md"
        p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        return mod.references(p)

    return parse


# --- extraction ------------------------------------------------------------


def test_backticked_path_is_extracted(md):
    assert "scripts/check_api_parity.py" in md("See `scripts/check_api_parity.py` for the gate.\n")


def test_markdown_link_is_extracted(md):
    assert "docs/design/thing.md" in md("See [the design](docs/design/thing.md).\n")


def test_line_number_is_reported(md):
    refs = md("""
        line one
        line two with `scripts/x.py`
    """)
    assert refs["scripts/x.py"] == 2


@pytest.mark.parametrize(
    "token",
    [
        "GOLDENMATCH_NATIVE",          # env var, no slash
        "score_strings",               # function name
        "--timeout=120",               # a flag
        "https://example.com/a.py",    # URL
        "packages/python/<pkg>/x.py",  # placeholder
        "core/**/*.py",                # glob
        "~/.railway/config.json",      # home-relative, not in the repo
        "/docs/llms.txt",              # site path, not a file path
        ".../pyproject.toml",          # elided
        "goldenmatch.core.cluster",    # dotted module, no slash
    ],
)
def test_non_paths_are_not_extracted(md, token):
    assert md(f"prose `{token}` prose\n") == {}


def test_anchor_is_stripped_from_a_link(md):
    assert "docs/x.md" in md("[x](docs/x.md#section)\n")


# --- resolution ------------------------------------------------------------


def test_exact_tracked_path_resolves():
    resolves = mod._resolver({"scripts/check_claude_refs.py"})
    assert resolves("scripts/check_claude_refs.py")


def test_tail_relative_reference_resolves():
    """A python package's doc naming its TS sibling's file, the house style."""
    resolves = mod._resolver({"packages/typescript/infermap/core/scorers/registry.ts"})
    assert resolves("core/scorers/registry.ts")


def test_path_that_exists_nowhere_does_not_resolve():
    resolves = mod._resolver({"packages/python/goldenmatch/core/cluster.py"})
    assert not resolves("core/not_a_real_file.py")


def test_basename_match_alone_is_not_enough():
    """`a/b/thing.py` must not be satisfied by an unrelated `z/thing.py`."""
    resolves = mod._resolver({"z/thing.py"})
    assert not resolves("a/b/thing.py")


# --- end to end ------------------------------------------------------------


def test_allow_entries_all_carry_a_reason():
    assert mod._ALLOW, "an empty allowlist means the declared-exception discipline lapsed"
    for token, reason in mod._ALLOW.items():
        assert reason.strip(), f"{token} has no reason"


def test_scan_floor_rejects_a_scan_that_found_nothing(monkeypatch, capsys):
    monkeypatch.setattr(mod, "check", lambda: ([], 0, 0))
    assert mod.main([]) == 2
    assert "the scan is broken" in capsys.readouterr().err


def test_the_repo_as_it_stands_passes():
    """End-to-end on the real tree -- the state the gate must hold."""
    problems, files, candidates = mod.check()
    assert files >= mod.MIN_CLAUDE_FILES, files
    assert candidates >= mod.MIN_CANDIDATES, candidates
    assert problems == [], [
        f"{p.name}:{line}: {token}" for p, line, token in problems
    ]
