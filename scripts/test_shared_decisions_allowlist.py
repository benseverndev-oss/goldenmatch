"""Allowlist for fields whose readers are known to agree.

Mirrors parity/dead_code/*.yaml's contract: an entry is a claim that the readers
DO agree and someone checked, not that we would rather not look.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.allowlist import load_allowlist  # noqa: E402

ALLOW = Path(__file__).resolve().parent.parent / "parity" / "shared_decisions.allow"


def test_allowlist_loads():
    entries = load_allowlist()
    assert isinstance(entries, set)


def test_a_missing_allowlist_raises_rather_than_returning_empty(monkeypatch):
    """A silently-empty allowlist disables the only thing standing between the
    inventory and a reviewer's time. Phase A shipped exactly this bug."""
    import shared_decisions.allowlist as mod

    monkeypatch.setattr(mod, "ALLOWLIST", Path("does-not-exist.allow"))
    with pytest.raises(FileNotFoundError):
        mod.load_allowlist()


def test_an_entry_without_a_reason_is_rejected():
    """The shipped allowlist is EMPTY at B0a, so a loop over it never executes
    and would pass whatever the format rule was. Drive the rule with a synthetic
    entry instead, so the test can actually fail."""
    from shared_decisions.allowlist import entries_missing_reasons

    good = ["field_a  # checked 2026-09-02, both readers agree"]
    bad = ["field_b", "field_c  # fine"]
    assert entries_missing_reasons(good) == []
    assert entries_missing_reasons(bad) == ["field_b"]


def test_the_shipped_allowlist_obeys_the_format():
    """Vacuous while the file is empty, which is correct -- it becomes a real
    check the moment B1 adds the first entry."""
    from shared_decisions.allowlist import entries_missing_reasons

    lines = ALLOW.read_text(encoding="utf-8").splitlines()
    assert entries_missing_reasons(lines) == []


def test_stale_entries_are_detected(tmp_path, monkeypatch):
    """A real witness: an allowlist naming a field that is no longer shared must
    be reported. Asserting against the SHIPPED allowlist cannot witness this --
    it is empty at B0a, so every assertion over it passes vacuously."""
    import shared_decisions.allowlist as mod

    allow = tmp_path / "shared_decisions.allow"
    allow.write_text(
        "gone_field  # was agreed in 2026\nkept_field  # still shared\n", encoding="utf-8"
    )
    monkeypatch.setattr(mod, "ALLOWLIST", allow)

    stale = mod.stale_entries({"kept_field"})
    assert stale == {"gone_field"}, stale
