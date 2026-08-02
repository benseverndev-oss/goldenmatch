"""Cross-route parity for FS ``missing="disagree"`` semantics.

#1846 made missing-value semantics toggleable (``unobserved`` vs ``disagree``);
``test_fs_missing_semantics_1846.py`` locks the ONE origin
(:func:`comparison_vector`) and ``test_fs_native_missing_mode.py`` locks the
native-eligibility gate. What was UNGUARDED is that the several live FS scoring
routes all honor ``disagree`` IDENTICALLY:

  * scalar        :func:`score_probabilistic`         (the reference)
  * vectorized    :func:`score_probabilistic_vectorized`      (default pipeline)
  * batch         :func:`score_probabilistic_vectorized_batch` (small-block coalesce)
  * dispatch      :func:`probabilistic_block_scorer`   (what the pipeline actually calls)

This gap is not hypothetical: the reverted candidate-pair-dedup lever (PR #2295)
was a scoring route that silently IGNORED ``fs_missing_mode`` — on a ``disagree``
config it treated nulls as neutral instead of level-0-against, collapsing
precision 0.928 -> 0.365 before it was caught by hand. A route that drops
``disagree`` still produces plausible-looking pairs, so only a parity assertion
catches it. Any NEW FS scoring route must be added here.

The fixture carries nulls in every comparison field, and each test asserts the
fixture is DISCRIMINATING (``disagree`` output actually differs from
``unobserved``) so it cannot silently degrade into a no-op that would pass even
if a route ignored the mode.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    fs_missing_mode,
    probabilistic_block_scorer,
    score_probabilistic,
    score_probabilistic_vectorized,
    score_probabilistic_vectorized_batch,
    train_em,
)


def _df() -> pl.DataFrame:
    """A single block with nulls in every comparison field, so ``disagree``
    (missing -> level 0) diverges from ``unobserved`` (missing -> no evidence)."""
    return pl.DataFrame({
        "__row_id__": list(range(1, 11)),
        "first_name": ["John", "Jon", "Jane", "Janet", "Bob",
                       "Robert", "Alice", "Alicia", "Tom", "Thomas"],
        "last_name": ["Smith", "Smith", None, "Doe", "Jones",
                      None, "Brown", "Brown", None, "Wilson"],
        "zip": ["90210", "90210", "10001", "10001", "60601",
                "60601", "30301", None, "20001", "20001"],
    })


def _mk(missing: str | None = "disagree") -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs", type="probabilistic", missing=missing, link_threshold=0.0,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )


def _ps(pairs) -> dict[tuple[int, int], float]:
    return {(min(a, b), max(a, b)): round(s, 4) for a, b, s in pairs}


def _same(a: dict, b: dict, tol: float = 0.02) -> bool:
    """Same canonical pair SET, and per-pair scores within native/rapidfuzz
    tolerance (the same tol the vectorized-parity suite uses)."""
    if set(a) != set(b):
        return False
    return all(abs(a[k] - b[k]) <= tol for k in a)


class TestDisagreeRouteParity:
    """Every scoring route must produce the same ``disagree`` output as the
    scalar reference — and the fixture must be discriminating."""

    def test_fixture_is_discriminating(self):
        """Guard the guard: if ``disagree`` == ``unobserved`` on this fixture, a
        route ignoring the mode would pass parity vacuously. It must not."""
        mk_d, mk_u = _mk("disagree"), _mk("unobserved")
        df = _df()
        dis = _ps(score_probabilistic_vectorized(df, mk_d, train_em(df, mk_d, n_sample_pairs=200)))
        obs = _ps(score_probabilistic_vectorized(df, mk_u, train_em(df, mk_u, n_sample_pairs=200)))
        assert dis != obs, (
            "fixture does not exercise the disagree/unobserved difference; "
            "the parity assertions below would be vacuous"
        )

    def test_vectorized_matches_scalar(self):
        df, mk = _df(), _mk("disagree")
        em = train_em(df, mk, n_sample_pairs=200)
        assert fs_missing_mode(mk) == "disagree"
        sca = _ps(score_probabilistic(df, mk, em))
        vec = _ps(score_probabilistic_vectorized(df, mk, em))
        assert _same(sca, vec), f"scalar={sca} vectorized={vec}"

    def test_batch_matches_scalar(self):
        df, mk = _df(), _mk("disagree")
        em = train_em(df, mk, n_sample_pairs=200)
        sca = _ps(score_probabilistic(df, mk, em))
        bat = _ps(score_probabilistic_vectorized_batch([df], mk, em))
        assert _same(sca, bat), f"scalar={sca} batch={bat}"

    def test_block_scorer_dispatch_matches_scalar(self):
        """The pipeline calls ``probabilistic_block_scorer(mk, em)`` — the dispatch
        that selects vectorized vs scalar — so it must honor disagree too."""
        df, mk = _df(), _mk("disagree")
        em = train_em(df, mk, n_sample_pairs=200)
        fn = probabilistic_block_scorer(mk, em)
        sca = _ps(score_probabilistic(df, mk, em))
        assert _same(sca, _ps(fn(df))), "block-scorer dispatch dropped disagree"


class TestDisagreeViaEnvOverride:
    """The reverted #2295 lever's failing config reached ``disagree`` via the
    per-dataset auto-config pick, resolved through ``GOLDENMATCH_FS_MISSING``.
    The env override must reach every route identically — a matchkey declared
    ``unobserved`` but overridden to ``disagree`` scores as ``disagree``."""

    def test_env_override_reaches_all_routes(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_MISSING", "disagree")
        df, mk = _df(), _mk("unobserved")  # config says unobserved; env overrides
        assert fs_missing_mode(mk) == "disagree"
        em = train_em(df, mk, n_sample_pairs=200)
        sca = _ps(score_probabilistic(df, mk, em))
        vec = _ps(score_probabilistic_vectorized(df, mk, em))
        bat = _ps(score_probabilistic_vectorized_batch([df], mk, em))
        assert _same(sca, vec) and _same(sca, bat)

    def test_env_override_actually_changes_output(self, monkeypatch):
        """Sanity: the override is not a no-op — output differs from the
        config-declared unobserved run."""
        df, mk = _df(), _mk("unobserved")
        obs = _ps(score_probabilistic_vectorized(df, mk, train_em(df, mk, n_sample_pairs=200)))
        monkeypatch.setenv("GOLDENMATCH_FS_MISSING", "disagree")
        dis = _ps(score_probabilistic_vectorized(df, mk, train_em(df, mk, n_sample_pairs=200)))
        assert dis != obs


class TestDisagreeCoalesceInvariance:
    """The small-block coalesce (batch) route slices per-block diagonals out of a
    shared matrix. Under ``disagree`` — where nulls are forced ``observed`` — a
    two-block batch must emit the SAME within-block pairs as scoring each block
    alone (no cross-block null contamination)."""

    def test_two_block_batch_equals_per_block(self):
        df = _df()
        b1 = df.head(5)
        b2 = df.tail(5)
        mk = _mk("disagree")
        em = train_em(df, mk, n_sample_pairs=200)
        per_block = _ps(
            score_probabilistic_vectorized(b1, mk, em)
            + score_probabilistic_vectorized(b2, mk, em)
        )
        batched = _ps(score_probabilistic_vectorized_batch([b1, b2], mk, em))
        assert _same(per_block, batched), f"per_block={per_block} batched={batched}"
