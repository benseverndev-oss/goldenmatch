"""Tests for the EXPERIMENTAL GoldenDB matrix-native backend (backend='gpu').

Runs on CPU JAX -- validates numerical correctness, the exact GA2M additive
attribution, monotonicity, the gradient-based training step, the block-scorer
contract, pipeline dispatch, and the (id, cluster_id) handoff into the existing
CPU clustering path. GPU wall-clock is NOT validated here (no GPU in CI).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

pytest.importorskip("jax")

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.blocker import BlockResult
from goldenmatch.core.goldendb import (
    GA2MCombiner,
    GA2MInteractionCombiner,
    apply_field_weights,
    build_training_matrix,
    char_ngram_hashed,
    coarse_encode,
    combine_matrices,
    cosine_matrix,
    faiss_available,
    find_matches_gpu,
    fit_field_weights,
    jax_available,
    resolve_dataset_gpu,
    score_blocks_gpu,
    topk_candidates,
)


def _mk(threshold: float = 0.6) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="m",
        type="weighted",
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        threshold=threshold,
    )


def _block(df: pl.DataFrame) -> BlockResult:
    return BlockResult(block_key="b", df=df.lazy())


def _people() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3],
            "name": ["John Smith", "Jon Smith", "Mary Jones", "Mary Jones"],
        }
    )


# ── encoding + cosine (the matmul) ────────────────────────────────────────────

def test_jax_available():
    assert jax_available() is True


def test_encode_cosine_identical_high_different_low():
    mat = char_ngram_hashed(["john smith", "john smith", "zzzzz qqqqq"])
    sim = cosine_matrix(mat)
    assert sim.shape == (3, 3)
    assert sim[0, 1] > 0.99          # identical strings
    assert sim[0, 2] < 0.2           # disjoint strings
    np.testing.assert_allclose(np.diag(sim), 1.0, atol=1e-4)


def test_encode_null_rows_are_zero():
    mat = char_ngram_hashed(["abc", None, ""])
    assert np.all(mat[1] == 0.0)
    assert np.all(mat[2] == 0.0)


# ── GA2M combine: exact attribution + monotonicity ────────────────────────────

def test_combine_attribution_is_exact():
    rng = np.random.default_rng(0)
    sim_stack = rng.random((3, 5, 5)).astype(np.float32)
    weights = np.array([1.0, 2.0, 0.5])
    score, attribution = combine_matrices(sim_stack, weights)
    # The audit: contributions sum EXACTLY to the score.
    np.testing.assert_allclose(score, attribution.sum(axis=0), atol=1e-5)


def test_combine_is_monotone_in_each_field():
    sim_stack = np.full((2, 2, 2), 0.5, dtype=np.float32)
    weights = np.array([1.0, 1.0])
    base, _ = combine_matrices(sim_stack, weights)
    bumped = sim_stack.copy()
    bumped[0, 0, 1] = 0.95
    after, _ = combine_matrices(bumped, weights)
    assert after[0, 1] >= base[0, 1]


def test_combine_null_validity_excludes_field():
    # Field 1 invalid everywhere -> score is driven by field 0 alone.
    sim_stack = np.stack([np.full((2, 2), 0.8), np.full((2, 2), 0.0)]).astype(np.float32)
    weights = np.array([1.0, 1.0])
    valid = np.stack([np.ones((2, 2)), np.zeros((2, 2))]).astype(np.float32)
    score, _ = combine_matrices(sim_stack, weights, valid)
    np.testing.assert_allclose(score[0, 1], 0.8, atol=1e-5)


# ── GA2M trainable combine (differentiable, monotone, probabilistic) ──────────

def test_ga2m_predict_monotone():
    c = GA2MCombiner(n_fields=2)
    low = c.predict(np.array([[0.1, 0.1]]))[0]
    high = c.predict(np.array([[0.9, 0.9]]))[0]
    assert high >= low


def test_ga2m_train_step_reduces_loss():
    rng = np.random.default_rng(0)
    sims = rng.random((256, 2))
    labels = (sims.mean(axis=1) > 0.6).astype(float)
    c = GA2MCombiner(n_fields=2, seed=1)
    loss0 = c.loss(sims, labels)
    for _ in range(300):
        c.train_step(sims, labels, lr=0.05)
    loss1 = c.loss(sims, labels)
    assert loss1 < loss0


def test_ga2m_attribution_sums_to_weighted_average():
    c = GA2MCombiner(n_fields=3)
    sims = np.array([[0.2, 0.8, 0.5]])
    contrib = c.attribution(sims)
    # contributions reconstruct the weighted-average term feeding the link.
    import jax

    w = np.asarray(jax.nn.softplus(jax.numpy.asarray(c.params.w)))
    expected = (sims[0] * w).sum() / (w.sum() + 1e-9)
    np.testing.assert_allclose(contrib.sum(), expected, atol=1e-5)


# ── GA2M pairwise interaction terms (the "2") ─────────────────────────────────

def test_interaction_attribution_is_exact():
    c = GA2MInteractionCombiner(n_fields=3)
    sims = np.array([[0.3, 0.7, 0.5], [0.9, 0.1, 0.4]])
    attr = c.attribution(sims)
    recon = attr["bias"] + attr["main"].sum(axis=1) + attr["interactions"].sum(axis=1)
    np.testing.assert_allclose(recon, c.logit(sims), atol=1e-5)


def test_interaction_predict_monotone():
    c = GA2MInteractionCombiner(n_fields=2)
    low = c.predict(np.array([[0.2, 0.2]]))[0]
    high = c.predict(np.array([[0.9, 0.9]]))[0]
    assert high >= low


def test_interaction_model_beats_linear_on_and_gate():
    """label = (sim_a AND sim_b) via the product gate -- the interaction model
    should fit it better (lower BCE) than a main-effects-only model."""
    rng = np.random.default_rng(0)
    sims = rng.random((400, 2))
    labels = (sims[:, 0] * sims[:, 1] > 0.45).astype(float)

    linear = GA2MCombiner(n_fields=2, seed=1)
    for _ in range(400):
        linear.train_step(sims, labels, lr=0.1)

    inter = GA2MInteractionCombiner(n_fields=2, seed=1)
    for _ in range(400):
        inter.train_step(sims, labels, lr=0.3)

    assert inter.loss(sims, labels) < linear.loss(sims, labels)


# ── block scorer contract ─────────────────────────────────────────────────────

def test_find_matches_gpu_finds_duplicate():
    df = pl.DataFrame(
        {"__row_id__": [0, 1, 2], "name": ["John Smith", "Jon Smith", "Zelda Quux"]}
    )
    pairs = find_matches_gpu(df, _mk(0.6))
    assert any({a, b} == {0, 1} for a, b, _s in pairs)
    assert not any(2 in (a, b) for a, b, _s in pairs)
    for a, b, s in pairs:
        assert isinstance(a, int) and isinstance(b, int) and isinstance(s, float)
        assert a < b                      # canonicalised (min, max)
        assert 0.0 <= s <= 1.0


def test_find_matches_gpu_exclude_pairs():
    df = pl.DataFrame(
        {"__row_id__": [0, 1, 2], "name": ["John Smith", "Jon Smith", "Zelda Quux"]}
    )
    pairs = find_matches_gpu(df, _mk(0.6), exclude_pairs={(0, 1)})
    assert all({a, b} != {0, 1} for a, b, _s in pairs)


def test_negative_evidence_penalizes_disagreeing_field():
    from goldenmatch.config.schemas import NegativeEvidenceField

    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "name": ["John Smith", "John Smith", "John Smith"],
            "phone": ["5551111", "5551111", "5559999"],
        }
    )
    mk = MatchkeyConfig(
        name="m",
        type="weighted",
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        threshold=0.6,
        negative_evidence=[
            NegativeEvidenceField(field="phone", scorer="exact", threshold=0.5, penalty=0.5)
        ],
    )
    keys = {frozenset((a, b)) for a, b, _ in find_matches_gpu(df, mk)}
    assert frozenset((0, 1)) in keys      # same phone -> no penalty, stays matched
    assert frozenset((0, 2)) not in keys  # phone disagrees -> penalised below threshold
    assert frozenset((1, 2)) not in keys


def test_find_matches_gpu_single_row_empty():
    df = pl.DataFrame({"__row_id__": [0], "name": ["solo"]})
    assert find_matches_gpu(df, _mk()) == []


def test_score_blocks_gpu_contract_and_exclusion():
    blocks = [_block(_people())]
    pairs = score_blocks_gpu(blocks, _mk(0.6), set())
    assert isinstance(pairs, list)
    assert any({a, b} == {0, 1} for a, b, _s in pairs)
    assert any({a, b} == {2, 3} for a, b, _s in pairs)

    excluded = score_blocks_gpu(blocks, _mk(0.6), {(0, 1)})
    assert all({a, b} != {0, 1} for a, b, _s in excluded)


def test_score_blocks_gpu_empty_blocks():
    assert score_blocks_gpu([], _mk(), set()) == []


# ── pipeline dispatch ─────────────────────────────────────────────────────────

def test_get_block_scorer_routes_gpu():
    from goldenmatch.core.pipeline import _get_block_scorer

    assert _get_block_scorer(SimpleNamespace(backend="gpu")) is score_blocks_gpu
    default = _get_block_scorer(SimpleNamespace(backend=None))
    assert default.__name__ == "score_blocks_parallel"


# ── Stage A: ANN recall ───────────────────────────────────────────────────────

def _varied_block() -> pl.DataFrame:
    names = [
        "John Smith", "Jon Smith", "John Smyth",
        "Mary Jones", "Mary Jonas", "Maria Jones",
        "Robert Lee", "Bob Lee", "Roberta Lee",
        "Wei Zhang", "Wei Zang", "Xavier Stone",
    ]
    return pl.DataFrame({"__row_id__": list(range(len(names))), "name": names})


def test_topk_candidates_finds_nearest():
    coarse = char_ngram_hashed(["alpha", "alpha", "omega"])
    pairs = topk_candidates(coarse, k=1)
    assert (0, 1) in pairs            # the two identical vectors are mutual NN


def test_coarse_encode_normalised():
    emb = char_ngram_hashed(["john smith", "mary jones"])
    coarse = coarse_encode([emb], np.array([1.0]))
    norms = np.linalg.norm(coarse, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)


def test_faiss_recall_matches_bruteforce():
    """Exact FAISS IndexFlatIP and the brute-force scan must return the same
    top-k candidate set. Uses continuous random vectors so cosine ties (which both
    backends break arbitrarily and differently) don't make the comparison flaky."""
    pytest.importorskip("faiss")
    rng = np.random.default_rng(1)
    mat = rng.random((50, 32)).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    bf = set(topk_candidates(mat, k=3, backend="bruteforce"))
    fa = set(topk_candidates(mat, k=3, backend="faiss"))
    assert bf == fa


