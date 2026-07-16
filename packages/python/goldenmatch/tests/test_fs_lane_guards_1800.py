"""Issue #1800 / FS-in-scale-lanes — probabilistic matchkeys through the
distributed and chunked lanes.

History: both lanes used to score only ``exact`` + ``weighted`` matchkeys, so
a ``type="probabilistic"`` matchkey contributed zero pairs with no error
(#1800's silent drop), then failed loudly (the #1800 fix). They now SCORE FS
matchkeys against one shared EMResult: the distributed driver trains once
before dispatch (or loads ``mk.model_path``); the chunked lane trains once on
the first chunk (or loads ``mk.model_path``). The loud guard remains for the
one case that stays unsupported: the bare scoring kernel invoked with an FS
matchkey and NO model source (per-partition EM training would fit
inconsistent models).

Deliberately NOT gated on ``ray`` — the kernel and driver-side model prep run
without touching Ray, so the tests run in the default (ray-free) environment.
"""

from __future__ import annotations

import csv

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)


def _fs_config(model_path: str | None = None) -> GoldenMatchConfig:
    """A config with a probabilistic (Fellegi-Sunter) matchkey + blocking."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fs_name",
                type="probabilistic",
                fields=[MatchkeyField(field="name", scorer="jaro_winkler")],
                model_path=model_path,
            ),
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        output=OutputConfig(),
    )


def _weighted_config() -> GoldenMatchConfig:
    """A weighted config (the lanes DO support this) — negative control."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="w_name",
                type="weighted",
                threshold=0.8,
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0),
                ],
            ),
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        output=OutputConfig(),
    )


def _person_df() -> pl.DataFrame:
    return pl.DataFrame({
        "name": ["Alice", "Alyce", "Bob", "Robert"],
        "zip": ["10001", "10001", "10002", "10002"],
    })


_SURNAMES = [
    "Garcia", "Kowalski", "Nguyen", "Okafor", "Petrov", "Sato",
    "Muller", "Rossi", "Dubois", "Larsen", "Novak", "Silva",
]


def _dup_df() -> tuple[pl.DataFrame, set[tuple[int, int]]]:
    """12 zip-blocks of 3 records: one exact-duplicate pair plus one
    unrelated name per block. EM's within-block sample then sees both
    agreements AND disagreements (a single-signal sample fits a
    non-monotonic model where exact dups score below threshold), and the
    expected FS output is exactly the 12 duplicate pairs.
    """
    names: list[str] = []
    zips: list[str] = []
    dup_pairs: set[tuple[int, int]] = set()
    rid = 0
    for i, nm in enumerate(_SURNAMES):
        names += [f"{nm} Alpha", f"{nm} Alpha", f"Zed{i} Qrstuv"]
        zips += [f"{40000 + i}"] * 3
        dup_pairs.add((rid, rid + 1))
        rid += 3
    return pl.DataFrame({"name": names, "zip": zips}), dup_pairs


def _trained_em(df: pl.DataFrame, cfg: GoldenMatchConfig):
    from goldenmatch.core.probabilistic import load_or_train_em

    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64),
        )
    return load_or_train_em(df, cfg.get_matchkeys()[0])


# ── Distributed kernel (`_score_partition_with_config`) ────────────────


def test_kernel_rejects_probabilistic_without_model():
    """No driver-supplied EMResult and no mk.model_path: the kernel must
    raise, not train per-partition or silently return []."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    with pytest.raises(NotImplementedError, match="probabilistic"):
        _score_partition_with_config(_person_df(), _fs_config())


def test_kernel_error_names_the_matchkey_and_alternatives():
    """The message must name the offending matchkey and the ways out
    (model_path, driver-side training, single-box, weighted)."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    with pytest.raises(NotImplementedError) as exc:
        _score_partition_with_config(_person_df(), _fs_config())
    msg = str(exc.value)
    assert "fs_name" in msg
    assert "model_path" in msg
    assert "weighted" in msg  # the conversion suggestion


