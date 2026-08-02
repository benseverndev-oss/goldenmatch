"""Tests for the Layer-2 field-attribution helpers (model-free, box-safe)."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from interp.field_attribution import (  # noqa: E402
    attribute_direction,
    field_agreements,
    label_sae_features,
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
