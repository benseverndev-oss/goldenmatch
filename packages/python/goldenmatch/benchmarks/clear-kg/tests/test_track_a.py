"""End-to-end Track A: exact < relaxed < er_aware (alias penalty, then homograph
mis-credit), and F1 tracks extraction quality (lossy < pattern). Pinned."""
from extract_data import generate_extraction_corpus
from run_track_a import MODES, run


def test_canonicalization_modes_are_monotone_and_er_aware_is_correct():
    ds = generate_extraction_corpus(seed=0)
    res = run(ds)
    p = res["pattern"]
    # exact under-counts (alias penalty) < relaxed (recovers aliases) < er_aware
    assert p["exact"]["f1"] < p["relaxed"]["f1"] < p["er_aware"]["f1"]
    # the relaxed->er_aware gap is specifically the homograph mis-credit
    assert p["relaxed"]["homograph_recall"] < p["er_aware"]["homograph_recall"]
    assert p["er_aware"]["homograph_recall"] == 1.0
    # a faithful extractor + ER-aware canonicalization recovers the whole KG
    assert p["er_aware"]["f1"] == 1.0


def test_f1_tracks_extraction_quality():
    ds = generate_extraction_corpus(seed=0)
    res = run(ds)
    # dropping + corrupting triples lowers F1 under every mode (table stakes)
    for m in MODES:
        assert res["lossy"][m]["f1"] <= res["pattern"][m]["f1"], m
    assert res["lossy"]["er_aware"]["f1"] < res["pattern"]["er_aware"]["f1"]


def test_modes_cover_the_convention():
    assert MODES == ("exact", "relaxed", "er_aware")
