"""End-to-end: dedupe + identity graph integration."""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    IdentityConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)
from goldenmatch.core.pipeline import run_dedupe_df
from goldenmatch.identity import IdentityStore


def _people_df():
    return pl.DataFrame({
        "id":    ["1", "2", "3", "4"],
        "name":  ["Alice Smith", "Alyce Smith", "Bob Jones", "Robert Jones"],
        "email": ["a@x.com", "a@x.com", "b@y.com", "b@y.com"],
        "zip":   ["12345", "12345", "67890", "67890"],
    })


def _config(identity_path: str, run_name: str) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        output=OutputConfig(run_name=run_name),
        matchkeys=[MatchkeyConfig(
            name="people_fuzzy",
            type="weighted",
            threshold=0.85,
            fields=[
                MatchkeyField(field="name",  scorer="jaro_winkler", weight=0.7),
                MatchkeyField(field="email", scorer="exact",        weight=0.3),
            ],
        )],
        blocking=BlockingConfig(strategy="static", keys=[
            BlockingKeyConfig(fields=["zip"]),
        ]),
        identity=IdentityConfig(
            enabled=True, path=identity_path, source_pk_column="id",
            dataset="people-test",
        ),
    )


def test_identity_persists_across_runs(tmp_path):
    db = str(tmp_path / "identity.db")
    df = _people_df()

    r1 = run_dedupe_df(df, _config(db, "r1"), source_name="src")
    assert r1["identity_summary"] is not None
    # 2 clusters above threshold (Alice/Alyce, Bob/Robert) -> 2 identities
    assert r1["identity_summary"]["created"] >= 2

    with IdentityStore(path=db) as s:
        eid_alice_run1 = s.find_entity_by_record("src:1")
        eid_bob_run1 = s.find_entity_by_record("src:3")
        assert eid_alice_run1 is not None
        assert eid_bob_run1 is not None
        assert eid_alice_run1 != eid_bob_run1

    # Rerun with one new record that should join Alice's cluster.
    df2 = pl.concat([df, pl.DataFrame({
        "id":    ["5"],
        "name":  ["Alyss Smith"],
        "email": ["a@x.com"],
        "zip":   ["12345"],
    })])
    r2 = run_dedupe_df(df2, _config(db, "r2"), source_name="src")
    assert r2["identity_summary"]["absorbed_records"] >= 1

    with IdentityStore(path=db) as s:
        assert s.find_entity_by_record("src:1") == eid_alice_run1
        assert s.find_entity_by_record("src:5") == eid_alice_run1


def test_identity_disabled_by_default(tmp_path):
    df = _people_df()
    cfg = GoldenMatchConfig(
        output=OutputConfig(run_name="r"),
        matchkeys=[MatchkeyConfig(
            name="x", type="weighted", threshold=0.85,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        )],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["zip"])]),
    )
    result = run_dedupe_df(df, cfg, source_name="src")
    assert result["identity_summary"] is None
