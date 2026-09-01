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


def test_entries_are_bare_module_names():
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


def test_missing_allowlist_dir_raises(tmp_path):
    """A missing allowlist source is a hard failure, not a safe default.

    If parity/dead_code/ goes missing (bad rebase, path change, partial
    checkout), the guard must fail loudly. An empty allowlist would silently
    allow Task 7 to delete live integrations (MongoDB, BigQuery, HubSpot,
    Vertex AI) with every test still green.
    """
    fake_missing_dir = tmp_path / "nonexistent" / "dead_code"
    with mock.patch.object(allowlist_module, "ALLOW_DIR", fake_missing_dir):
        with pytest.raises(FileNotFoundError) as exc_info:
            load_allowlist()
        assert "not found" in str(exc_info.value)
        assert "live integrations" in str(exc_info.value)


def test_dir_with_no_yaml_files_raises(tmp_path):
    """A source directory that exists but holds no *.yaml maps is the same
    failure mode as a missing directory -- there is nothing to allowlist
    against, so it must not read as an (empty) safe default."""
    empty_dir = tmp_path / "dead_code"
    empty_dir.mkdir()
    (empty_dir / "README.md").write_text("not a yaml map\n", encoding="utf-8")
    with mock.patch.object(allowlist_module, "ALLOW_DIR", empty_dir):
        with pytest.raises(FileNotFoundError):
            load_allowlist()


def test_empty_yaml_map_is_permitted(tmp_path):
    """A yaml file with comments/whitespace only (no entries) is permitted.

    The file's presence documents the mechanism even if no modules are
    currently allowlisted for that package.
    """
    empty_dir = tmp_path / "dead_code"
    empty_dir.mkdir()
    (empty_dir / "goldenmatch.yaml").write_text(
        "# Empty allowlist (comments and whitespace only)\n", encoding="utf-8"
    )
    with mock.patch.object(allowlist_module, "ALLOW_DIR", empty_dir):
        entries = load_allowlist()
        assert entries == set()


def test_real_allowlist_loads_the_carried_over_modules():
    """The real allowlist union carries over every module the retired
    parity/dead_code.allow used to list (with reasons preserved in the yaml,
    not asserted here), plus the maps' own pre-existing entries."""
    entries = load_allowlist()
    carried_over = {
        "goldenmatch.identity.mongo_backend",
        "goldenmatch.connectors.mongo",
        "goldenmatch.core.vertex_embedder",
        "goldenmatch.connectors.bigquery",
        "goldenmatch.connectors.hubspot",
    }
    assert carried_over <= entries
    assert len(entries) >= len(carried_over)