def test_faiss_ivf_recall_runs():
    """The IVF (approximate) path executes and returns candidate pairs."""
    pytest.importorskip("faiss")
    from goldenmatch.core.goldendb.recall import _topk_faiss

    rng = np.random.default_rng(0)
    mat = rng.random((300, 32)).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    pairs = _topk_faiss(mat, k=5, min_sim=0.0, use_ivf=True, nlist=8, nprobe=8)
    assert all(a < b for a, b in pairs)


def test_recall_backend_auto_selects_faiss_when_available():
    from goldenmatch.core.goldendb.recall import _resolve_backend

    expected = "faiss" if faiss_available() else "bruteforce"
    assert _resolve_backend(None) == expected
    assert _resolve_backend("bruteforce") == "bruteforce"


def test_resolve_dataset_gpu_with_faiss_backend(monkeypatch):
    pytest.importorskip("faiss")
    monkeypatch.setenv("GOLDENMATCH_GOLDENDB_RECALL_BACKEND", "faiss")
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3, 4],
            "name": [
                "Catherine Reed", "Katherine Reed",
                "Sophie Lang", "Sofie Lang",
                "Totally Different",
            ],
        }
    )
    keys = {frozenset((a, b)) for a, b, _ in resolve_dataset_gpu(df, _mk(0.5), k=4)}
    assert frozenset((0, 1)) in keys
    assert frozenset((2, 3)) in keys


