"""Issue #1805 (checkbox 1) — match-mode scope (`across_files_only` /
`target_ids`) on the FS bucket route.

The audit found ZERO FS-route tests for the two-table / cross-source match
modes even though the FS bucket route honors both (`score_buckets` takes
`across_files_only` + `source_lookup` and `target_ids`, filtering internally at
`backends/score_buckets.py`). `target_ids` had a single batched-route test
(`test_probabilistic_parallel.py`); `across_files_only` on FS had none.

These pin the bucket route (bucket-python unconditionally; bucket-native behind
`_fs_native_enabled()`): the mode must (a) keep every genuinely cross-side pair
and (b) drop every same-side pair, and the filtered set must equal the
unfiltered set minus its same-side pairs (the filter is scope-only, it must not
perturb scores or invent pairs). One 4-row fixture drives both modes: a
cross-side duplicate (survives) and a same-side duplicate (dropped).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.probabilistic import _fs_native_enabled, train_em

native_required = pytest.mark.skipif(
    not _fs_native_enabled(),
    reason="native FS kernel not built/enabled (GOLDENMATCH_FS_NATIVE + built _native)",
)


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ],
    )


def _blocking() -> BlockingConfig:
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])])


def _fixture() -> pl.DataFrame:
    """One zip block. Rows 0/1 are a cross-side duplicate (shared email, typo'd
    first name); rows 2/3 are a same-side duplicate (shared email). Distinct
    emails across the two entities keep 0/1 vs 2/3 from matching."""
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        # `__source__` mirrors _SOURCE_LOOKUP: the bucket across-files path reads
        # the source off the frame, while source_lookup is the external/batched
        # scorer's view -- pass both so every route sees the same membership.
        "__source__": ["a", "b", "a", "a"],
        "first_name": ["John", "Jon", "Mary", "Mari"],
        "last_name": ["Smith", "Smith", "Jones", "Jones"],
        "email": ["john@x.com", "john@x.com", "mary@x.com", "mary@x.com"],
        "zip": ["10001", "10001", "10001", "10001"],
    })


# Membership per mode. across_files_only: source A/B. target_ids: two-table set.
# Both are chosen so (0,1) straddles (survives) and (2,3) is same-side (dropped).
_SOURCE_LOOKUP = {0: "a", 1: "b", 2: "a", 3: "a"}
_TARGET_IDS = {1, 2, 3}


def _pairset(pairs) -> set[tuple[int, int]]:
    return {(min(a, b), max(a, b)) for a, b, _s in pairs}


def _run(*, native: bool, monkeypatch, **score_kwargs):
    from goldenmatch.backends.score_buckets import score_buckets

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "1" if native else "0")
    df = _fixture()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200, seed=42)
    return _pairset(score_buckets(df, _blocking(), mk, set(), em_result=em, **score_kwargs))


def _side_across(a: int, b: int) -> bool:
    return _SOURCE_LOOKUP[a] != _SOURCE_LOOKUP[b]


def _side_target(a: int, b: int) -> bool:
    return (a in _TARGET_IDS) != (b in _TARGET_IDS)


# ── across_files_only ─────────────────────────────────────────────────────────


def test_bucket_python_across_files_only(monkeypatch):
    unfiltered = _run(native=False, monkeypatch=monkeypatch)
    filtered = _run(native=False, monkeypatch=monkeypatch,
                    across_files_only=True, source_lookup=_SOURCE_LOOKUP)

    # Anchor: the unfiltered set contains a same-source pair to drop.
    assert any(not _side_across(a, b) for a, b in unfiltered), unfiltered
    # Every surviving pair is cross-source, and the filter is scope-only.
    assert all(_side_across(a, b) for a, b in filtered), filtered
    assert filtered == {p for p in unfiltered if _side_across(*p)}
    assert (0, 1) in filtered and (2, 3) not in filtered


@native_required
def test_bucket_native_across_files_only(monkeypatch):
    unfiltered = _run(native=True, monkeypatch=monkeypatch)
    filtered = _run(native=True, monkeypatch=monkeypatch,
                    across_files_only=True, source_lookup=_SOURCE_LOOKUP)
    assert any(not _side_across(a, b) for a, b in unfiltered), unfiltered
    assert filtered == {p for p in unfiltered if _side_across(*p)}
    assert (0, 1) in filtered and (2, 3) not in filtered


# ── target_ids (two-table linkage scope) ──────────────────────────────────────


def test_bucket_python_target_ids(monkeypatch):
    unfiltered = _run(native=False, monkeypatch=monkeypatch)
    filtered = _run(native=False, monkeypatch=monkeypatch, target_ids=_TARGET_IDS)

    assert any(not _side_target(a, b) for a, b in unfiltered), unfiltered
    assert all(_side_target(a, b) for a, b in filtered), filtered
    assert filtered == {p for p in unfiltered if _side_target(*p)}
    assert (0, 1) in filtered and (2, 3) not in filtered


@native_required
def test_bucket_native_target_ids(monkeypatch):
    unfiltered = _run(native=True, monkeypatch=monkeypatch)
    filtered = _run(native=True, monkeypatch=monkeypatch, target_ids=_TARGET_IDS)
    assert any(not _side_target(a, b) for a, b in unfiltered), unfiltered
    assert filtered == {p for p in unfiltered if _side_target(*p)}
    assert (0, 1) in filtered and (2, 3) not in filtered


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
