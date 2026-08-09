"""Unit tests for the context-budget gate and the commit-message gate.

Both exist because a documented trap kept firing. The tests pin the behaviour
that makes them worth having: the budget must bite on growth but never on an
improvement, and the commit-message check must match the way GitHub Actions
actually matches -- anywhere in the message, prose included.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_commit_msg as msg  # noqa: E402
import check_context_budget as budget  # noqa: E402

# --------------------------------------------------------------------------
# context budget
# --------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A fake repo whose CLAUDE.md sizes the gate will measure."""
    monkeypatch.setattr(budget, "ROOT", tmp_path)

    def write(rel: str, size: int) -> Path:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * size, encoding="utf-8")
        return p

    return write


def test_over_budget_fails(tree, monkeypatch):
    monkeypatch.setattr(budget, "BUDGETS", {"CLAUDE.md": 100})
    tree("CLAUDE.md", 500)
    failures, _, _ = budget.check()
    assert len(failures) == 1
    assert "exceeds its 100-byte budget by 400" in failures[0]


def test_within_budget_passes(tree, monkeypatch):
    monkeypatch.setattr(budget, "BUDGETS", {"CLAUDE.md": 1000})
    tree("CLAUDE.md", 900)
    assert budget.check()[0] == []


def test_exactly_at_budget_passes(tree, monkeypatch):
    """The budget is a ceiling, not a strict inequality -- off-by-one matters."""
    monkeypatch.setattr(budget, "BUDGETS", {"CLAUDE.md": 500})
    tree("CLAUDE.md", 500)
    assert budget.check()[0] == []


def test_shrinking_is_reported_but_never_fails(tree, monkeypatch):
    """A gate that punishes an improvement gets switched off."""
    monkeypatch.setattr(budget, "BUDGETS", {"CLAUDE.md": 10_000})
    tree("CLAUDE.md", 1_000)
    failures, notes, _ = budget.check()
    assert failures == []
    assert len(notes) == 1
    assert "tightening the ratchet" in notes[0]


def test_unlisted_file_uses_the_default_budget(tree, monkeypatch):
    monkeypatch.setattr(budget, "BUDGETS", {})
    monkeypatch.setattr(budget, "DEFAULT_BUDGET", 200)
    tree("packages/x/CLAUDE.md", 500)
    failures, _, _ = budget.check()
    assert len(failures) == 1
    assert "by adding an explicit entry" in failures[0]


def test_stale_budget_entry_fails(tree, monkeypatch):
    """A manifest naming missing files can't be trusted to name the present ones."""
    monkeypatch.setattr(budget, "BUDGETS", {"gone/CLAUDE.md": 1000})
    tree("CLAUDE.md", 10)
    failures, _, _ = budget.check()
    assert any("has a budget entry but does not exist" in f for f in failures)


def test_scan_floor_reports_broken_not_clean(tree, monkeypatch, capsys):
    monkeypatch.setattr(budget, "BUDGETS", {})
    tree("CLAUDE.md", 10)
    assert budget.main([]) == 2
    assert "the scan is broken" in capsys.readouterr().err


def test_the_repo_as_it_stands_is_within_budget():
    """End-to-end on the real tree -- the state the gate must hold."""
    failures, _, scanned = budget.check()
    assert scanned >= budget.MIN_EXPECTED_FILES, scanned
    assert failures == [], failures


def test_every_budget_entry_names_a_real_file():
    missing = [rel for rel in budget.BUDGETS if not (budget.ROOT / rel).exists()]
    assert not missing, missing


# --------------------------------------------------------------------------
# commit message
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "chore: x\n\n[skip ci]\n",
        "chore: x\n\n[ci skip]\n",
        "chore: x\n\n[no ci]\n",
        "chore: x\n\n[skip actions]\n",
        "chore: x\n\n[SKIP CI]\n",  # case-insensitive
        "chore: x\n\n[ skip ci ]\n",  # padded
        # The two that actually happened: the directive in DESCRIPTIVE prose.
        "chore: re-trigger CI after the [skip ci] baseline\n",
        "docs: explain the [skip ci] trap\n",
    ],
)
def test_directives_are_rejected(tmp_path, body):
    p = tmp_path / "COMMIT_EDITMSG"
    p.write_text(body, encoding="utf-8")
    assert msg.main([str(p)]) == 1


@pytest.mark.parametrize(
    "body",
    [
        "feat: a normal message\n\nwith a body\n",
        "docs: the skip-CI-directive trap\n",  # the documented workaround
        "chore: skip ci without brackets is not a directive\n",
        "chore: mentions [skipping] and [cider] but neither matches\n",
    ],
)
def test_clean_messages_pass(tmp_path, body):
    p = tmp_path / "COMMIT_EDITMSG"
    p.write_text(body, encoding="utf-8")
    assert msg.main([str(p)]) == 0


def test_comment_lines_are_ignored(tmp_path):
    """git strips `#` lines before storing, so they never reach Actions."""
    p = tmp_path / "COMMIT_EDITMSG"
    p.write_text("feat: x\n\n# [skip ci] would be stripped by git\n", encoding="utf-8")
    assert msg.main([str(p)]) == 0


def test_reports_line_number_and_match(tmp_path, capsys):
    p = tmp_path / "COMMIT_EDITMSG"
    p.write_text("feat: x\n\nbody\nthe [ci skip] mention\n", encoding="utf-8")
    msg.main([str(p)])
    err = capsys.readouterr().err
    assert "line 4" in err
    assert "[ci skip]" in err
    assert "--no-verify" in err  # the deliberate-override escape hatch


def test_missing_argument_is_a_usage_error(capsys):
    assert msg.main([]) == 2


def test_unreadable_file_is_an_error_not_a_pass(tmp_path):
    assert msg.main([str(tmp_path / "nope")]) == 2