def test_recall_path_matches_dense_path():
    """With k >= n-1 the ANN shortlist is every pair, so the recall path must
    reproduce the dense path exactly (modulo fp tolerance)."""
    df = _varied_block()
    mk = _mk(0.5)
    dense = find_matches_gpu(df, mk, use_recall=False)
    recall = find_matches_gpu(df, mk, use_recall=True, k=df.height)
    dense_keys = {(a, b) for a, b, _ in dense}
    recall_keys = {(a, b) for a, b, _ in recall}
    assert dense_keys == recall_keys
    dscore = {(a, b): s for a, b, s in dense}
    for a, b, s in recall:
        assert abs(s - dscore[(a, b)]) < 1e-4


def test_recall_path_finds_duplicates_with_small_k():
    df = _varied_block()
    pairs = find_matches_gpu(df, _mk(0.55), use_recall=True, k=3)
    keys = {frozenset((a, b)) for a, b, _ in pairs}
    # char-ngram cosine catches character-similar variants (not nicknames like
    # Bob/Robert, which share almost no n-grams -- correctly below threshold).
    assert frozenset((0, 1)) in keys      # John Smith / Jon Smith
    assert frozenset((9, 10)) in keys     # Wei Zhang / Wei Zang


def test_recall_auto_selected_above_threshold(monkeypatch):
    import goldenmatch.core.goldendb.scorer as gs

    monkeypatch.setattr(gs, "ANN_THRESHOLD", 4)   # force recall on a tiny block
    df = _varied_block()
    pairs = gs.find_matches_gpu(df, _mk(0.55))      # use_recall=None -> auto
    assert any({a, b} == {0, 1} for a, b, _ in pairs)


