"""Tests for `goldenmatch.core.boost` -- the LLM boost engine.

`boost_accuracy` is exported from `goldenmatch/__init__.py`, so it is public
API, and it sat at 5.8% line coverage over 397 statements: 374 statements of a
top-level surface that had never executed in CI. The one existing test file
covers the labeler, not this module.

Nothing here calls an LLM, and nothing requires scikit-learn or
sentence-transformers (neither is installed in CI). The classifier branch is
exercised with a duck-typed stub instead: what matters is that boost_accuracy
routes to a saved model and maps its probabilities onto the pairs in order, not
that sklearn multiplies correctly.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pyarrow as pa
import pytest
from goldenmatch.core import boost


def _table() -> pa.Table:
    return pa.table(
        {
            "__row_id__": [1, 2, 3, 4],
            "name": ["Jonathan Okafor", "Jon Okafor", "Priya Rahman", "Priya Rahman"],
            "city": ["Leeds", "Leeds", "Bristol", "Bristol"],
        }
    )


# -- feature extraction ------------------------------------------------------


def test_pair_features_identical_strings_are_all_ones():
    jw, ts, lev, exact, ratio = boost._compute_pair_features("acme ltd", "acme ltd")
    assert exact == 1.0
    assert ratio == 1.0
    for v in (jw, ts, lev):
        assert v == pytest.approx(1.0)


def test_pair_features_empty_values_are_zero_not_nan():
    """Empty input must not produce NaN: these feed a classifier, and a NaN
    poisons the whole row rather than scoring it low."""
    feats = boost._compute_pair_features("", "")
    assert feats == [0.0, 0.0, 0.0, 1.0, 0.0]  # exact==1.0: "" equals ""
    assert not any(math.isnan(f) for f in feats)

    half = boost._compute_pair_features("acme", "")
    assert half[3] == 0.0
    assert not any(math.isnan(f) for f in half)


def test_pair_features_length_ratio_is_shorter_over_longer():
    *_, ratio = boost._compute_pair_features("abcd", "ab")
    assert ratio == pytest.approx(0.5)


def test_feature_matrix_shape_is_one_plus_five_per_column():
    pairs = [(1, 2, 0.9), (3, 4, 0.8)]
    m = boost.extract_feature_matrix(pairs, _table(), ["name", "city"])
    assert m.shape == (2, 1 + 5 * 2)
    assert m.dtype == np.float64


def test_feature_matrix_first_feature_is_the_original_score():
    """The comment calls it the single most informative feature; if the column
    order shifted, a trained model would silently read the wrong one."""
    pairs = [(1, 2, 0.42)]
    m = boost.extract_feature_matrix(pairs, _table(), ["name"])
    assert m[0][0] == pytest.approx(0.42)


def test_feature_matrix_unknown_row_id_yields_a_zero_row_of_correct_width():
    pairs = [(1, 2, 0.9), (99, 100, 0.5)]
    m = boost.extract_feature_matrix(pairs, _table(), ["name", "city"])
    assert m.shape == (2, 11)
    assert not m[1].any(), "unknown ids must give a zero row, not a ragged one"


def test_feature_matrix_identical_rows_score_higher_than_unrelated_ones():
    pairs = [(3, 4, 0.5), (1, 3, 0.5)]  # identical pair, then unrelated pair
    m = boost.extract_feature_matrix(pairs, _table(), ["name", "city"])
    assert m[0][1:].sum() > m[1][1:].sum()


# -- sampling ----------------------------------------------------------------


def test_sample_initial_returns_everything_when_under_the_budget():
    pairs = [(i, i + 1, 0.5) for i in range(10)]
    assert boost._sample_initial_pairs(pairs, n=100) == list(range(10))


def test_sample_initial_is_deterministic_and_within_budget():
    """The rng is seeded (42), so two calls must agree -- an unstable sample
    makes labelling cost and results irreproducible run to run."""
    pairs = [(i, i + 1, i / 500.0) for i in range(500)]
    a = sorted(boost._sample_initial_pairs(pairs, n=100))
    b = sorted(boost._sample_initial_pairs(pairs, n=100))
    assert a == b
    assert len(a) <= 100
    assert all(0 <= i < 500 for i in a)


def test_sample_initial_reaches_both_extremes():
    """30% high / 30% low is the documented split; sampling only the middle
    would starve the classifier of clear positives and negatives."""
    pairs = [(i, i + 1, i / 500.0) for i in range(500)]
    idx = boost._sample_initial_pairs(pairs, n=100)
    scores = [pairs[i][2] for i in idx]
    assert min(scores) < 0.1
    assert max(scores) > 0.9


def test_sample_uncertain_prefers_probabilities_nearest_a_half():
    probs = np.array([0.01, 0.49, 0.98, 0.52])
    got = boost._sample_uncertain_pairs(probs, labeled_indices=set(), n=2)
    assert set(got) == {1, 3}


def test_sample_uncertain_never_returns_an_already_labeled_pair():
    probs = np.array([0.50, 0.51, 0.99])
    got = boost._sample_uncertain_pairs(probs, labeled_indices={0}, n=2)
    assert 0 not in got


# -- model persistence -------------------------------------------------------


class _StubModel:
    """Duck-types the three attributes save_model reads off a LogisticRegression."""

    def __init__(self):
        self.coef_ = np.array([[0.5, -0.25]])
        self.intercept_ = np.array([0.1])
        self.classes_ = np.array([0, 1])


def test_column_hash_ignores_column_order():
    assert boost._column_hash(["b", "a"]) == boost._column_hash(["a", "b"])


def test_column_hash_changes_with_the_column_set():
    assert boost._column_hash(["a", "b"]) != boost._column_hash(["a", "c"])


def test_save_model_writes_json_carrying_the_column_hash(tmp_path):
    boost.save_model(_StubModel(), ["name", "city"], directory=tmp_path)
    data = json.loads((tmp_path / boost.MODEL_FILE).read_text())
    assert data["columns"] == ["name", "city"]
    assert data["column_hash"] == boost._column_hash(["name", "city"])
    assert data["coef"] == [[0.5, -0.25]]


def test_load_model_returns_none_when_absent(tmp_path):
    assert boost.load_model(["name"], directory=tmp_path) is None


def test_load_model_returns_none_on_corrupt_json(tmp_path):
    (tmp_path / boost.MODEL_FILE).write_text("{not json")
    assert boost.load_model(["name"], directory=tmp_path) is None


def test_load_model_refuses_a_model_trained_on_different_columns(tmp_path):
    """The guard that stops a model trained on other columns being applied: it
    would score against features it never saw."""
    boost.save_model(_StubModel(), ["name", "city"], directory=tmp_path)
    assert boost.load_model(["name", "postcode"], directory=tmp_path) is None


# -- fine-tune helpers -------------------------------------------------------


def test_training_texts_join_columns_and_skip_nulls():
    rows = [{"name": "Ann", "city": None}, {"name": "Bo", "city": "Leeds"}]
    assert boost._build_training_texts(rows, ["name", "city"]) == [
        "name: Ann",
        "name: Bo | city: Leeds",
    ]


def test_training_texts_empty_row_is_empty_string():
    assert boost._build_training_texts([{}], ["name"]) == [""]


def test_load_finetuned_returns_none_without_a_model_dir(tmp_path):
    got = boost.load_finetuned_and_rescore(
        [(1, 2, 0.9)], _table(), ["name"], model_dir=tmp_path / "nope"
    )
    assert got is None


# -- boost_accuracy routing --------------------------------------------------


def test_boost_accuracy_returns_empty_input_untouched():
    assert boost.boost_accuracy([], _table(), ["name"]) == []


def test_boost_accuracy_uses_a_saved_finetuned_model_without_any_llm(monkeypatch):
    sentinel = [(1, 2, 0.99)]
    monkeypatch.setattr(boost, "load_finetuned_and_rescore", lambda *a, **k: sentinel)
    monkeypatch.setattr(
        boost, "detect_provider", lambda: pytest.fail("must not reach the LLM path")
    )
    assert boost.boost_accuracy([(1, 2, 0.5)], _table(), ["name"]) is sentinel


def test_boost_accuracy_rescores_from_a_saved_classifier(monkeypatch):
    """The documented no-LLM-calls path: a saved model maps onto the pairs in
    order, and the returned score is the model probability, not the original."""

    class _Proba:
        def predict_proba(self, feats):
            n = len(feats)
            return np.column_stack([np.zeros(n), np.linspace(0.1, 0.9, n)])

    monkeypatch.setattr(boost, "load_finetuned_and_rescore", lambda *a, **k: None)
    monkeypatch.setattr(boost, "load_model", lambda *a, **k: _Proba())
    monkeypatch.setattr(
        boost, "detect_provider", lambda: pytest.fail("must not reach the LLM path")
    )

    pairs = [(1, 2, 0.5), (3, 4, 0.5)]
    out = boost.boost_accuracy(pairs, _table(), ["name", "city"])
    assert [(a, b) for a, b, _ in out] == [(1, 2), (3, 4)]
    assert [s for *_, s in out] == pytest.approx([0.1, 0.9])


def test_boost_accuracy_skips_double_underscore_columns(monkeypatch):
    """`__row_id__` is bookkeeping, not a matchable field; scoring on it would
    add a feature that is trivially unequal on every real pair."""
    seen = {}

    def _capture(pairs, df, columns, **kw):
        seen["columns"] = columns
        # Return a result so the call short-circuits here. Returning None would
        # fall through to the sklearn import, which is absent in CI -- the test
        # would then die on an ImportError before reaching its assertion.
        return [(1, 2, 0.99)]

    monkeypatch.setattr(boost, "load_finetuned_and_rescore", _capture)

    boost.boost_accuracy([(1, 2, 0.5)], _table(), ["name", "__row_id__", "city"])
    assert seen["columns"] == ["name", "city"]


def test_boost_accuracy_returns_pairs_unchanged_when_no_api_key(monkeypatch):
    """No credentials is a normal state, not an error: the run continues with
    the original scores rather than raising at the user."""
    pytest.importorskip(
        "sklearn", reason="the no-key branch sits after the sklearn import"
    )
    monkeypatch.setattr(boost, "load_finetuned_and_rescore", lambda *a, **k: None)
    monkeypatch.setattr(boost, "load_model", lambda *a, **k: None)
    monkeypatch.setattr(boost, "detect_provider", lambda: None)

    pairs = [(1, 2, 0.5)]
    assert boost.boost_accuracy(pairs, _table(), ["name"]) == pairs


def test_the_extra_named_on_missing_sklearn_actually_provides_sklearn(monkeypatch):
    """The error message and the packaging metadata must agree.

    They did not. `boost_accuracy` told users to `pip install goldenmatch[llm]`,
    but that extra was anthropic + openai only -- scikit-learn reached the tree
    solely via sentence-transformers ([embeddings]) -- so following the
    instruction produced the identical error a second time. `[llm]` now declares
    scikit-learn.

    This asserts the RELATIONSHIP rather than either side's wording, so it stays
    honest if the message is reworded or the dependency is moved to a different
    extra: whichever extra the message names has to be the one that supplies it.
    """
    import builtins
    import re
    import tomllib
    from pathlib import Path

    real_import = builtins.__import__

    def _no_sklearn(name, *args, **kwargs):
        if name.startswith("sklearn"):
            raise ImportError("No module named 'sklearn'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(boost, "load_finetuned_and_rescore", lambda *a, **k: None)
    monkeypatch.setattr(boost, "load_model", lambda *a, **k: None)
    monkeypatch.setattr(builtins, "__import__", _no_sklearn)

    with pytest.raises(ImportError) as exc:
        boost.boost_accuracy([(1, 2, 0.5)], _table(), ["name"])

    msg = str(exc.value)
    assert "scikit-learn" in msg, "the message must name the missing package"

    named = re.search(r"goldenmatch\[([a-z0-9,\-]+)\]", msg)
    assert named, f"the message must point at an installable extra: {msg!r}"

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    for extra in named.group(1).split(","):
        assert extra in extras, f"the message names a nonexistent extra: {extra}"
        assert any(d.startswith("scikit-learn") for d in extras[extra]), (
            f"goldenmatch[{extra}] does not provide scikit-learn; following this "
            f"message returns the user to the same error"
        )
