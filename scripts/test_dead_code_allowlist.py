"""The allowlist, and the guard that stops it rotting.

An entry naming a module that no longer exists can never match, so it quietly
shrinks the audit while looking like documentation. That is the same failure as
a coverage floor on a deleted module.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import dead_code.allowlist as allowlist_module  # noqa: E402
from dead_code.allowlist import load_allowlist, stale_entries  # noqa: E402


def test_entries_parse_and_strip_reasons():
    entries = load_allowlist()
    assert all("#" not in e for e in entries)
    assert all(e == e.strip() for e in entries)


def test_known_external_integrations_are_allowlisted():
    """These sit at 0% coverage because they need external services, not
    because they are dead. Deleting them removes working integrations."""
    entries = load_allowlist()
    for mod in (
        "goldenmatch.identity.mongo_backend",
        "goldenmatch.core.vertex_embedder",
        "goldenmatch.connectors.bigquery",
        "goldenmatch.connectors.hubspot",
    ):
        assert mod in entries, f"{mod} must be allowlisted with a reason"


def test_no_stale_entries():
    assert stale_entries() == set()


def test_missing_allowlist_file_raises(tmp_path):
    """A missing allowlist file is a hard failure, not a safe default.

    If the allowlist file goes missing (bad rebase, path change, partial
    checkout), the guard must fail loudly. An empty allowlist would silently
    allow Task 7 to delete live integrations (MongoDB, BigQuery, HubSpot,
    Vertex AI) with every test still green.
    """
    fake_missing_path = tmp_path / "nonexistent" / "dead_code.allow"
    with mock.patch.object(allowlist_module, "ALLOW", fake_missing_path):
        with pytest.raises(FileNotFoundError) as exc_info:
            load_allowlist()
        assert "not found" in str(exc_info.value)
        assert "live integrations" in str(exc_info.value)


def test_empty_allowlist_file_is_permitted(tmp_path):
    """An empty allowlist file (comments and whitespace only) is permitted.

    The file's presence documents the allowlist mechanism even if no modules
    are currently allowlisted. However, in practice, entries are always present
    because live integrations (MongoDB, BigQuery, HubSpot, Vertex AI) require
    allowlisting due to CI lacking credentials.
    """
    empty_allow = tmp_path / "dead_code.allow"
    empty_allow.write_text("# Empty allowlist (comments and whitespace only)\n", encoding="utf-8")
    with mock.patch.object(allowlist_module, "ALLOW", empty_allow):
        entries = load_allowlist()
        assert entries == set()


def test_real_allowlist_loads_five_modules():
    """The real allowlist file loads and contains the five expected modules."""
    entries = load_allowlist()
    expected = {
        "goldenmatch.identity.mongo_backend",
        "goldenmatch.connectors.mongo",
        "goldenmatch.core.vertex_embedder",
        "goldenmatch.connectors.bigquery",
        "goldenmatch.connectors.hubspot",
    }
    assert entries == expected
