import datetime as dt
from decimal import Decimal

import polars as pl
from goldenmatch.core._hashing import record_fingerprint
from goldenmatch.identity.fingerprint_batch import _canonical_payload, batch_fingerprints


def _perrow(row: dict):
    payload = {k: v for k, v in row.items() if not k.startswith("__")}
    try:
        return record_fingerprint(_canonical_payload(payload))
    except (TypeError, ValueError):
        return None


def _adversarial_df() -> pl.DataFrame:
    # Batchable + ROW-level-mask cases only (NO column-level-fallback dtypes here --
    # a single bytes/Decimal/Duration column would force the WHOLE frame per-row).
    return pl.DataFrame({
        "s": ["alice", "bob", None, "x"],
        "i64": [1, 2, 3, 4],
        "f_finite": [1.5, 2.0, 3.25, 0.1],
        "f_mixed": [1.5, float("nan"), float("inf"), -2.0],   # row-level mask
        "b": [True, False, None, True],
        "d": [dt.date(2020, 1, 2), dt.date(1999, 12, 31), None, dt.date(2000, 2, 29)],
        "dt_us": [dt.datetime(2020, 1, 2, 3, 4, 5, 123000), dt.datetime(2020, 1, 2, 3, 4, 5, 500000),
                  dt.datetime(2020, 1, 2, 3, 4, 5, 0), dt.datetime(2020, 1, 2, 3, 4, 5, 123456)],
        "i32": pl.Series([10, 20, 30, 40], dtype=pl.Int32),
        "f32": pl.Series([0.1, 0.2, 0.3, 0.4], dtype=pl.Float32),
        "allnull": pl.Series([None, None, None, None]),       # bare Null dtype
    })


def test_batch_fingerprints_parity():
    df = _adversarial_df()
    assert batch_fingerprints(df) == [_perrow(r) for r in df.to_dicts()]


def _assert_parity(df: pl.DataFrame):
    got = batch_fingerprints(df)
    want = [_perrow(r) for r in df.to_dicts()]
    assert got == want, f"\n got={got}\nwant={want}"


def test_parity_duration_column():
    df = pl.DataFrame({
        "s": ["a", "b"],
        "dur": pl.Series(
            [dt.timedelta(seconds=1), dt.timedelta(days=2, seconds=3)],
            dtype=pl.Duration("us"),
        ),
    })
    _assert_parity(df)


def test_parity_time_with_micros_column():
    df = pl.DataFrame({
        "s": ["a", "b"],
        "t": pl.Series([dt.time(1, 2, 3, 123456), dt.time(23, 59, 59, 1)], dtype=pl.Time),
    })
    _assert_parity(df)


def test_parity_tz_aware_datetime_column():
    df = pl.DataFrame({
        "s": ["a", "b"],
        "dt_tz": pl.Series(
            [dt.datetime(2020, 1, 2, 3, 4, 5), dt.datetime(2021, 6, 7, 8, 9, 10)],
            dtype=pl.Datetime("us", time_zone="UTC"),
        ),
    })
    _assert_parity(df)


def test_parity_ms_unit_datetime_column():
    df = pl.DataFrame({
        "s": ["a", "b"],
        "dt_ms": pl.Series(
            [dt.datetime(2020, 1, 2, 3, 4, 5, 123000), dt.datetime(2021, 6, 7, 8, 9, 10)],
            dtype=pl.Datetime("ms"),
        ),
    })
    _assert_parity(df)


def test_parity_bytes_column_drives_full_fallback():
    df = pl.DataFrame({
        "s": ["a", "b"],
        "raw": pl.Series([b"\x00\x01", b"hello"], dtype=pl.Binary),
    })
    # bytes column => canonicalize_records_df returns (None, [True]*height).
    from goldenmatch.identity.fingerprint_batch import canonicalize_records_df
    batch_df, mask = canonicalize_records_df(df)
    assert batch_df is None
    assert mask == [True, True]
    _assert_parity(df)


def test_parity_decimal_column():
    df = pl.DataFrame({
        "s": ["a", "b"],
        "dec": pl.Series([Decimal("1.50"), Decimal("2.25")], dtype=pl.Decimal(scale=2)),
    })
    _assert_parity(df)


def test_parity_uint64_overflow_column():
    df = pl.DataFrame({
        "s": ["a", "b", "c"],
        "u": pl.Series([1, 2**63, 2**63 + 7], dtype=pl.UInt64),  # row 2,3 overflow Int64
    })
    _assert_parity(df)


def test_batch_fingerprints_parity_off_native(monkeypatch):
    # GOLDENMATCH_NATIVE=0 forces the pure-Python / dict-fallback wrapper path.
    # native_enabled reads os.environ per call so the monkeypatch takes effect.
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    df = _adversarial_df()
    assert batch_fingerprints(df) == [_perrow(r) for r in df.to_dicts()]
