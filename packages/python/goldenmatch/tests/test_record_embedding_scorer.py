"""Tests for record-level embedding scorer."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField


class TestRecordEmbeddingSchema:
    def test_record_embedding_with_columns(self):
        f = MatchkeyField(
            scorer="record_embedding",
            columns=["title", "manufacturer"],
            weight=1.0,
            model="all-MiniLM-L6-v2",
        )
        assert f.field == "__record__"
        assert f.columns == ["title", "manufacturer"]

    def test_record_embedding_requires_columns(self):
        with pytest.raises(ValueError, match="columns"):
            MatchkeyField(scorer="record_embedding", weight=1.0)

    def test_record_embedding_empty_columns_rejected(self):
        with pytest.raises(ValueError, match="columns"):
            MatchkeyField(scorer="record_embedding", columns=[], weight=1.0)

    def test_regular_scorer_still_requires_field(self):
        with pytest.raises(ValueError, match="field"):
            MatchkeyField(scorer="jaro_winkler", weight=1.0)

    def test_regular_scorer_with_field_still_works(self):
        f = MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)
        assert f.field == "name"

    def test_record_embedding_in_weighted_matchkey(self):
        mk = MatchkeyConfig(
            name="test",
            type="weighted",
            threshold=0.80,
            fields=[
                MatchkeyField(
                    scorer="record_embedding",
                    columns=["title", "manufacturer"],
                    weight=0.7,
                    model="all-MiniLM-L6-v2",
                ),
                MatchkeyField(field="brand", scorer="exact", weight=0.3),
            ],
        )
        assert len(mk.fields) == 2


def _make_fake_embedder():
    """Embedder with deterministic fake model."""
    from goldenmatch.core.embedder import Embedder
    e = Embedder("fake-model")

    class FakeModel:
        def encode(self, texts, show_progress_bar=False, normalize_embeddings=True):
            rng = np.random.default_rng(42)
            vecs = rng.random((len(texts), 8))
            seen: dict[str, np.ndarray] = {}
            for i, t in enumerate(texts):
                if t in seen:
                    vecs[i] = seen[t]
                else:
                    seen[t] = vecs[i]
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1
            return vecs / norms

    e._model = FakeModel()
    return e


class TestRecordEmbeddingScorer:
    def test_record_embedding_score_matrix(self):
        from goldenmatch.core.scorer import _record_embedding_score_matrix

        fake = _make_fake_embedder()
        with patch("goldenmatch.core.embedder.get_embedder", return_value=fake):
            df = pl.DataFrame({
                "__row_id__": [0, 1, 2],
                "title": ["Sony Turntable", "Sony Turntable", "Samsung TV"],
                "manufacturer": ["Sony", "Sony", "Samsung"],
            })
            matrix = _record_embedding_score_matrix(
                df, ["title", "manufacturer"], "fake-model"
            )
            assert matrix.shape == (3, 3)
            assert matrix[0, 1] == pytest.approx(1.0, abs=0.01)

    def test_record_embedding_null_handling(self):
        from goldenmatch.core.scorer import _record_embedding_score_matrix

        fake = _make_fake_embedder()
        with patch("goldenmatch.core.embedder.get_embedder", return_value=fake):
            df = pl.DataFrame({
                "__row_id__": [0, 1],
                "title": ["Sony", "Sony"],
                "manufacturer": [None, "Sony"],
            })
            matrix = _record_embedding_score_matrix(
                df, ["title", "manufacturer"], "fake-model"
            )
            assert matrix.shape == (2, 2)

    def test_record_concat_values_matches_score_matrix_concat(self):
        """core/probabilistic._record_concat_values claims a byte-for-byte
        copy of the concat `_record_embedding_score_matrix` builds. Capture
        what the shipped matrix-path scorer actually concatenates (rather
        than re-deriving the algorithm) and diff it against the FS-native
        helper's output for the same rows, including nulls and
        column_weights repeats."""
        from goldenmatch.config.schemas import MatchkeyField
        from goldenmatch.core.probabilistic import _record_concat_values
        from goldenmatch.core.scorer import _record_embedding_score_matrix

        df = pl.DataFrame({
            "__row_id__": [0, 1, 2, 3],
            "title": ["Sony Turntable", None, "Samsung TV", "Widget"],
            "manufacturer": ["Sony", "Acme", None, "Acme"],
        })
        cols = ["title", "manufacturer"]

        for weights in (None, {"title": 2.0, "manufacturer": 0.5}):
            captured: dict = {}

            class _CaptureEmbedder:
                def embed_column(self, values, cache_key):
                    captured["values"] = list(values)
                    return np.zeros((len(values), 4))

                def cosine_similarity_matrix(self, embeddings):
                    return np.eye(len(embeddings))

            with patch(
                "goldenmatch.core.embedder.get_embedder",
                return_value=_CaptureEmbedder(),
            ):
                _record_embedding_score_matrix(
                    df, cols, "fake-model", column_weights=weights,
                )

            f = MatchkeyField(
                scorer="record_embedding", columns=cols, weight=1.0,
                column_weights=weights,
            )
            fs_native = _record_concat_values(df, f, df.height)

            assert fs_native == captured["values"], weights

    def test_record_embedding_in_find_fuzzy(self):
        from goldenmatch.core.scorer import find_fuzzy_matches

        fake = _make_fake_embedder()
        with patch("goldenmatch.core.embedder.get_embedder", return_value=fake):
            df = pl.DataFrame({
                "__row_id__": [0, 1, 2],
                "title": ["Sony Turntable", "Sony Turntable", "Samsung TV"],
                "brand": ["Sony", "Sony", "Samsung"],
            })
            mk = MatchkeyConfig(
                name="rec_emb",
                type="weighted",
                threshold=0.5,
                fields=[
                    MatchkeyField(
                        scorer="record_embedding",
                        columns=["title", "brand"],
                        weight=0.7,
                        model="fake-model",
                    ),
                    MatchkeyField(field="brand", scorer="exact", weight=0.3),
                ],
            )
            results = find_fuzzy_matches(df, mk)
            pair_ids = {(r[0], r[1]) for r in results}
            assert (0, 1) in pair_ids

    def test_record_embedding_only_field(self):
        """record_embedding as the only field in a matchkey."""
        from goldenmatch.core.scorer import find_fuzzy_matches

        fake = _make_fake_embedder()
        with patch("goldenmatch.core.embedder.get_embedder", return_value=fake):
            df = pl.DataFrame({
                "__row_id__": [0, 1, 2],
                "title": ["Sony Turntable", "Sony Turntable", "Samsung TV"],
            })
            mk = MatchkeyConfig(
                name="rec_only",
                type="weighted",
                threshold=0.5,
                fields=[
                    MatchkeyField(
                        scorer="record_embedding",
                        columns=["title"],
                        weight=1.0,
                        model="fake-model",
                    ),
                ],
            )
            results = find_fuzzy_matches(df, mk)
            pair_ids = {(r[0], r[1]) for r in results}
            assert (0, 1) in pair_ids


class TestRecordConcatValueMirrorsScoreMatrix:
    """``probabilistic._record_concat_value``'s docstring claims it is
    "Byte-identical to the concat in ``_record_embedding_score_matrix``" (the
    EM E-step and the scoring path must embed the SAME text). Captures the
    per-row strings ``_record_embedding_score_matrix`` actually hands the
    embedder and compares them against ``_record_concat_value`` called on the
    same rows."""

    def _captured_concat_values(self, df: pl.DataFrame, columns, column_weights=None):
        from goldenmatch.core.scorer import _record_embedding_score_matrix

        captured: dict = {}

        class CapturingEmbedder:
            def embed_column(self, values, cache_key):
                captured["values"] = list(values)
                return np.zeros((len(values), 4))

            def cosine_similarity_matrix(self, embeddings):
                return np.eye(embeddings.shape[0])

        with patch(
            "goldenmatch.core.embedder.get_embedder",
            return_value=CapturingEmbedder(),
        ):
            _record_embedding_score_matrix(
                df, columns, "fake-model", column_weights=column_weights
            )
        return captured["values"]

    def test_matches_without_column_weights(self):
        from goldenmatch.core.probabilistic import _record_concat_value

        rows = [
            {"__row_id__": 0, "title": "Sony Turntable", "brand": "Sony"},
            {"__row_id__": 1, "title": None, "brand": "Samsung"},
            {"__row_id__": 2, "title": "Same", "brand": "Same"},
        ]
        df = pl.DataFrame(rows)
        columns = ["title", "brand"]

        expected = self._captured_concat_values(df, columns)
        actual = [_record_concat_value(row, columns, None) for row in rows]
        assert actual == expected

    def test_matches_with_column_weights(self):
        """Weighted repeats (``round(w)`` for ``w > 1.0``) and the ``w <= 0``
        skip-the-field guard must agree between the two implementations."""
        from goldenmatch.core.probabilistic import _record_concat_value

        rows = [
            {"__row_id__": 0, "title": "Sony Turntable", "brand": "Sony", "sku": "X1"},
            {"__row_id__": 1, "title": None, "brand": "Samsung", "sku": "X2"},
            {"__row_id__": 2, "title": "Same", "brand": "Same", "sku": "X3"},
        ]
        df = pl.DataFrame(rows)
        columns = ["title", "brand", "sku"]
        column_weights = {"title": 2.6, "brand": 1.0, "sku": 0.0}

        expected = self._captured_concat_values(df, columns, column_weights)
        actual = [
            _record_concat_value(row, columns, column_weights) for row in rows
        ]
        assert actual == expected
