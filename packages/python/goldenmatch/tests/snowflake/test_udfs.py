"""Unit tests for the Snowflake handler module.

Phase 1 handlers (the 8 GoldenFlow transforms + 5 identity reads) are
exercised against real upstream implementations -- no live Snowflake
required because the handlers are pure Python and the IdentityStore
runs against an on-disk SQLite file the test seeds.

Phase 2 scaffolds (correction_add, scan_table, health_score,
DedupeFull / Clusters / Pairs) are asserted to raise NotImplementedError
with the documented "ships in Phase 2" message.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from goldenmatch.snowflake import udfs

# ---------------------------------------------------------------------------
# GoldenFlow transforms (8) -- pure-string handlers.
# ---------------------------------------------------------------------------


def test_normalize_email_lowercases() -> None:
    assert udfs.normalize_email("  Foo@BAR.com  ") == "foo@bar.com"


def test_normalize_email_strips_plus_tag() -> None:
    # email_normalize drops +tags on Gmail and elsewhere.
    out = udfs.normalize_email("alice+spam@gmail.com")
    assert out == "alice@gmail.com"


def test_normalize_email_none_passthrough() -> None:
    assert udfs.normalize_email(None) is None


def test_normalize_phone_e164() -> None:
    out = udfs.normalize_phone("+1 (415) 555-2671")
    # phone_e164 returns E.164 when parseable.
    assert out == "+14155552671"


def test_normalize_phone_unparseable_passes_through() -> None:
    # phone_e164 preserves the input when phonenumbers can't parse it --
    # the handler just propagates that policy.
    assert udfs.normalize_phone("not-a-phone") == "not-a-phone"


def test_normalize_date_iso8601() -> None:
    out = udfs.normalize_date("2023-04-15")
    assert out == "2023-04-15"
    out2 = udfs.normalize_date("04/15/2023")
    assert out2 == "2023-04-15"


def test_normalize_name_proper_titlecases() -> None:
    out = udfs.normalize_name_proper("JOHN SMITH")
    # name_proper returns title-cased form.
    assert out is not None
    assert out.lower() == "john smith"
    assert out[0].isupper()


def test_canonicalize_url_normalizes() -> None:
    out = udfs.canonicalize_url("HTTPS://Example.COM/Foo/")
    # url_normalize lowercases the scheme + host; path case preserved.
    # CodeQL flags startswith("https://...") as an incomplete URL
    # sanitization check (it's looking for URL allowlists, not test
    # assertions) -- use ``split('/')`` to make the intent unambiguous.
    assert out is not None
    parts = out.split("/")
    assert parts[0] == "https:"
    assert parts[2] == "example.com"


def test_canonicalize_address_standardizes() -> None:
    out = udfs.canonicalize_address("123 Main Street, Apt #4")
    # address_standardize abbreviates Street -> St and normalizes #.
    assert out is not None
    # Some transformation happened.
    assert out != "123 Main Street, Apt #4"


def test_strip() -> None:
    # Ruff B005 false-positives because the call shape matches
    # ``str.strip(chars)`` even though udfs.strip is a UDF handler.
    fn = udfs.strip
    assert fn("  hello  ") == "hello"
    assert fn("hello") == "hello"
    assert fn("") == ""
    assert fn(None) is None


def test_whitespace_normalize() -> None:
    assert udfs.whitespace_normalize("  a   b\tc\n d ") == "a b c d"
    assert udfs.whitespace_normalize("hello") == "hello"
    assert udfs.whitespace_normalize(None) is None


# ---------------------------------------------------------------------------
# Identity reads (5) -- against a seeded SQLite IdentityStore.
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_identity_db(tmp_path: Path) -> Path:
    """Build an IdentityStore with one identity + record + history event
    so the read UDFs have something to find."""
    from goldenmatch.identity.model import (
        EvidenceEdge,
        IdentityEvent,
        IdentityNode,
        SourceRecord,
    )
    from goldenmatch.identity.store import IdentityStore, new_entity_id

    db = tmp_path / "identity.db"
    store = IdentityStore(backend="sqlite", path=str(db))
    eid = new_entity_id()
    store.upsert_identity(IdentityNode(
        entity_id=eid, dataset="customers", status="active",
        confidence=0.99, merged_into=None,
    ))
    store.upsert_record(SourceRecord(
        record_id="salesforce:001",
        source="salesforce",
        source_pk="001",
        record_hash="hash-1",
        entity_id=eid,
        payload={"name": "Alice Smith", "email": "alice@example.com"},
        dataset="customers",
    ))
    store.add_edge(EvidenceEdge(
        entity_id=eid,
        record_a_id="salesforce:001",
        record_b_id="hubspot:42",
        kind="same_as",
        score=0.91,
        matchkey_name="email_exact",
        run_name="test",
    ))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="created", payload={"source": "test"},
        run_name="test",
    ))
    store.close()
    return db


def test_identity_resolve_returns_view(seeded_identity_db: Path) -> None:
    out = udfs.identity_resolve("salesforce:001", str(seeded_identity_db))
    assert out is not None
    assert out["dataset"] == "customers"
    assert out["status"] == "active"
    # The records list includes the seeded record.
    record_ids = [r["record_id"] for r in out["records"]]
    assert "salesforce:001" in record_ids


def test_identity_resolve_unknown_returns_none(seeded_identity_db: Path) -> None:
    out = udfs.identity_resolve("nope:nada", str(seeded_identity_db))
    assert out is None


def test_identity_view_round_trip(seeded_identity_db: Path) -> None:
    # First resolve to get the entity_id.
    resolved = udfs.identity_resolve("salesforce:001", str(seeded_identity_db))
    assert resolved is not None
    eid = resolved["entity_id"]
    out = udfs.identity_view(eid, str(seeded_identity_db))
    assert out is not None
    assert out["entity_id"] == eid


def test_identity_history_returns_events(seeded_identity_db: Path) -> None:
    resolved = udfs.identity_resolve("salesforce:001", str(seeded_identity_db))
    assert resolved is not None
    eid = resolved["entity_id"]
    events = udfs.identity_history(eid, str(seeded_identity_db))
    kinds = [e["kind"] for e in events]
    assert "created" in kinds


def test_identity_conflicts_lists_edges(seeded_identity_db: Path) -> None:
    # The seeded edge has kind="match" -- find_conflicts filters to
    # `conflicts_with` edges, so it should return empty for this dataset.
    out = udfs.identity_conflicts("customers", str(seeded_identity_db))
    assert isinstance(out, list)


def test_identity_list_returns_dataset(seeded_identity_db: Path) -> None:
    out = udfs.identity_list("customers", "active", str(seeded_identity_db))
    assert len(out) == 1
    assert out[0]["dataset"] == "customers"
    assert out[0]["status"] == "active"


def test_identity_list_empty_filter_returns_all(seeded_identity_db: Path) -> None:
    out = udfs.identity_list("", "", str(seeded_identity_db))
    # Empty strings collapse to None filters; still returns the seeded row.
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Phase 2 scaffolds -- assert clean NotImplementedError, not silent failure.
# ---------------------------------------------------------------------------


def test_correction_add_phase_2_stub() -> None:
    with pytest.raises(NotImplementedError, match="Phase 2"):
        udfs.correction_add("approve", "customers", "", "{}")


def test_scan_table_phase_2_stub() -> None:
    with pytest.raises(NotImplementedError, match="Phase 2"):
        udfs.scan_table("my_table", "")


def test_health_score_phase_2_stub() -> None:
    with pytest.raises(NotImplementedError, match="Phase 2"):
        udfs.health_score("my_table")


@pytest.mark.parametrize("klass", [
    udfs.DedupeFull, udfs.DedupeClusters, udfs.DedupePairs,
])
def test_dedupe_classes_phase_2_stub(klass) -> None:
    with pytest.raises(NotImplementedError, match="Phase 2"):
        klass().process("my_table", "{}")


# ---------------------------------------------------------------------------
# IMPORTS-directory resolution.
# ---------------------------------------------------------------------------


def test_import_dir_picks_up_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GOLDENMATCH_UDF_IMPORTS", str(tmp_path))
    assert udfs._import_dir() == tmp_path


def test_resolve_db_path_relative_to_imports(tmp_path: Path,
                                              monkeypatch) -> None:
    monkeypatch.setenv("GOLDENMATCH_UDF_IMPORTS", str(tmp_path))
    out = udfs._resolve_db_path("custom.db")
    assert out == str(tmp_path / "custom.db")


def test_resolve_db_path_absolute_passthrough(tmp_path: Path) -> None:
    # Use a real absolute path so the test runs on both Windows and
    # POSIX. ``Path("/var/...").is_absolute()`` is False on Windows.
    abs_path = str(tmp_path / "identity.db")
    assert udfs._resolve_db_path(abs_path) == abs_path


def test_resolve_db_path_empty_falls_back_to_default(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("GOLDENMATCH_UDF_IMPORTS", str(tmp_path))
    out = udfs._resolve_db_path("")
    assert out == str(tmp_path / "identity.db")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
