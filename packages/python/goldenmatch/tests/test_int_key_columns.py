"""A non-string identifier column must not crash zero-config.

A CSV whose phone column is plain digits reads as int64. Auto-config's
strong-id blocking pass measured it through `filter_valid_key`, which ran a
UTF-8 trim on the column, so both `gm.dedupe(path)` and `goldenmatch dedupe`
died with `ArrowNotImplementedError: Function 'utf8_trim_whitespace' has no
kernel matching input types (int64)`.
"""

import csv
import math

import pyarrow as pa
from goldenmatch.core.frame import ArrowFrame


def _frames(table: pa.Table) -> list:
    """The Arrow frame always; its Polars twin too when polars is installed."""
    frames: list = [ArrowFrame(table)]
    try:
        import polars as pl
    except ImportError:
        return frames
    from goldenmatch.core.frame import PolarsFrame

    frames.append(PolarsFrame(pl.from_arrow(table)))
    return frames


def _col(frame, name):
    native = frame.native
    return native.column(name).to_pylist() if hasattr(native, "num_rows") else native[name].to_list()


def test_filter_valid_key_keeps_integer_keys():
    table = pa.table({"phone": pa.array([5552013344, None, 5558831200], type=pa.int64())})
    for frame in _frames(table):
        assert _col(frame.filter_valid_key("phone"), "phone") == [5552013344, 5558831200]


def test_filter_valid_key_drops_float_nan_like_the_string_sentinel():
    table = pa.table({"score": pa.array([1.5, float("nan"), None, 2.0], type=pa.float64())})
    for frame in _frames(table):
        kept = _col(frame.filter_valid_key("score"), "score")
        assert kept == [1.5, 2.0], type(frame).__name__
        assert not any(isinstance(v, float) and math.isnan(v) for v in kept)


def test_filter_valid_key_string_semantics_unchanged():
    table = pa.table({"k": ["a", " NaN ", "null", "", None, "None", "b"]})
    for frame in _frames(table):
        # "" is a real value (PR #390); sentinels and nulls drop.
        assert _col(frame.filter_valid_key("k"), "k") == ["a", "", "b"]


def test_zero_config_dedupe_with_an_integer_phone_column(tmp_path):
    import goldenmatch as gm

    rows = [
        ("Maria Gonzalez", "maria.gonzalez@example.com", 5552013344, "14 Oak Street"),
        ("Maria Gonzales", "mgonzalez@example.com", 5552013344, "14 Oak St"),
        ("James O'Neil", "jim.oneil@example.com", 5558831200, "902 Pine Avenue"),
        ("Jim O'Neill", "jim.oneil@example.com", 5558831200, "902 Pine Ave"),
        ("Priya Raman", "priya.raman@example.com", 5554107788, "3 Birch Road"),
        ("Priya Ramen", "p.raman@example.com", 5554107788, "3 Birch Rd"),
        ("Thomas Becker", "tbecker@example.com", 5556670192, "77 Elm Court"),
        ("Aisha Bello", "aisha.bello@example.com", 5553392471, "510 Cedar Lane"),
    ]
    path = tmp_path / "customers.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "email", "phone", "address"])
        w.writerows(rows)
    result = gm.dedupe(str(path))
    groups = {tuple(sorted(c["members"])) for c in result.clusters.values() if c["size"] > 1}
    assert groups == {(0, 1), (2, 3), (4, 5)}


def test_negative_evidence_scores_integer_values_instead_of_disabling_itself():
    """#2990: NE on an int64 column raised TypeError in the string scorer and was
    disabled for the rest of the process, silently skipping a penalty the
    committed config asks for."""
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField, NegativeEvidenceField
    from goldenmatch.core import scorer

    scorer._NE_BROKEN.clear()
    mk = MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.8,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(
                field="account_id", scorer="ensemble", transforms=[], threshold=0.4, penalty=0.3
            )
        ],
    )
    assert scorer._apply_negative_evidence(mk, {"account_id": (1003, 987654)}) == 0.3
    assert scorer._apply_negative_evidence(mk, {"account_id": (1003, 1003)}) == 0.0
    assert scorer._apply_negative_evidence(mk, {"account_id": (None, 987654)}) == 0.0
    assert ("ensemble", "account_id") not in scorer._NE_BROKEN
