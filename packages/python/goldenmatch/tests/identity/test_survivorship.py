"""Survivorship learning + per-cell golden-record provenance (#1111, epic #1108)."""
from __future__ import annotations

import polars as pl
from goldenmatch.core.memory.store import Correction
from goldenmatch.identity.survivorship import (
    CellProvenance,
    GoldenRecordWithProvenance,
    build_golden_with_provenance,
    learn_field_survivorship,
    learned_field_strategies,
)


def _df():
    # Two source records for one entity: a sparse CRM row and a fuller web row.
    return pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["crm", "web"],
        "name": ["Jane", "Jane Doe"],
        "email": ["jane@x.com", None],
        "updated_at": ["2020-01-01", "2024-06-01"],
    })


# ── Per-cell provenance ─────────────────────────────────────────────────────


def test_provenance_traces_each_cell_to_a_source():
    res = build_golden_with_provenance(_df(), [0, 1])
    assert isinstance(res, GoldenRecordWithProvenance)
    # most_complete (default): longer "Jane Doe" wins name (from web/row 1).
    assert res.values["name"] == "Jane Doe"
    name_prov = res.provenance["name"]
    assert isinstance(name_prov, CellProvenance)
    assert name_prov.source == "web"
    assert name_prov.source_row_id == 1
    assert name_prov.strategy == "most_complete"
    # email only present on the CRM row -> sole non-null survives, from crm/row 0.
    assert res.values["email"] == "jane@x.com"
    assert res.provenance["email"].source == "crm"
    assert res.provenance["email"].source_row_id == 0


def test_provenance_excludes_internal_and_meta_columns():
    res = build_golden_with_provenance(
        _df(), [0, 1], timestamp_col="updated_at",
    )
    assert "__row_id__" not in res.values
    assert "__source__" not in res.values
    # timestamp_col is consumed for provenance, not emitted as a golden cell.
    assert "updated_at" not in res.values


def test_provenance_timestamp_tracked_when_column_given():
    res = build_golden_with_provenance(
        _df(), [0, 1], timestamp_col="updated_at",
    )
    # name winner is row 1 (web) -> its timestamp rides along.
    assert res.provenance["name"].timestamp == "2024-06-01"


def test_provenance_source_record_id_with_pk_col():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["crm", "web"],
        "cust_pk": ["C1", "W9"],
        "name": ["Jane", "Jane Doe"],
    })
    res = build_golden_with_provenance(df, [0, 1], source_pk_col="cust_pk")
    assert res.provenance["name"].source_record_id == "web:W9"


def test_provenance_field_strategy_override():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["crm", "web"],
        "name": ["Jane", "Jane Doe"],
    })
    # Force first_non_null on name -> keeps row 0's "Jane".
    res = build_golden_with_provenance(
        df, [0, 1], field_strategies={"name": "first_non_null"},
    )
    assert res.values["name"] == "Jane"
    assert res.provenance["name"].source_row_id == 0
    assert res.provenance["name"].strategy == "first_non_null"


def test_provenance_most_recent_uses_timestamp():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["crm", "web"],
        "phone": ["111", "222"],
        "ts": ["2020-01-01", "2024-01-01"],
    })
    res = build_golden_with_provenance(
        df, [0, 1], field_strategies={"phone": "most_recent"}, timestamp_col="ts",
    )
    # most_recent -> the 2024 row (row 1) wins.
    assert res.values["phone"] == "222"
    assert res.provenance["phone"].source_row_id == 1


def test_provenance_most_recent_without_timestamp_falls_back():
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "__source__": ["crm", "web"],
        "phone": ["111", "2222"],
    })
    # most_recent with no timestamp_col -> graceful fallback to most_complete.
    res = build_golden_with_provenance(
        df, [0, 1], field_strategies={"phone": "most_recent"},
    )
    assert res.values["phone"] == "2222"  # longer wins under the fallback


def test_provenance_empty_members():
    res = build_golden_with_provenance(_df(), [])
    assert res.values == {}
    assert res.provenance == {}


def test_provenance_config_defaults():
    from goldenmatch.config.schemas import SurvivorshipConfig

    cfg = SurvivorshipConfig(
        field_strategies={"name": "first_non_null"},
        timestamp_column="updated_at",
    )
    res = build_golden_with_provenance(_df(), [0, 1], config=cfg)
    assert res.values["name"] == "Jane"  # first_non_null from config
    assert res.provenance["name"].timestamp == "2020-01-01"


def test_as_record_drops_provenance():
    res = build_golden_with_provenance(_df(), [0, 1])
    rec = res.as_record()
    assert set(rec.keys()) == set(res.values.keys())
    assert "name" in rec


# ── Survivorship learning from FIELD_CORRECT corrections ────────────────────


def _field_correction(field_name, original, corrected, trust=1.0):
    return Correction(
        id=f"{field_name}:{original}:{corrected}",
        id_a=0,
        id_b=0,
        decision="field_correct",
        source="manual",
        trust=trust,
        field_hash="h",
        record_hash="r",
        original_score=0.0,
        field_name=field_name,
        original_value=original,
        corrected_value=corrected,
    )


def test_learn_prefers_most_complete_when_steward_picks_longer():
    # Every correction replaces a short value with a longer one.
    corrections = [
        _field_correction("address1", "5 Main", "5 Main Street"),
        _field_correction("address1", "10 Elm", "10 Elm Avenue"),
        _field_correction("address1", "7 Oak", "7 Oak Boulevard"),
    ]
    recs = learn_field_survivorship(corrections)
    assert "address1" in recs
    rec = recs["address1"]
    assert rec.best_strategy in ("most_complete", "longest_value")
    assert rec.agreement == 1.0
    assert rec.support == 3
    # first_non_null keeps the (loser) original -> never reproduces the winner.
    assert rec.per_strategy["first_non_null"] == 0.0


def test_learned_field_strategies_thresholds():
    corrections = [
        _field_correction("city", "NY", "New York"),
        _field_correction("city", "LA", "Los Angeles"),
        _field_correction("city", "SF", "San Francisco"),
    ]
    recs = learn_field_survivorship(corrections)
    learned = learned_field_strategies(recs, min_support=3, min_agreement=0.6)
    assert learned.get("city") in ("most_complete", "longest_value")
    # Raising support above the evidence drops it.
    assert learned_field_strategies(recs, min_support=10) == {}


def test_learn_ignores_non_field_corrections_and_noops():
    corrections = [
        # pair-level correction -> ignored.
        Correction(
            id="p", id_a=1, id_b=2, decision="approve", source="manual",
            trust=1.0, field_hash="h", record_hash="r", original_score=0.9,
        ),
        # field correction where nothing changed -> ignored.
        _field_correction("name", "Jane", "Jane"),
    ]
    assert learn_field_survivorship(corrections) == {}


def test_learn_trust_weighting():
    # High-trust correction prefers longer; a low-trust one disagrees.
    corrections = [
        _field_correction("name", "Jo", "Joseph", trust=1.0),
        _field_correction("name", "Jo", "Joseph", trust=1.0),
        _field_correction("name", "Joseph", "Jo", trust=0.5),  # picks shorter
    ]
    recs = learn_field_survivorship(corrections)
    rec = recs["name"]
    # most_complete reproduces the two high-trust (longer) winners but not the
    # low-trust shorter one: agreement = 2.0 / 2.5 = 0.8.
    assert rec.per_strategy["most_complete"] == 0.8
    assert rec.best_strategy in ("most_complete", "longest_value")
