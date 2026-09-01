"""The allowlist, and the guard that stops it rotting.

An entry naming a module that no longer exists can never match, so it quietly
shrinks the audit while looking like documentation. That is the same failure as
a coverage floor on a deleted module.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

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
