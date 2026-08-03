"""Tests for the Layer-2 field-attribution helpers (model-free, box-safe)."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from interp.field_attribution import (  # noqa: E402
    affine_r2,
    attribute_direction,
    field_agreements,
    fixed_weight_score,
    label_sae_features,
    record_disjoint_split,
    richer_field_features,
)

FIELDS = ["first_name", "surname", "dob"]


def _rows():
    return {
        0: {"first_name": "John", "surname": "Smith", "dob": "1990-01-01"},
        1: {"first_name": "John", "surname": "Smith", "dob": "1990-01-01"},  # exact match to 0
        2: {"first_name": "Jane", "surname": "Smith", "dob": "1975-12-30"},  # shares surname only
        3: {"first_name": "Jon", "surname": "Smyth", "dob": "1990-01-02"},  # fuzzy-close to 0
    }


class TestFieldAgreements:
    def test_exact_match_is_one(self):
        fa = field_agreements(_rows(), [(0, 1, 1)], FIELDS)
        assert fa.shape == (1, 3)
        assert np.allclose(fa[0], 1.0)

    def test_shared_surname_only(self):
        fa = field_agreements(_rows(), [(0, 2, 0)], FIELDS)[0]
        assert fa[1] == 1.0  # surname agrees exactly
        assert fa[0] < 0.8  # John vs Jane
        assert fa[2] < 0.9  # different dob

    def test_fuzzy_between_zero_and_one(self):
        fa = field_agreements(_rows(), [(0, 3, 1)], FIELDS)[0]
        assert all(0.0 <= v <= 1.0 for v in fa)
        assert fa[0] > 0.7  # John vs Jon are close
        assert fa[1] > 0.7  # Smith vs Smyth are close

    def test_missing_side_is_zero(self):
        rows = {0: {"first_name": "John", "surname": "", "dob": "1990"}, 1: {"first_name": "John"}}
        fa = field_agreements(rows, [(0, 1, 1)], FIELDS)[0]
        assert fa[0] == 1.0
        assert fa[1] == 0.0  # surname missing on both/one side
        assert fa[2] == 0.0  # dob missing on side 1


class TestAttributeDirection:
    def test_recovers_the_driving_field(self):
        # projection is built to depend ONLY on field 1 (surname) -> it must rank first
        rng = np.random.default_rng(0)
        X = rng.random((200, 3))
        proj = 5.0 * X[:, 1] + 0.01 * rng.standard_normal(200)
        res = attribute_direction(proj, X, FIELDS)
        assert res["top_field"] == "surname"
        assert res["r2"] > 0.9
        assert abs(res["coefficients"]["surname"]) > abs(res["coefficients"]["first_name"])
        assert abs(res["coefficients"]["surname"]) > abs(res["coefficients"]["dob"])

    def test_low_r2_when_fields_dont_explain(self):
        rng = np.random.default_rng(1)
        X = rng.random((200, 3))
        proj = rng.standard_normal(200)  # unrelated to the fields
        res = attribute_direction(proj, X, FIELDS)
        assert res["r2"] < 0.2

    def test_shape_validation(self):
        import pytest

        with pytest.raises(ValueError):
            attribute_direction(np.zeros(5), np.zeros((4, 3)), FIELDS)
        with pytest.raises(ValueError):
            attribute_direction(np.zeros(4), np.zeros((4, 2)), FIELDS)


class TestRecordDisjointSplit:
    def test_no_record_appears_on_both_sides(self):
        pairs = [(i, i + 1, i % 2) for i in range(0, 200, 2)]
        tr, te = record_disjoint_split(pairs, seed=0)
        tr_recs = {r for i in tr for r in pairs[i][:2]}
        te_recs = {r for i in te for r in pairs[i][:2]}
        assert tr_recs and te_recs
        assert not (tr_recs & te_recs)

    def test_indices_are_disjoint_and_in_range(self):
        pairs = [(i, i + 1, 1) for i in range(0, 100, 2)]
        tr, te = record_disjoint_split(pairs, seed=3)
        assert not (set(tr) & set(te))
        assert all(0 <= i < len(pairs) for i in tr + te)

    def test_deterministic(self):
        pairs = [(i, i + 1, 1) for i in range(0, 60, 2)]
        assert record_disjoint_split(pairs, seed=7) == record_disjoint_split(pairs, seed=7)

    def test_does_not_guarantee_entity_disjointness(self):
        # THE point of this helper: records 0 and 2 are the same entity, but
        # nothing stops them landing on opposite sides -- that is the leak a
        # cluster-disjoint split closes.
        pairs = [(0, 1, 1), (2, 3, 1)]
        seen_opposite = False
        for s in range(40):
            tr, te = record_disjoint_split(pairs, seed=s)
            if tr and te:
                seen_opposite = True
                break
        assert seen_opposite


class TestRicherFieldFeatures:
    def test_shape_and_names(self):
        X, names = richer_field_features(_rows(), [(0, 1, 1), (0, 2, 0)], FIELDS)
        assert X.shape == (2, 6 * len(FIELDS))
        assert len(names) == 6 * len(FIELDS)
        assert names[:2] == ["first_name__agreement", "first_name__exact"]

    def test_exact_pair_sets_exact_and_zero_edit(self):
        X, names = richer_field_features(_rows(), [(0, 1, 1)], FIELDS)
        idx = {n: j for j, n in enumerate(names)}
        assert X[0, idx["surname__exact"]] == 1.0
        assert X[0, idx["surname__agreement"]] == 1.0
        assert X[0, idx["surname__edit_norm"]] == 0.0
        assert X[0, idx["surname__missing"]] == 0.0
        assert X[0, idx["surname__conflict"]] == 0.0

    def test_missing_is_distinguishable_from_conflict(self):
        rows = {
            0: {"first_name": "John", "surname": "Smith", "dob": "1990-01-01"},
            1: {"first_name": "", "surname": "Zzzzzz", "dob": "1990-01-01"},
        }
        X, names = richer_field_features(rows, [(0, 1, 0)], FIELDS)
        idx = {n: j for j, n in enumerate(names)}
        # first_name absent on one side -> missing, NOT conflict
        assert X[0, idx["first_name__missing"]] == 1.0
        assert X[0, idx["first_name__conflict"]] == 0.0
        # surname present on both but disagreeing -> conflict, NOT missing
        assert X[0, idx["surname__missing"]] == 0.0
        assert X[0, idx["surname__conflict"]] == 1.0

    def test_features_are_bounded(self):
        X, _ = richer_field_features(_rows(), [(0, 3, 1), (0, 2, 0), (2, 3, 0)], FIELDS)
        assert X.min() >= 0.0
        assert X.max() <= 1.0


class TestFixedWeightScore:
    def test_weights_are_applied_not_refit(self):
        X = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        s = fixed_weight_score(X, FIELDS, {"first_name": 0.42, "surname": 0.04, "dob": 0.01})
        assert np.allclose(s, [0.42, 0.04])

    def test_unknown_field_contributes_zero(self):
        X = np.ones((1, 3))
        s = fixed_weight_score(X, FIELDS, {"first_name": 1.0})
        assert s[0] == 1.0

    def test_shape_validation(self):
        import pytest

        with pytest.raises(ValueError):
            fixed_weight_score(np.zeros((4, 2)), FIELDS, {})


class TestAffineR2:
    def test_perfect_affine_relation_scores_one(self):
        s = np.linspace(0, 1, 50)
        y = 3.0 * s - 1.0
        res = affine_r2(s, y, s, y)
        assert res["r2_test"] > 0.999
        assert abs(res["slope"] - 3.0) < 1e-6
        assert abs(res["intercept"] + 1.0) < 1e-6

    def test_scale_and_offset_are_free_but_shape_is_not(self):
        # a frozen score that is monotone-but-nonlinear in y cannot reach 1.0
        rng = np.random.default_rng(0)
        s = rng.random(400)
        y = s**3
        res = affine_r2(s, y, s, y)
        assert 0.6 < res["r2_test"] < 0.98

    def test_unrelated_score_scores_near_zero(self):
        rng = np.random.default_rng(1)
        s_tr, y_tr = rng.random(300), rng.random(300)
        s_te, y_te = rng.random(300), rng.random(300)
        assert affine_r2(s_tr, y_tr, s_te, y_te)["r2_test"] < 0.1

    def test_held_out_can_be_worse_than_train(self):
        rng = np.random.default_rng(2)
        s_tr, y_tr = rng.random(30), rng.random(30)
        s_te, y_te = rng.random(300), rng.random(300)
        res = affine_r2(s_tr, y_tr, s_te, y_te)
        assert res["r2_test"] <= res["r2_train"]

    def test_shape_validation(self):
        import pytest

        with pytest.raises(ValueError):
            affine_r2(np.zeros(5), np.zeros(4), np.zeros(3), np.zeros(3))


class TestLabelSaeFeatures:
    def test_labels_feature_by_correlated_field(self):
        rng = np.random.default_rng(2)
        X = rng.random((300, 3))
        # feature 0 tracks dob agreement; feature 1 tracks first_name agreement
        feats = np.stack([
            3.0 * X[:, 2] + 0.01 * rng.standard_normal(300),
            3.0 * X[:, 0] + 0.01 * rng.standard_normal(300),
        ], axis=1)
        labels = label_sae_features(feats, X, FIELDS)
        assert labels[0]["top_field"] == "dob"
        assert labels[1]["top_field"] == "first_name"
        assert abs(labels[0]["corr"]) > 0.8

    def test_dead_feature_is_safe(self):
        X = np.random.default_rng(3).random((50, 3))
        feats = np.zeros((50, 1))  # never fires
        labels = label_sae_features(feats, X, FIELDS)
        assert labels[0]["top_field"] is None
        assert labels[0]["corr"] == 0.0
