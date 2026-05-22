"""Phase 4 follow-up: Golden / Matches key-binding wiring (task #211).

Confirms the `e` binding on the Golden tab + the `c` binding on the
Matches tab dispatch to the respective modals. Full pilot-driven UX
testing of the modal-open flow is covered by the existing pilot tests
in test_tui_corrections.py; this file is a focused binding presence +
action-shape check.
"""
from __future__ import annotations

import pytest


def test_golden_tab_binds_e_to_edit_action():
    """`e` keybinding on GoldenTab maps to action_edit_golden_field."""
    from goldenmatch.tui.tabs.golden_tab import GoldenTab

    bindings = list(GoldenTab.BINDINGS)
    assert any(
        (b.key, b.action) == ("e", "edit_golden_field")
        for b in bindings
    ), f"GoldenTab missing 'e' binding (have: {[(b.key, b.action) for b in bindings]})"
    # Action handler exists.
    assert hasattr(GoldenTab, "action_edit_golden_field")


def test_matches_tab_binds_c_to_correct_action():
    """`c` keybinding on MatchesTab maps to action_correct_pair."""
    from goldenmatch.tui.tabs.matches_tab import MatchesTab

    bindings = list(MatchesTab.BINDINGS)
    assert any(
        (b.key, b.action) == ("c", "correct_pair")
        for b in bindings
    ), f"MatchesTab missing 'c' binding (have: {[(b.key, b.action) for b in bindings]})"
    assert hasattr(MatchesTab, "action_correct_pair")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
