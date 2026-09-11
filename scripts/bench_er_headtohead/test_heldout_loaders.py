"""Held-out loaders for FS link-cut routing P3 (pyarrow only, fetched data)."""

from __future__ import annotations

import inspect

import pytest

from scripts.bench_er_headtohead import datasets as D

HELDOUT_NAMES = (
    "abt_buy",
    "walmart_amazon",
    "itunes_amazon",
    "fodors_zagats",
    "febrl1",
    "febrl2",
    "synth_person_c05_d10",
    "synth_person_c05_d40",
    "synth_person_c20_d10",
    "synth_person_c20_d40",
    "synth_biblio_c05_d10",
    "synth_biblio_c05_d40",
    "synth_biblio_c20_d10",
    "synth_biblio_c20_d40",
)


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _cluster_of(truth) -> dict:
    return dict(zip(truth.column("record_id").to_pylist(), truth.column("cluster_id").to_pylist()))


def test_every_heldout_name_is_registered():
    missing = [n for n in HELDOUT_NAMES if n not in D._LOADERS]
    assert not missing, missing


def test_abt_buy_unions_both_sources_and_links_the_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _write(
        tmp_path / "Abt-Buy" / "Abt.csv",
        "id,name,description,price\n1,sony tv,big tv,$10\n2,lg radio,small,$5\n",
    )
    _write(
        tmp_path / "Abt-Buy" / "Buy.csv",
        "id,name,description,manufacturer,price\n7,Sony TV 40,tv,Sony,10\n8,Bose,spk,Bose,99\n",
    )
    _write(tmp_path / "Abt-Buy" / "abt_buy_perfectMapping.csv", "idAbt,idBuy\n1,7\n")
    records, truth = D.load_dataset("abt_buy")
    assert records.column("record_id").to_pylist() == ["abt:1", "abt:2", "buy:7", "buy:8"]
    cid = _cluster_of(truth)
    assert cid["abt:1"] == cid["buy:7"]
    assert len({cid["abt:1"], cid["abt:2"], cid["buy:8"]}) == 3


def test_magellan_links_only_label_one_pairs_across_all_splits(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    base = tmp_path / "Magellan" / "Walmart-Amazon"
    _write(base / "tableA.csv", "id,title\n0,apple ipod\n1,bose speaker\n")
    _write(base / "tableB.csv", "id,title\n0,ipod apple 8gb\n1,sony tv\n")
    _write(base / "train.csv", "ltable_id,rtable_id,label\n1,1,0\n")
    _write(base / "valid.csv", "ltable_id,rtable_id,label\n")
    _write(base / "test.csv", "ltable_id,rtable_id,label\n0,0,1\n")
    records, truth = D.load_dataset("walmart_amazon")
    assert records.column("record_id").to_pylist() == ["a:0", "a:1", "b:0", "b:1"]
    cid = _cluster_of(truth)
    assert cid["a:0"] == cid["b:0"]
    assert cid["a:1"] != cid["b:1"]


def test_febrl_raw_trims_the_space_after_commas_and_groups_by_entity(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _write(
        tmp_path / "FEBRL" / "dataset1.csv",
        "rec_id, given_name, surname\nrec-1-org, ann, lee\nrec-1-dup-0, anne, lee\nrec-2-org, bob, ray\n",
    )
    records, truth = D.load_dataset("febrl1")
    assert records.column_names == ["record_id", "given_name", "surname"]
    assert records.column("given_name").to_pylist() == ["ann", "anne", "bob"]
    cid = _cluster_of(truth)
    assert cid["rec-1-org"] == cid["rec-1-dup-0"] != cid["rec-2-org"]


def test_febrl_raw_rejects_an_unexpected_record_id(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _write(tmp_path / "FEBRL" / "dataset2.csv", "rec_id, surname\nperson-9, lee\n")
    with pytest.raises(ValueError, match="rec_id"):
        D.load_dataset("febrl2")


@pytest.mark.parametrize("name", ["abt_buy", "itunes_amazon", "fodors_zagats", "febrl1"])
def test_absent_data_is_unavailable_not_a_crash(tmp_path, monkeypatch, name):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    with pytest.raises(D.DatasetUnavailable):
        D.load_dataset(name)


def test_synthetic_heldout_variant_uses_its_own_seed_and_knobs(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_BENCH_SYNTHETIC_ROWS", "300")
    assert D._SYNTH_HELDOUT["synth_biblio_c20_d40"] == ("biblio", 2.0, 0.40)
    records, truth = D.load_dataset("synth_person_c20_d40")
    # The generator has a pre-existing final-batch clip off-by-one (rows+1 for some
    # (rows, seed)); fixing it would move committed fixtures, so it is tracked separately.
    assert records.num_rows == truth.num_rows
    assert records.num_rows in (300, 301)
    assert len(set(truth.column("cluster_id").to_pylist())) < records.num_rows


def test_design_synthetic_defaults_are_unchanged():
    params = inspect.signature(D._synthetic).parameters
    assert params["dupe_rate"].default == 0.20
    assert params["seed"].default == 42
    assert params["corruption"].default == 1.0
    assert D._HELDOUT_SYNTH_SEED != params["seed"].default


def _fake_magellan(root, subdir: str, *, valid: str = "") -> None:
    base = root / "Magellan" / subdir
    _write(base / "tableA.csv", "id,title\n0,apple ipod\n1,bose speaker\n")
    _write(base / "tableB.csv", "id,title\n0,ipod apple 8gb\n1,sony tv\n")
    _write(base / "train.csv", "ltable_id,rtable_id,label\n1,1,0\n")
    _write(base / "valid.csv", "ltable_id,rtable_id,label\n" + valid)
    _write(base / "test.csv", "ltable_id,rtable_id,label\n0,0,1\n")


def test_magellan_subdirs_name_every_magellan_loader(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    assert set(D.MAGELLAN_SUBDIRS) == {"walmart_amazon", "itunes_amazon", "fodors_zagats"}
    for name, subdir in D.MAGELLAN_SUBDIRS.items():
        _fake_magellan(tmp_path, subdir)
        records, _truth = D.load_dataset(name)
        assert records.num_rows == 4


def test_magellan_labels_keep_every_labelled_pair_in_file_order(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _fake_magellan(tmp_path, "Walmart-Amazon", valid="0,1,0\n")
    assert D.magellan_labels("Walmart-Amazon") == [
        ("a:1", "b:1", False),
        ("a:0", "b:1", False),
        ("a:0", "b:0", True),
    ]


def test_labelled_pairs_maps_labels_to_frame_row_indices(tmp_path, monkeypatch):
    from scripts.autoconfig_quality import datasets as Q

    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    _fake_magellan(tmp_path, "Walmart-Amazon", valid="0,0,0\n")
    # Rows follow records order: a:0, a:1, b:0, b:1. (a:0, b:0) is labelled 0 in valid and 1
    # in test, so it counts as a match, as the truth builder counts it.
    assert Q.labelled_pairs("walmart_amazon") == {(1, 3): False, (0, 2): True}
    assert Q.labelled_pairs("febrl3") is None


def test_labelled_pairs_is_none_when_the_data_is_absent(tmp_path, monkeypatch):
    from scripts.autoconfig_quality import datasets as Q

    monkeypatch.setattr(D, "DATASETS_DIR", tmp_path)
    assert Q.labelled_pairs("itunes_amazon") is None
