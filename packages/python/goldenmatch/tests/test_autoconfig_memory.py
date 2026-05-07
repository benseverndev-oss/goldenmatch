"""Tests for AutoConfigMemory (Tier 4 cross-run memory)."""
import pytest
import polars as pl
from goldenmatch.core.autoconfig_memory import (
    AutoConfigMemory,
    profile_signature,
)
from goldenmatch.config.schemas import (
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    BlockingConfig,
    BlockingKeyConfig,
)


def _config():
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m",
            type="weighted",
            threshold=0.7,
            fields=[MatchkeyField(
                field="name",
                scorer="jaro_winkler",
                weight=1.0,
                transforms=["lowercase"],
            )],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            max_block_size=5000,
            skip_oversized=False,
        ),
    )


# ── profile_signature tests ────────────────────────────────────────────────


def test_signature_deterministic_for_same_shape():
    df1 = pl.DataFrame({"name": ["a"], "city": ["x"]})
    df2 = pl.DataFrame({"name": ["b"], "city": ["y"]})
    assert profile_signature(df1) == profile_signature(df2)


def test_signature_differs_by_column_count():
    df1 = pl.DataFrame({"name": ["a"]})
    df2 = pl.DataFrame({"name": ["a"], "city": ["x"]})
    assert profile_signature(df1) != profile_signature(df2)


def test_signature_differs_by_mode():
    df = pl.DataFrame({"name": ["a"], "city": ["x"]})
    assert profile_signature(df, mode="dedupe") != profile_signature(df, mode="match")


def test_signature_skips_internal_columns():
    df1 = pl.DataFrame({"name": ["a"], "city": ["x"]})
    df2 = pl.DataFrame({"name": ["a"], "city": ["x"], "__row_id__": [0]})
    assert profile_signature(df1) == profile_signature(df2)


def test_signature_is_16_hex_chars():
    df = pl.DataFrame({"name": ["a"]})
    sig = profile_signature(df)
    assert len(sig) == 16
    assert all(c in "0123456789abcdef" for c in sig)


def test_signature_stable_across_calls():
    df = pl.DataFrame({"name": ["a"], "age": [1]})
    assert profile_signature(df) == profile_signature(df)


# ── AutoConfigMemory tests ─────────────────────────────────────────────────


def test_remember_then_lookup_succeeded():
    mem = AutoConfigMemory(db_path=":memory:")
    mem.remember("sig1", _config(), succeeded=True, n_iterations=2, f1_proxy=0.85)
    out = mem.lookup_best("sig1")
    assert out is not None
    assert out == _config()


def test_lookup_returns_none_for_unknown_signature():
    mem = AutoConfigMemory(db_path=":memory:")
    assert mem.lookup_best("never-seen") is None


def test_lookup_only_returns_succeeded_runs():
    mem = AutoConfigMemory(db_path=":memory:")
    mem.remember("sig1", _config(), succeeded=False, n_iterations=3)
    assert mem.lookup_best("sig1") is None


def test_lookup_returns_most_recent_succeeded():
    """Multiple successful runs → most recent wins."""
    import time
    mem = AutoConfigMemory(db_path=":memory:")
    cfg_old = _config()
    cfg_new = _config().model_copy(update={
        "matchkeys": [_config().matchkeys[0].model_copy(update={"threshold": 0.5})],
    })
    mem.remember("sig1", cfg_old, succeeded=True, n_iterations=1, f1_proxy=0.8)
    time.sleep(0.01)  # ensure created_at timestamps differ
    mem.remember("sig1", cfg_new, succeeded=True, n_iterations=2, f1_proxy=0.9)
    out = mem.lookup_best("sig1")
    assert out is not None
    assert out.matchkeys[0].threshold == 0.5  # the newer one


def test_clear_removes_all():
    mem = AutoConfigMemory(db_path=":memory:")
    mem.remember("sig1", _config(), succeeded=True, n_iterations=1)
    mem.clear()
    assert mem.lookup_best("sig1") is None


def test_remember_failed_run_does_not_appear_in_lookup():
    mem = AutoConfigMemory(db_path=":memory:")
    mem.remember("sig1", _config(), succeeded=False, n_iterations=3)
    mem.remember("sig2", _config(), succeeded=True, n_iterations=2)
    assert mem.lookup_best("sig1") is None
    assert mem.lookup_best("sig2") is not None


def test_all_for_returns_all_rows():
    mem = AutoConfigMemory(db_path=":memory:")
    mem.remember("sig1", _config(), succeeded=True, n_iterations=1)
    mem.remember("sig1", _config(), succeeded=False, n_iterations=2)
    rows = mem.all_for("sig1")
    assert len(rows) == 2
    assert all(r["profile_signature"] == "sig1" for r in rows)


def test_all_for_empty_for_unknown_signature():
    mem = AutoConfigMemory(db_path=":memory:")
    assert mem.all_for("unknown-sig") == []


def test_f1_proxy_stored_and_retrievable():
    mem = AutoConfigMemory(db_path=":memory:")
    mem.remember("sig1", _config(), succeeded=True, n_iterations=1, f1_proxy=0.92)
    rows = mem.all_for("sig1")
    assert len(rows) == 1
    assert abs(rows[0]["f1_proxy"] - 0.92) < 1e-6


def test_f1_proxy_none_stored_as_null():
    mem = AutoConfigMemory(db_path=":memory:")
    mem.remember("sig1", _config(), succeeded=True, n_iterations=1, f1_proxy=None)
    rows = mem.all_for("sig1")
    assert rows[0]["f1_proxy"] is None


def test_pydantic_round_trip():
    """model_dump_json / model_validate_json must preserve config exactly."""
    cfg = _config()
    json_str = cfg.model_dump_json()
    cfg2 = GoldenMatchConfig.model_validate_json(json_str)
    assert cfg == cfg2


def test_close_is_idempotent():
    mem = AutoConfigMemory(db_path=":memory:")
    mem.close()
    mem.close()  # should not raise