# ── gradient-based training: learn field weights from labels ──────────────────

def _two_field_labeled():
    """A frame where ``name`` predicts the label and ``noise`` does not."""
    rng = np.random.default_rng(0)
    base = ["alpha", "beta", "gamma", "delta"]
    n = 40
    names = [base[i % 4] for i in range(n)]
    noise = [f"tok{int(rng.integers(0, 100000))}" for _ in range(n)]
    df = pl.DataFrame(
        {"__row_id__": list(range(n)), "name": names, "noise": noise}
    )
    mk = MatchkeyConfig(
        name="m",
        type="weighted",
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="noise", scorer="jaro_winkler", weight=1.0),
        ],
        threshold=0.5,
    )
    labeled = [
        (a, b, 1.0 if names[a] == names[b] else 0.0)
        for a in range(n)
        for b in range(a + 1, n)
    ]
    return df, mk, labeled, names


def test_build_training_matrix_shape_and_labels():
    df, mk, labeled, _ = _two_field_labeled()
    sims, labels = build_training_matrix(df, mk, labeled)
    assert sims.shape == (len(labeled), 2)
    assert labels.shape == (len(labeled),)
    assert set(np.unique(labels)).issubset({0.0, 1.0})


def test_fit_field_weights_upweights_informative_field():
    df, mk, labeled, _ = _two_field_labeled()
    _combiner, weights = fit_field_weights(df, mk, labeled, steps=300, lr=0.1)
    # name carries the label signal; noise is random -> name weighted higher.
    assert weights["name"] > weights["noise"]