def test_kernel_scores_probabilistic_with_supplied_model():
    """A driver-supplied EMResult unlocks FS scoring in the kernel: the
    exact-duplicate pairs must come back."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    cfg = _fs_config()
    cfg.backend = "bucket"
    df, dup_pairs = _dup_df()
    em = _trained_em(df, cfg)
    pairs = _score_partition_with_config(
        df, cfg, fs_em_results={"fs_name": em},
    )
    got = {(min(a, b), max(a, b)) for a, b, _s in pairs}
    assert dup_pairs <= got


def test_kernel_scores_probabilistic_with_model_path(tmp_path):
    """mk.model_path on disk unlocks FS scoring without a supplied dict
    (the db/sync streaming caller's route)."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    df, dup_pairs = _dup_df()
    path = str(tmp_path / "fs_model.json")
    train_cfg = _fs_config(model_path=path)
    _trained_em(df, train_cfg)  # trains and persists to model_path

    cfg = _fs_config(model_path=path)
    cfg.backend = "bucket"
    pairs = _score_partition_with_config(df, cfg)
    got = {(min(a, b), max(a, b)) for a, b, _s in pairs}
    assert dup_pairs <= got


def test_kernel_still_accepts_weighted():
    """Negative control: a weighted config must NOT trip the guard."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    cfg = _weighted_config()
    cfg.backend = "bucket"
    pairs = _score_partition_with_config(_person_df(), cfg)
    assert isinstance(pairs, list)  # runs to completion, no raise


# ── Distributed driver model prep (`_prepare_fs_models`) ───────────────


class _StubDataset:
    """Duck-typed stand-in for ray.data.Dataset: only take_batch is used
    by the driver-side FS model prep (no Ray in the test env)."""

    def __init__(self, df: pl.DataFrame):
        self._df = df
        self.take_batch_calls = 0

    def take_batch(self, n: int, *, batch_format: str = "pyarrow"):
        assert batch_format == "pyarrow"
        self.take_batch_calls += 1
        return self._df.head(n).to_arrow()


def test_prepare_fs_models_trains_on_driver_sample():
    from goldenmatch.distributed.scoring import _prepare_fs_models

    ds = _StubDataset(_dup_df()[0])
    models = _prepare_fs_models(ds, _fs_config())
    assert models is not None and set(models) == {"fs_name"}
    assert ds.take_batch_calls == 1
    assert models["fs_name"].match_weights  # a real trained/fallback model


def test_prepare_fs_models_loads_persisted_model_without_sampling(tmp_path):
    """A preloaded mk.model_path must be loaded driver-side WITHOUT pulling
    a sample from the dataset (the dataset may be expensive to touch)."""
    from goldenmatch.distributed.scoring import _prepare_fs_models

    df, _ = _dup_df()
    path = str(tmp_path / "fs_model.json")
    _trained_em(df, _fs_config(model_path=path))  # persist

    ds = _StubDataset(df)
    models = _prepare_fs_models(ds, _fs_config(model_path=path))
    assert models is not None and "fs_name" in models
    assert ds.take_batch_calls == 0


def test_prepare_fs_models_none_without_fs_matchkeys():
    from goldenmatch.distributed.scoring import _prepare_fs_models

    assert _prepare_fs_models(None, _weighted_config()) is None


# ── Chunked lane (`ChunkedMatcher.process_file`) ───────────────────────


def _write_csv(path, rows):
    with open(path, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["name", "zip"])
        w.writerows(rows)


def test_chunked_scores_probabilistic_within_and_across_chunks(tmp_path):
    """FS through the chunked lane: exact duplicates inside one chunk AND
    split across chunks must both surface as pairs. The model trains once
    on the first chunk and is reused (see _ensure_fs_model)."""
    from goldenmatch.core.chunked import ChunkedMatcher

    rows = [[f"Person {chr(65 + i % 26)}{i}", f"{30000 + i}"] for i in range(40)]
    rows[7] = ["Person Q7", "30003"]   # within-chunk dup of row 3 (chunk 1)...
    rows[3] = ["Person Q7", "30003"]   # ...same name + zip
    rows[31] = ["Person M12", "30012"]  # cross-chunk dup of row 12 (chunk 1 vs 2)
    rows[12] = ["Person M12", "30012"]
    f = tmp_path / "data.csv"
    _write_csv(f, rows)

    matcher = ChunkedMatcher(config=_fs_config(), chunk_size=20)
    result = matcher.process_file(str(f))
    assert result["chunks_processed"] == 2
    got = {(min(a, b), max(a, b)) for a, b, _s in matcher._all_pairs}
    assert (3, 7) in got     # within-chunk FS pair
    assert (12, 31) in got   # cross-chunk FS pair
    assert "fs_name" in matcher._fs_em_results  # trained once, cached


def test_chunked_uses_persisted_model_when_set(tmp_path):
    """mk.model_path short-circuits first-chunk training in the chunked
    lane (Splink-style train-once reuse)."""
    from goldenmatch.core.chunked import ChunkedMatcher

    df, _ = _dup_df()
    path = str(tmp_path / "fs_model.json")
    _trained_em(df, _fs_config(model_path=path))  # persist

    rows = [[f"Person {chr(65 + i % 26)}{i}", f"{30000 + i}"] for i in range(10)]
    rows[6] = list(rows[1])  # one dup pair
    f = tmp_path / "data.csv"
    _write_csv(f, rows)

    matcher = ChunkedMatcher(config=_fs_config(model_path=path), chunk_size=5)
    result = matcher.process_file(str(f))
    assert result["total_records"] == 10
    got = {(min(a, b), max(a, b)) for a, b, _s in matcher._all_pairs}
    assert (1, 6) in got


def test_chunked_process_file_still_accepts_weighted(tmp_path):
    """Negative control: weighted config runs through the chunked lane."""
    from goldenmatch.core.chunked import ChunkedMatcher

    f = tmp_path / "data.csv"
    _write_csv(f, [[f"Person {i}", f"{10000 + i % 3}"] for i in range(10)])

    matcher = ChunkedMatcher(config=_weighted_config(), chunk_size=5)
    result = matcher.process_file(str(f))  # no raise
    assert result["total_records"] == 10


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
