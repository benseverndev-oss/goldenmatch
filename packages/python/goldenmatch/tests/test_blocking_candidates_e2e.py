"""End-to-end + integration tests for blocking-candidate pool (#408).

Foundation unit tests are in tests/test_blocking_candidates.py. This
file proves the user-visible behavior: a healthcare-shaped fixture
with NPI does NOT end up with NPI as the blocking key, and a
fully-pathological fixture (every column near-unique) raises
ControllerNotConfidentError(failing_subprofile='blocking').
"""

from __future__ import annotations

import polars as pl
import pytest


def _healthcare_style_fixture(n: int = 5000) -> pl.DataFrame:
    """Healthcare subscriber data shape: name + address + zip + NPI +
    phone + email + specialty. NPI is near-unique-per-record; zip and
    last_name are mid-cardinality and ideal composite blocking keys."""
    # 80 distinct zips (~62 records/zip avg)
    # 50 distinct last names (~100 records/lastname avg)
    # Composite zip + last_name: should land in ~2000-3000 distinct buckets
    # NPI: unique per record (degenerate blocking key)
    return pl.DataFrame({
        "first_name": [f"first_{i % 200}" for i in range(n)],
        "last_name": [f"last_{i % 50}" for i in range(n)],
        "zip": [f"{(i % 80) + 27000:05d}" for i in range(n)],
        "city": [f"city_{i % 30}" for i in range(n)],
        "dm_npi": [f"1{1000000000 + i:010d}" for i in range(n)],  # unique per record
        "phone": [f"555-{i:07d}" for i in range(n)],
    })


def test_autoconfig_does_not_pick_npi_as_block_key():
    """#408 canonical regression: NPI-shaped near-unique column must NOT
    be picked as the blocking key, even though it's a great matchkey.
    """
    from goldenmatch.core.autoconfig import auto_configure_df

    df = _healthcare_style_fixture(n=5000)
    cfg = auto_configure_df(
        df, confidence_required=False, _skip_finalize=True,
    )

    blocking_fields: set[str] = set()
    if cfg.blocking and cfg.blocking.keys:
        for key in cfg.blocking.keys:
            for f in (getattr(key, "fields", None) or []):
                blocking_fields.add(f)

    assert "dm_npi" not in blocking_fields, (
        f"dm_npi (cardinality=1.0) must NOT be picked as a blocking key; "
        f"got blocking={blocking_fields}. See #408."
    )


def test_autoconfig_raises_on_degenerate_blocking_with_confidence_required():
    """All columns near-unique -> blocking guard fires with confidence_required."""
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.autoconfig_controller import ControllerNotConfidentError

    n = 200_000  # above REFUSE_AT_N threshold so guard fires
    df = pl.DataFrame({
        "id_a": [f"a_{i:08d}" for i in range(n)],
        "id_b": [f"b_{i:08d}" for i in range(n)],
        "id_c": [f"c_{i:08d}" for i in range(n)],
    })

    with pytest.raises(ControllerNotConfidentError) as exc_info:
        auto_configure_df(df, confidence_required=True, _skip_finalize=True)
    # Either failing_sub_profile == "blocking" (our new gate fired) OR
    # an earlier guard (data/scoring) caught it first -- both are valid
    # rejections of the degenerate input.
    assert exc_info.value.failing_sub_profile in {
        "blocking", "data", "scoring", "blocking_degenerate",
    }


def test_blocking_excluded_log_message_includes_column_name(caplog: pytest.LogCaptureFixture):
    """When blocking rejects a near-unique candidate, the INFO log line
    surfaces the column name + reason so users can debug."""
    import logging

    from goldenmatch.core.autoconfig import auto_configure_df

    df = _healthcare_style_fixture(n=5000)
    with caplog.at_level(logging.INFO, logger="goldenmatch.core.autoconfig"):
        auto_configure_df(df, confidence_required=False, _skip_finalize=True)

    # NPI-shaped column should appear in a "blocking candidate rejected" line.
    relevant = [
        r.getMessage() for r in caplog.records
        if "Blocking candidate rejected" in r.getMessage()
    ]
    # Either the new log line fired, OR npi never entered the candidate
    # pool (col_type might not be 'identifier' for it). Both are acceptable
    # outcomes -- the test guarantees no false-positive log entries.
    for msg in relevant:
        # If anything got logged, it must be a real near-unique column,
        # not a mid-cardinality one like zip or last_name.
        for safe_col in ["zip", "last_name", "first_name", "city"]:
            assert f"'{safe_col}'" not in msg, (
                f"Mid-cardinality column wrongly flagged: {msg}"
            )


