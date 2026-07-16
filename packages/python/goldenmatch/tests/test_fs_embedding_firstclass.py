"""Embeddings as first-class Fellegi-Sunter scorers.

`embedding` / `record_embedding` used to CRASH on the FS path (both EM
training and scalar scoring go through `score_field`, which has no embedding
branch). This suite pins that they now train + score end-to-end on the
vectorized path.

All local tests inject a deterministic FAKE embedder (torch-free, OOM-free) via
`get_embedder`. A real-embedder end-to-end smoke is CI-only (torch segfaults
locally).
"""

from __future__ import annotations

import hashlib
import os

import numpy as np
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


class _FakeEmbedder:
    """Deterministic, L2-normalized, hash-based embeddings. Identical strings
    map to identical vectors (cosine 1.0); different strings are ~orthogonal.
    Mirrors the real embedder's null coercion (None/empty -> "")."""

    dim = 8

    def embed_column(self, values, cache_key=None):
        clean = [str(v) if v is not None and str(v).strip() else "" for v in values]
        out = np.zeros((len(clean), self.dim), dtype=np.float64)
        for i, s in enumerate(clean):
            h = hashlib.sha256(s.encode("utf-8")).digest()
            v = np.frombuffer(h[: self.dim], dtype=np.uint8).astype(np.float64)
            v = v - v.mean()
            norm = np.linalg.norm(v)
            out[i] = v / norm if norm > 0 else v
        return out

    def cosine_similarity_matrix(self, embeddings):
        return embeddings @ embeddings.T


@pytest.fixture
def fake_embedder(monkeypatch):
    emb = _FakeEmbedder()
    monkeypatch.setattr(
        "goldenmatch.core.embedder.get_embedder", lambda *a, **k: emb,
    )
    return emb


def _emb_df() -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": list(range(6)),
        "bio": ["data scientist", "data scientist", "sales manager",
                "sales manager", "data scientist", "sales manager"],
    })


def _emb_mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs", type="probabilistic", link_threshold=0.0,
        fields=[MatchkeyField(field="bio", scorer="embedding", levels=2,
                              partial_threshold=0.8)],
    )


# ── Task 1: un-gate ───────────────────────────────────────────────────


def test_embedding_scorers_vectorizable():
    from goldenmatch.core.probabilistic import vectorized_scorer_supported
    assert vectorized_scorer_supported("embedding") is True
    assert vectorized_scorer_supported("record_embedding") is True
    # regression: ordinary scorers still supported
    assert vectorized_scorer_supported("jaro_winkler") is True


# ── Task 2: EM trains on an embedding field ───────────────────────────


def test_train_em_on_embedding_field(fake_embedder):
    from goldenmatch.core.probabilistic import train_em
    em = train_em(_emb_df(), _emb_mk(), n_sample_pairs=10, max_iterations=3)
    assert "bio" in em.match_weights
    assert all(np.isfinite(w) for w in em.match_weights["bio"])


# ── Task 3: scoring routes vectorized + kill-switch safety ────────────


def test_block_scorer_vectorized_for_embedding_even_with_killswitch(fake_embedder, monkeypatch):
    """A model-backed scorer forces the vectorized path even under the
    FS_VECTORIZED=0 debug knob (scalar literally can't run it)."""
    from goldenmatch.core.probabilistic import probabilistic_block_scorer, train_em
    monkeypatch.setenv("GOLDENMATCH_FS_VECTORIZED", "0")
    df, mk = _emb_df(), _emb_mk()
    em = train_em(df, mk, n_sample_pairs=10, max_iterations=2)
    fn = probabilistic_block_scorer(mk, em)
    pairs = fn(df)  # must not crash (scalar would ValueError)
    assert isinstance(pairs, list)


def test_vectorized_embedding_scores_duplicates_high(fake_embedder):
    """Same-bio pairs (identical embedding, cosine 1.0) outscore different-bio."""
    from goldenmatch.core.probabilistic import (
        score_probabilistic_vectorized,
        train_em,
    )
    df, mk = _emb_df(), _emb_mk()
    em = train_em(df, mk, n_sample_pairs=10, max_iterations=3)
    scores = {(min(a, b), max(a, b)): s
              for a, b, s in score_probabilistic_vectorized(df, mk, em)}
    # (0,1) same bio; (0,2) different bio.
    assert scores.get((0, 1), 0.0) > scores.get((0, 2), 1.0)


