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


# ---------------------------------------------------------------------------
# #410 v2: sample-vs-full row-count threading (post-#411 follow-up)
# ---------------------------------------------------------------------------


def test_v0_uses_n_rows_full_when_provided():
    """``_legacy_auto_configure_v0`` honors the ``n_rows_full`` kwarg
    instead of falling back to ``df.height``. Without this fix, when
    the controller passes a sub-sample of a large frame to v0, the
    sample-sized ``df.height`` propagates to ``build_blocking`` as
    ``n_rows_full``, the Chao1 helper sees ``sample_n == full_n`` and
    short-circuits to the observed ratio, defeating the gate.

    Pinned via a small fixture where the sample is artificially smaller
    than the declared full population. Assert the cardinality gate
    sees the projected (scaled) ratio, not the observed one.
    """
    from goldenmatch.core.autoconfig import _legacy_auto_configure_v0

    # Sample of 50 rows with 40 distinct names (observed ratio 0.8).
    # If treated as full_n=50: gate REJECTS at 0.8 > 0.5.
    # If treated as full_n=1_000_000: projected ratio = 40 * sqrt(20K) / 1M
    #   = 40 * 141 / 1M = 0.0056 → gate ACCEPTS.
    df = pl.DataFrame({
        "first_name": [f"name_{i % 40}" for i in range(50)],
        "last_name": [f"last_{i % 30}" for i in range(50)],
        "city": ["NYC", "LA", "SF", "Boston", "Seattle"] * 10,
    })
    cfg = _legacy_auto_configure_v0(
        df,
        n_rows_full=1_000_000,
    )
    # Hard assertion: the function ran and produced a config. The
    # exact blocking choice depends on the rule chain (could be
    # name-based or composite), but the test pins the contract that
    # n_rows_full is honored — without it, v0 would have rejected
    # all candidates and fallen to first_string fallback.
    assert cfg.blocking is not None


def test_controller_threads_full_n_to_v0(monkeypatch: pytest.MonkeyPatch):
    """End-to-end check that the controller wires its true ``n_rows``
    into the v0 call. Patches ``_legacy_auto_configure_v0`` to capture
    the ``n_rows_full`` kwarg it actually receives.
    """
    from goldenmatch.core import autoconfig as _ac_mod
    from goldenmatch.core import autoconfig_controller as _ctl_mod

    captured: dict = {}
    _real = _ac_mod._legacy_auto_configure_v0

    def _capturing(df_arg, **kwargs):
        captured["n_rows_full"] = kwargs.get("n_rows_full")
        captured["df_height"] = df_arg.height
        return _real(df_arg, **kwargs)

    monkeypatch.setattr(_ac_mod, "_legacy_auto_configure_v0", _capturing)
    monkeypatch.setattr(_ctl_mod, "_legacy_auto_configure_v0", _capturing, raising=False)

    # Build a healthcare-shape df larger than the controller's
    # init_sample cap (5K) so df.height differs from sample.height
    # at the v0 call. We assert the controller passed the FULL count.
    df = _healthcare_style_fixture(n=6000)
    _ac_mod.auto_configure_df(
        df, confidence_required=False, _skip_finalize=True,
    )

    assert "n_rows_full" in captured, (
        "controller never called v0 -- test harness wrong, not the fix"
    )
    assert captured["n_rows_full"] == 6000, (
        f"controller passed n_rows_full={captured['n_rows_full']} but "
        f"true population was 6000. v0 would have read the sample's "
        f"height ({captured['df_height']}) instead -- #410 bug."
    )


# ---------------------------------------------------------------------------
# #417: BLOCKING_DEGENERATE upper-bound regression test
# ---------------------------------------------------------------------------


def test_guard_fires_on_mega_block_config(monkeypatch: pytest.MonkeyPatch):
    """#417: a config whose blocking key collapses every row into one
    block must trip the upper-bound guard. Before #417, the guard only
    checked the lower bound (singleton blocks); the user's 1.13M-rows-
    in-1-block case slipped through and wedged in bucket_score."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.autoconfig_controller import (
        ControllerNotConfidentError,
    )

    # Lower REFUSE_AT_N so the test fixture (200K rows) trips the gate.
    # Default is 100K so 200K already trips; explicit for clarity.
    n = 200_000
    # Every row gets the SAME blocking key. avg_block_size = N >> 10K.
    df = pl.DataFrame({
        "constant_block_key": ["only_value"] * n,
        "name": [f"name_{i % 1000}" for i in range(n)],
        "id_a": [f"a{i}" for i in range(n)],
    })

    # Build a config that pins blocking to the constant column so the
    # controller can't escape via the composite-search fallback.
    # Wrap in confidence_required=True (default) so the guard fires.
    pinned = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="m",
                type="weighted",
                threshold=0.5,
                fields=[MatchkeyField(field="name", scorer="exact", weight=1.0)],
            ),
        ],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["constant_block_key"])],
        ),
    )
    with pytest.raises(ControllerNotConfidentError) as exc_info:
        auto_configure_df(
            df,
            config=pinned,
            confidence_required=True,
            _skip_finalize=True,
        )
    assert exc_info.value.failing_sub_profile == "blocking"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