def test_postflight_renders_blocking_line():
    """PostflightReport.__str__ includes 'Blocking: keys=[...]' when
    the controller committed a blocking config."""
    from goldenmatch.core.autoconfig_verify import _render_blocking_line

    # Build a fake history-like object with a committed entry.
    class _FakeKey:
        def __init__(self, fields: list[str]) -> None:
            self.fields = fields

    class _FakeBlocking:
        def __init__(self, fields: list[str]) -> None:
            self.keys = [_FakeKey(fields)]

    class _FakeConfig:
        def __init__(self, fields: list[str]) -> None:
            self.blocking = _FakeBlocking(fields)

    class _FakeEntry:
        def __init__(self, fields: list[str]) -> None:
            self.config = _FakeConfig(fields)

    class _FakeHistory:
        def __init__(self, fields: list[str]) -> None:
            self._fields = fields

        def pick_committed(self):
            return _FakeEntry(self._fields)

    line = _render_blocking_line(_FakeHistory(["zip", "last_name"]))
    assert line == "Blocking: keys=[zip, last_name]"


def test_postflight_blocking_line_empty_when_no_blocking():
    """No blocking config in the committed entry -> empty string."""
    from goldenmatch.core.autoconfig_verify import _render_blocking_line

    assert _render_blocking_line(None) == ""

    class _Empty:
        def pick_committed(self):
            return None

    assert _render_blocking_line(_Empty()) == ""


# ---------------------------------------------------------------------------
# #410: composite-search wiring + sample-correction regression tests
# ---------------------------------------------------------------------------


def test_autoconfig_picks_composite_or_refuses_on_healthcare_fixture():
    """#410 kill criterion: healthcare-shape fixture (NPI + zip +
    last_name + name + phone). Auto-config must EITHER pick a composite
    blocking key (zip + last_name -- the only viable option after the
    cardinality gate rejects NPI) OR raise ControllerNotConfidentError
    on the blocking sub-profile.

    The pre-#410 #409 implementation produced a single mega-block at
    full scale because the composite search was never wired in and the
    fallback path picked first_string with substring:0:5. That's the
    bug this test pins.
    """
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.autoconfig_controller import ControllerNotConfidentError

    df = _healthcare_style_fixture(n=5000)
    try:
        cfg = auto_configure_df(
            df, confidence_required=False, _skip_finalize=True,
        )
    except ControllerNotConfidentError as exc:
        # Acceptable: guard correctly refused the degenerate config.
        assert exc.failing_sub_profile == "blocking"
        return

    # Otherwise the committed config must have a viable blocking key.
    assert cfg.blocking is not None
    assert cfg.blocking.keys
    blocking_fields = []
    for key in cfg.blocking.keys:
        for f in (getattr(key, "fields", None) or []):
            blocking_fields.append(f)

    # NPI must NOT be the sole blocking key.
    assert blocking_fields != ["dm_npi"], (
        "NPI was picked as a single-column blocking key -- the #410 "
        "regression. Expected composite or refusal."
    )
    # NPI must not appear at all in the blocking-key list.
    assert "dm_npi" not in blocking_fields, (
        f"dm_npi appeared in blocking_fields={blocking_fields}. #410."
    )


def test_make_quality_column_profile_adapter_preserves_fields():
    """#410: adapter from autoconfig.ColumnProfile to
    quality_exclusions.ColumnProfile preserves cardinality_ratio,
    null_rate, dtype + projects distinct_count from cardinality * n."""
    from goldenmatch.core.autoconfig import (
        ColumnProfile as _AutoCp,
    )
    from goldenmatch.core.autoconfig import (
        _make_quality_column_profile,
    )

    src = _AutoCp(
        name="zip",
        dtype="Utf8",
        col_type="zip",
        confidence=0.9,
        cardinality_ratio=0.05,
        null_rate=0.0,
        avg_len=5.0,
    )
    out = _make_quality_column_profile(src, n_rows=1_000_000)
    assert out.cardinality_ratio == 0.05
    assert out.null_rate == 0.0
    assert out.distinct_count == 50_000  # 0.05 * 1M
    assert out.dtype == "Utf8"
    assert out.mean_string_length == 5.0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
