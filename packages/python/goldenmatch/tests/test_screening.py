"""One-to-many screening against a watchlist (#1095).

Screen a single query record (an applicant) against a reference list (a
sanctions / PEP / blocklist) and get scored, explained hits.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.screening import (
    ScreeningResult,
    screen_record,
    screen_records,
)

# A weighted name matchkey -- the threshold-bearing type screen_record screens on.
NAME_MK = MatchkeyConfig(
    name="name_mk",
    type="weighted",
    threshold=0.85,
    fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
)


def _watchlist():
    # E1 has two aliases (AKA); E2 is a separate entity.
    return pl.DataFrame([
        {"entity_id": "E1", "name": "Robert Smith"},
        {"entity_id": "E1", "name": "Robert Smyth"},
        {"entity_id": "E2", "name": "Alice Jones"},
    ])


def test_hit_returns_scored_explained_match():
    res = screen_record({"name": "Robert Smith"}, _watchlist(), NAME_MK)
    assert isinstance(res, ScreeningResult)
    assert res.is_hit
    assert res.screened == 3
    top = res.top
    assert top is not None
    assert top.candidate["name"] == "Robert Smith"
    assert top.score == pytest.approx(1.0)
    assert top.matchkey == "name_mk"
    # Reason breakdown names the field that agreed.
    name_reason = next(r for r in top.reasons if r.field == "name")
    assert name_reason.agreed
    assert name_reason.score == pytest.approx(1.0)


def test_no_hit_when_nothing_resembles():
    res = screen_record({"name": "Zzyzx Nobody"}, _watchlist(), NAME_MK)
    assert not res.is_hit
    assert res.hits == []
    assert res.top is None


def test_aka_rows_collapse_to_one_entity():
    # Both E1 alias rows match "Robert Smith" (1.0 and ~0.96 >= 0.85).
    grouped = screen_record(
        {"name": "Robert Smith"}, _watchlist(), NAME_MK,
        entity_id_column="entity_id",
    )
    assert len(grouped.hits) == 1
    assert grouped.hits[0].entity_id == "E1"
    # Without grouping, the same query reports BOTH alias rows.
    ungrouped = screen_record({"name": "Robert Smith"}, _watchlist(), NAME_MK)
    assert len(ungrouped.hits) == 2


def test_threshold_floor_filters_weaker_hits():
    # The base matchkey threshold is 0.85; an extra floor of 0.99 keeps only the
    # exact alias.
    res = screen_record(
        {"name": "Robert Smith"}, _watchlist(), NAME_MK, threshold=0.99,
    )
    assert len(res.hits) == 1
    assert res.hits[0].score == pytest.approx(1.0)


def test_limit_caps_returned_hits():
    res = screen_record({"name": "Robert Smith"}, _watchlist(), NAME_MK, limit=1)
    assert len(res.hits) == 1


def test_accepts_single_matchkey_or_list():
    one = screen_record({"name": "Alice Jones"}, _watchlist(), NAME_MK)
    many = screen_record({"name": "Alice Jones"}, _watchlist(), [NAME_MK])
    assert one.top.entity_id == many.top.entity_id == 2  # row_id, no entity col


def test_watchlist_without_row_id_is_handled():
    wl = _watchlist()
    assert "__row_id__" not in wl.columns
    res = screen_record({"name": "Alice Jones"}, wl, NAME_MK)
    assert res.is_hit
    # The caller's frame is untouched (row id added on a copy).
    assert "__row_id__" not in wl.columns


def test_batch_screens_each_record_positionally():
    results = screen_records(
        [{"name": "Robert Smith"}, {"name": "Alice Jones"}, {"name": "Nobody"}],
        _watchlist(), NAME_MK, entity_id_column="entity_id",
    )
    assert len(results) == 3
    assert results[0].top.entity_id == "E1"
    assert results[1].top.entity_id == "E2"
    assert not results[2].is_hit


def test_result_as_dict_is_serializable():
    import json

    res = screen_record(
        {"name": "Robert Smith"}, _watchlist(), NAME_MK, entity_id_column="entity_id",
    )
    blob = json.dumps(res.as_dict())
    assert "hits" in blob and "reasons" in blob