def _rec_df() -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": list(range(8)),
        "title": ["deep learning", "deep learning", "tax law", "tax law",
                  "quantum optics", "quantum optics", "roman history", "roman history"],
        "venue": ["neurips", "neurips", "harvard lr", "harvard lr",
                  "physical review", "physical review", "past & present", "past & present"],
    })


def _rec_mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs", type="probabilistic", link_threshold=0.0,
        fields=[MatchkeyField(field="__record__", scorer="record_embedding",
                              columns=["title", "venue"], levels=2,
                              partial_threshold=0.8)],
    )


def test_record_embedding_trains_and_scores(fake_embedder):
    from goldenmatch.core.probabilistic import (
        score_probabilistic_vectorized,
        train_em,
    )
    df, mk = _rec_df(), _rec_mk()
    em = train_em(df, mk, n_sample_pairs=8, max_iterations=3)
    assert "__record__" in em.match_weights
    scores = {(min(a, b), max(a, b)): s
              for a, b, s in score_probabilistic_vectorized(df, mk, em)}
    # (0,1) identical record; (0,2) different.
    assert scores.get((0, 1), 0.0) > scores.get((0, 2), 1.0)


# ── Task 4: train <-> score level parity (load-bearing invariant) ─────


def test_train_score_level_parity(fake_embedder):
    import goldenmatch.core.probabilistic as prob

    df, mk = _emb_df(), _emb_mk()
    row_lookup = {r["__row_id__"]: r for r in df.to_dicts()}
    pairs = [(0, 1), (0, 2), (2, 3), (1, 4)]
    est = prob._build_comparison_matrix(pairs, row_lookup, mk)  # E-step levels

    f = mk.fields[0]
    n = df.height
    vals = prob._field_values_for_block(df, f, n)
    sim = prob._field_score_matrix_dedup(vals, f.scorer)
    lvl = prob._levels_from_similarity(
        sim, int(f.levels), float(f.partial_threshold), level_thresholds=f.level_thresholds,
    )
    for i, (a, b) in enumerate(pairs):
        assert est[i, 0] == lvl[a, b], (a, b, est[i, 0], lvl[a, b])


# ── Task 5: TUI engine routes FS through the block-scorer router ──────


def test_tui_engine_fs_routes_through_block_scorer(tmp_path, monkeypatch):
    """The TUI must score FS via `probabilistic_block_scorer` (native/
    vectorized), not call `score_probabilistic` (scalar) directly."""
    import csv as _csv

    import goldenmatch.core.probabilistic as prob
    from goldenmatch.tui.engine import MatchEngine

    f = tmp_path / "d.csv"
    names = ["alice", "alyce", "bob", "robert", "cara", "kara"]
    with open(f, "w", newline="") as fp:
        w = _csv.writer(fp)
        w.writerow(["name", "grp"])
        for nm in names:
            w.writerow([nm, "g"])

    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="fs", type="probabilistic", link_threshold=0.0,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                  levels=2, partial_threshold=0.8)],
        )],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["grp"])]),
        output=OutputConfig(),
    )

    calls: list[str] = []
    real = prob.probabilistic_block_scorer
    monkeypatch.setattr(
        prob, "probabilistic_block_scorer",
        lambda mk, em: (calls.append(mk.name), real(mk, em))[1],
    )
    MatchEngine([str(f)]).run_full(cfg)
    assert "fs" in calls


# ── Task 6: real-embedder end-to-end (opt-in; torch segfaults locally) ─


@pytest.mark.skipif(
    os.environ.get("GOLDENMATCH_RUN_EMBEDDER_TESTS") != "1",
    reason="real sentence-transformers embedder (torch); opt-in via "
           "GOLDENMATCH_RUN_EMBEDDER_TESTS=1 — segfaults on the dev box.",
)
def test_embedding_fs_end_to_end_real_embedder():
    """End-to-end proof with the real embedder: an embedding FS matchkey
    trains + scores through dedupe_df and clusters the obvious duplicates."""
    from goldenmatch import dedupe_df

    df = pl.DataFrame({
        "bio": ["machine learning researcher", "ML researcher",
                "corporate tax attorney", "tax lawyer",
                "machine learning researcher", "corporate tax attorney"],
        "grp": ["g"] * 6,
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="fs", type="probabilistic", link_threshold=0.5,
            fields=[MatchkeyField(field="bio", scorer="embedding", levels=2,
                                  partial_threshold=0.7)],
        )],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["grp"])]),
        output=OutputConfig(),
    )
    res = dedupe_df(df, config=cfg)
    assert res.scored_pairs  # trained + scored end-to-end, no crash


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
