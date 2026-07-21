"""Issue #1805 (checkbox 1) — a persisted FS model (`model_path`) reused on the
native / bucket-native routes must score identically to the vectorized route.

`TestModelReuseSkipsBuildBlocks` (test_probabilistic.py) proves a preloaded
model skips `build_blocks`, and `TestNativeFSParity` proves native == vectorized
-- but only ever on a *freshly trained* EM. Neither covers the join: load an EM
from disk (the Splink train-once/reuse seam, `load_or_train_em`) and confirm the
native kernel and the bucket-native kernel produce byte-identical pairs to the
vectorized reference under THAT loaded model. A serialization round-trip that
perturbed a weight would silently shift native scoring on every reuse.

The vectorized route is the reference. Native and bucket-native ride the
existing `_fs_native_enabled()` skipif; bucket-python runs unconditionally.
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
from goldenmatch.core.probabilistic import (
    _fs_native_enabled,
    fs_model_preloaded,
    load_or_train_em,
    score_probabilistic_vectorized,
    train_em,
)

native_required = pytest.mark.skipif(
    not _fs_native_enabled(),
    reason="native FS kernel not built/enabled (GOLDENMATCH_FS_NATIVE + built _native)",
)


def _mk(**kw) -> MatchkeyConfig:
    defaults = dict(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ],
    )
    defaults.update(kw)
    return MatchkeyConfig(**defaults)


def _blocking() -> BlockingConfig:
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])])


def _df() -> pl.DataFrame:
    """Six rows in ONE zip block: two near-dup families sharing an email + a
    singleton, so the whole-frame scorers and the single-block bucket route see
    the same pair universe."""
    return pl.DataFrame({
        "__row_id__": [1, 2, 3, 4, 5, 6],
        "first_name": ["John", "Jon", "Jonn", "Jane", "Janet", "Zoe"],
        "last_name": ["Smith", "Smith", "Smyth", "Doe", "Doe", "Xu"],
        "email": ["j@x.com", "j@x.com", "j@x.com", "jane@x.com", "jane@x.com", "zoe@x.com"],
        "zip": ["90210", "90210", "90210", "90210", "90210", "90210"],
    })


def _pairset(pairs) -> dict[tuple[int, int], float]:
    return {(min(a, b), max(a, b)): round(s, 4) for a, b, s in pairs}


def _preloaded_em(tmp_path, df, mk):
    """Train once, persist, then reload via the model_path reuse seam. Returns
    the EM that came off disk (asserted preloaded)."""
    path = str(tmp_path / "fs_model.json")
    train_em(df, mk, n_sample_pairs=200, seed=42).save_json(path)
    reuse = _mk(model_path=path)
    em = load_or_train_em(df, reuse)
    assert fs_model_preloaded(reuse), "fixture must exercise the on-disk reuse path"
    return reuse, em


def _reference(df, mk, em) -> dict[tuple[int, int], float]:
    return _pairset(score_probabilistic_vectorized(df, mk, em))


def test_bucket_python_matches_vectorized_under_preloaded_model(tmp_path, monkeypatch):
    from goldenmatch.backends.score_buckets import score_buckets

    df = _df()
    mk, em = _preloaded_em(tmp_path, df, _mk())
    ref = _reference(df, mk, em)
    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "0")
    got = _pairset(score_buckets(df, _blocking(), mk, set(), em_result=em))
    assert got == ref


@native_required
def test_native_matches_vectorized_under_preloaded_model(tmp_path):
    from goldenmatch.core.probabilistic import score_probabilistic_native

    df = _df()
    mk, em = _preloaded_em(tmp_path, df, _mk())
    ref = _reference(df, mk, em)
    got = _pairset(score_probabilistic_native(df, mk, em))
    assert got == ref


@native_required
def test_bucket_native_matches_vectorized_under_preloaded_model(tmp_path, monkeypatch):
    from goldenmatch.backends.score_buckets import score_buckets

    df = _df()
    mk, em = _preloaded_em(tmp_path, df, _mk())
    ref = _reference(df, mk, em)
    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "1")
    got = _pairset(score_buckets(df, _blocking(), mk, set(), em_result=em))
    assert got == ref


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
