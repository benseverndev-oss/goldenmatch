"""Wave 1 detect dispatch / pure-path tests (box-safe; INFERMAP_NATIVE=0)."""

import polars as pl
from infermap.detect import _detect_core_pure, detect_domain_detailed


def test_core_pure_confident():
    r = _detect_core_pure(
        ["provider_npi", "first_name"],
        [("health", ["provider npi"]), ("fin", ["iban"])],
        0.3,
    )
    assert r == ("health", 0.5, "fin", 0.0, "confident")


def test_core_pure_tie():
    r = _detect_core_pure(["a", "b"], [("x", ["a"]), ("y", ["b"])], 0.3)
    assert r[0] is None and r[4] == "tie"


def test_core_pure_below_min():
    r = _detect_core_pure(["a", "b", "c", "d"], [("h", ["a"])], 0.3)
    assert r[0] is None and r[4] == "below_min_score"


def test_core_pure_no_data_empty_cols():
    assert _detect_core_pure([], [("h", ["x"])], 0.3)[4] == "no_data"


def test_core_pure_no_data_empty_hints():
    assert _detect_core_pure(["a"], [("h", [])], 0.3)[4] == "no_data"


def test_detect_domain_detailed_end_to_end():
    df = pl.DataFrame({"provider_npi": [1], "patient_id": [2]})
    res = detect_domain_detailed(df)
    # deterministic decision over the real packs, not no_data
    assert res.reason in {"confident", "tie", "below_min_score"}