def test_apply_field_weights_updates_matchkey():
    df, mk, labeled, _ = _two_field_labeled()
    _combiner, weights = fit_field_weights(df, mk, labeled, steps=200, lr=0.1)
    trained_mk = apply_field_weights(mk, weights)
    by_name = {f.field: f.weight for f in trained_mk.fields}
    assert by_name["name"] == pytest.approx(weights["name"])
    assert by_name["noise"] == pytest.approx(weights["noise"])
    # original mk untouched (model_copy)
    assert all(f.weight == 1.0 for f in mk.fields)


def test_training_loop_improves_precision_end_to_end():
    """Learned weights should rank true (same-name) pairs above noise pairs better
    than equal weights -- the full train->apply->score loop."""
    df, mk, labeled, names = _two_field_labeled()
    _combiner, weights = fit_field_weights(df, mk, labeled, steps=400, lr=0.1)
    trained_mk = apply_field_weights(mk, weights)

    def mean_gap(matchkey):
        sims, labels = build_training_matrix(df, matchkey, labeled)
        w = np.array([f.weight for f in matchkey.fields])
        score = (sims * w).sum(axis=1) / w.sum()
        return score[labels == 1].mean() - score[labels == 0].mean()

    assert mean_gap(trained_mk) > mean_gap(mk)


# ── blocker-free dataset resolution (the "approximate primitive join") ────────

def test_resolve_dataset_gpu_finds_cross_block_duplicates():
    """ANN recall finds duplicates a blocking key would separate -- Catherine vs
    Katherine (different first letter) and Sophie vs Sofie."""
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3, 4],
            "name": [
                "Catherine Reed", "Katherine Reed",
                "Sophie Lang", "Sofie Lang",
                "Totally Different",
            ],
        }
    )
    pairs = resolve_dataset_gpu(df, _mk(0.5), k=4)
    keys = {frozenset((a, b)) for a, b, _ in pairs}
    assert frozenset((0, 1)) in keys      # Catherine / Katherine
    assert frozenset((2, 3)) in keys      # Sophie / Sofie
    assert not any(4 in (a, b) for a, b, _ in pairs)


# ── the (id, cluster_id) handoff into the existing CPU clustering path ─────────

def test_full_pipeline_dedupe_with_gpu_backend():
    """End-to-end: run_dedupe_df with backend='gpu' clusters duplicates through the
    real pipeline (block -> GPU score -> cluster -> golden)."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
    )
    from goldenmatch.core.pipeline import run_dedupe_df

    df = pl.DataFrame(
        {
            "name": ["John Smith", "Jon Smith", "Mary Jones", "Mary Jones", "Bob Brown"],
            "city": ["NYC", "NYC", "LA", "LA", "SF"],
        }
    )
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="m",
                type="weighted",
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
                threshold=0.6,
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
        backend="gpu",
    )
    res = run_dedupe_df(df, cfg, output_clusters=True)
    member_sets = [set(c["members"]) for c in res["clusters"].values()]
    assert {0, 1} in member_sets      # John Smith / Jon Smith (NYC block)
    assert {2, 3} in member_sets      # Mary Jones / Mary Jones (LA block)
    assert {4} in member_sets         # Bob Brown alone (SF block)


def test_gpu_pairs_feed_cpu_clustering():
    """The spec's contract: the GPU emits (id_a, id_b, score); the unchanged CPU
    clustering path turns it into clusters keyed by __row_id__."""
    from goldenmatch.core.cluster import build_clusters

    df = _people()
    pairs = score_blocks_gpu([_block(df)], _mk(0.6), set())
    clusters = build_clusters(pairs, df["__row_id__"].to_list())
    member_sets = [set(c["members"]) for c in clusters.values()]
    assert {0, 1} in member_sets
    assert {2, 3} in member_sets
