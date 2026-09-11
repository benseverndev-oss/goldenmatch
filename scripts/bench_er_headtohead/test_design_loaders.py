"""DESIGN-corpus loaders for FS link-cut routing P3 (widened set, 2026-09-11).

Real Leipzig clustering loaders (musicbrainz_20k, geo_settlements, affiliations)
plus the design synthetic variants. Same fake-file pattern as
``test_heldout_loaders.py``."""

from __future__ import annotations

import json

import pytest

from scripts.bench_er_headtohead import datasets as D

DESIGN_REAL_NAMES = ("musicbrainz_20k", "geo_settlements", "affiliations")

DESIGN_SYNTH_NAMES = (
    "synth_biblio_d02",
    "synth_person_d02",
    "synth_product_d02",
    "synth_product_d20",
)


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path, objs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(o) for o in objs) + "\n", encoding="utf-8")


def _cluster_of(truth) -> dict:
    return dict(zip(truth.column("record_id").to_pylist(), truth.column("cluster_id").to_pylist()))


def test_every_design_real_name_is_registered():
    missing = [n for n in DESIGN_REAL_NAMES if n not in D._LOADERS]
    assert not missing, missing


def test_every_design_synth_name_is_registered():
    missing = [n for n in DESIGN_SYNTH_NAMES if n not in D._LOADERS]
    assert not missing, missing


# ── musicbrainz_20k ─────────────────────────────────────────────────────────


def test_musicbrainz_drops_leakage_columns_and_links_by_cid(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _write(
        tmp_path / "Leipzig-Clustering" / "MusicBrainz" / "musicbrainz-20-A01.csv",
        "TID,CID,CTID,SourceID,id,number,title,length,artist,album,year,language\n"
        "1,10,1,2,MBox1,9,Song One,219,Artist A,Album A,1999,English\n"
        "2,10,1,4,MBox2,7,Song One (dup),null,Artist A,Album A,1999,English\n"
        "3,20,1,5,MBox3,10,Song Two,unk.,Artist B,Album B,2005,English\n",
    )
    records, truth = D.load_dataset("musicbrainz_20k")
    assert records.column("record_id").to_pylist() == ["mb:1", "mb:2", "mb:3"]
    for leaked in ("CID", "CTID", "SourceID", "id", "TID"):
        assert leaked not in records.column_names
    assert set(records.column_names) == {
        "record_id",
        "number",
        "title",
        "length",
        "artist",
        "album",
        "year",
        "language",
    }
    cid = _cluster_of(truth)
    assert cid["mb:1"] == cid["mb:2"]
    assert cid["mb:1"] != cid["mb:3"]


# ── geo_settlements ──────────────────────────────────────────────────────────


def test_geo_settlements_nulls_missing_latlon_joins_list_type_and_unions_clusters(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    base = tmp_path / "Leipzig-Clustering" / "GeoSettlements"
    _write_jsonl(
        base / "settlements.json",
        [
            {"data": {"label": "Petra (Jordan)", "lat": 30.3167, "lon": 35.4833}, "id": 0},
            # No lat/lon -> null.
            {"data": {"label": "Petra"}, "id": 1},
            # type as a list -> joined with "|".
            {
                "data": {
                    "label": "Split",
                    "lat": 43.5089,
                    "lon": 16.4392,
                    "type": ["city", "village"],
                },
                "id": 2,
            },
            {"data": {"label": "Dresden", "lat": 51.05, "lon": 13.74}, "id": 3},
        ],
    )
    _write_jsonl(
        base / "combinedSettlements(PerfectMatch).json",
        [
            # Cluster A: 0, 1.
            {"id": 0, "data": {"label": "petra", "clusteredVertices": [0, 1]}},
            # Cluster B shares vertex 1 with cluster A -> the two clusters merge.
            {"id": 2, "data": {"label": "split", "clusteredVertices": [1, 2]}},
        ],
    )
    records, truth = D.load_dataset("geo_settlements")
    by_id = dict(zip(records.column("record_id").to_pylist(), range(records.num_rows)))
    lat = records.column("lat").to_pylist()
    lon = records.column("lon").to_pylist()
    assert lat[by_id["geo:1"]] is None
    assert lon[by_id["geo:1"]] is None
    type_col = records.column("type").to_pylist()
    assert type_col[by_id["geo:2"]] == "city|village"
    assert "ontology" not in records.column_names

    cid = _cluster_of(truth)
    assert cid["geo:0"] == cid["geo:1"] == cid["geo:2"]
    assert cid["geo:3"] != cid["geo:0"]


# ── affiliations ─────────────────────────────────────────────────────────────


def test_affiliations_headerless_mapping_joins_transitively(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    base = tmp_path / "Leipzig-Clustering" / "Affiliations"
    _write(
        base / "affiliationstrings_ids.csv",
        '"id1","affil1"\n"1","IBM Almaden"\n"2","IBM Almaden Research Center"\n'
        '"3","IBM Research"\n',
    )
    _write(base / "affiliationstrings_mapping.csv", '"1","2"\n"2","3"\n')
    records, truth = D.load_dataset("affiliations")
    assert records.column("record_id").to_pylist() == ["aff:1", "aff:2", "aff:3"]
    assert records.column_names == ["record_id", "affiliation"]
    cid = _cluster_of(truth)
    assert cid["aff:1"] == cid["aff:2"] == cid["aff:3"]


# ── missing files ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", DESIGN_REAL_NAMES)
def test_design_real_loader_raises_dataset_unavailable_when_absent(tmp_path, monkeypatch, name):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    with pytest.raises(D.DatasetUnavailable):
        D.load_dataset(name)


# ── synthetic design variants ─────────────────────────────────────────────────


def test_synth_product_d02_loads_with_matched_row_counts(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_BENCH_SYNTHETIC_ROWS", "300")
    records, truth = D.load_dataset("synth_product_d02")
    assert records.num_rows == truth.num_rows
    assert records.num_rows in (300, 301)


def test_synth_design_spec_table_has_the_expected_entries():
    assert D._SYNTH_DESIGN["synth_product_d20"] == ("product", 1.0, 0.20)
    assert D._SYNTH_DESIGN["synth_product_d02"] == ("product", 1.0, 0.02)
    assert D._SYNTH_DESIGN["synth_biblio_d02"] == ("biblio", 1.0, 0.02)
    assert D._SYNTH_DESIGN["synth_person_d02"] == ("person", 1.0, 0.02)


def test_design_synth_seed_differs_from_heldout_and_default():
    assert D._DESIGN_SYNTH_SEED != D._HELDOUT_SYNTH_SEED
    assert D._DESIGN_SYNTH_SEED != 42


def test_synth_heldout_names_are_untouched():
    for name in D._SYNTH_HELDOUT:
        assert name in D._LOADERS
