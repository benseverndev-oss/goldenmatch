"""Tests for the Layer-2 field-attribution helpers (model-free, box-safe)."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from interp.field_attribution import (  # noqa: E402
    ablation_flip_profile,
    affine_r2,
    attribute_direction,
    attribution_summary,
    contribution_summary,
    corruption_matched_pairs,
    field_agreements,
    fixed_weight_score,
    label_sae_features,
    logit,
    pair_corruption,
    prob_space_r2,
    record_disjoint_split,
    richer_field_features,
    spearman,
    variance_decomposition,
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


class TestLogitLink:
    def test_saturated_probabilities_stay_finite(self):
        z = logit(np.array([0.0, 1.0, 0.5]))
        assert np.all(np.isfinite(z))
        assert z[0] < 0 < z[1]
        assert abs(z[2]) < 1e-9

    def test_clipping_is_symmetric_and_eps_controlled(self):
        tight, loose = logit(np.array([0.0]), eps=1e-6), logit(np.array([0.0]), eps=1e-1)
        assert tight[0] < loose[0] < 0

    def test_prob_space_r2_perfect_when_logits_are_exact(self):
        p = np.array([0.01, 0.2, 0.5, 0.8, 0.99])
        assert prob_space_r2(logit(p, eps=1e-9), p) > 0.999

    def test_prob_space_r2_is_zero_for_a_constant_prediction(self):
        p = np.array([0.1, 0.4, 0.6, 0.9])
        r2 = prob_space_r2(np.full(4, logit(np.array([p.mean()]))[0]), p)
        assert abs(r2) < 1e-6

    def test_logit_link_beats_linear_on_a_logistic_relation(self):
        # y really is sigmoid(affine(score)) -> the logit link should recover it
        # almost exactly while a straight-line fit cannot.
        s = np.linspace(-4, 4, 300)
        y = 1.0 / (1.0 + np.exp(-(1.5 * s - 0.3)))
        lin = affine_r2(s, y, s, y, link="linear")["r2_test"]
        log_ = affine_r2(s, y, s, y, link="logit")["r2_test"]
        assert log_ > 0.99
        assert log_ > lin

    def test_link_is_reported_and_validated(self):
        import pytest

        s = np.linspace(0.1, 0.9, 20)
        assert affine_r2(s, s, s, s, link="logit")["link"] == "logit"
        with pytest.raises(ValueError):
            affine_r2(s, s, s, s, link="probit")


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


class TestContributionSummary:
    def test_exact_decomposition_is_recognised(self):
        rng = np.random.default_rng(0)
        c = rng.standard_normal((50, 4))
        r = contribution_summary(c, list("abcd"), c.sum(axis=1))
        assert r["exact"] is True
        assert r["max_abs_err"] < 1e-9

    def test_a_missing_term_is_caught(self):
        # dropping a real component must fail the check, not degrade a metric
        rng = np.random.default_rng(1)
        c = rng.standard_normal((50, 4))
        actual = c.sum(axis=1) + rng.standard_normal(50)  # unexplained term
        r = contribution_summary(c, list("abcd"), actual)
        assert r["exact"] is False
        assert r["max_abs_err"] > 0.1

    def test_ranks_by_mean_absolute_contribution(self):
        c = np.zeros((10, 3))
        c[:, 0] = 0.1
        c[:, 1] = 5.0
        c[:, 2] = -2.0
        r = contribution_summary(c, ["small", "big", "mid"], c.sum(axis=1))
        assert [e["component"] for e in r["ranking"]] == ["big", "mid", "small"]
        assert r["ranking"][1]["mean"] < 0  # sign preserved separately from magnitude

    def test_concentration_detects_a_sparse_circuit(self):
        c = np.zeros((20, 10))
        c[:, 3] = 10.0  # one component carries essentially everything
        c[:, 7] = 0.01
        r = contribution_summary(c, [f"c{i}" for i in range(10)], c.sum(axis=1))
        assert r["concentration"]["top_1"] > 0.99
        assert r["n_for_90pct"] == 1

    def test_concentration_detects_a_dense_computation(self):
        c = np.full((20, 10), 1.0)
        r = contribution_summary(c, [f"c{i}" for i in range(10)], c.sum(axis=1))
        assert r["concentration"]["top_1"] == pytest.approx(0.1)
        assert r["n_for_90pct"] == 9

    def test_shape_validation(self):
        with pytest.raises(ValueError):
            contribution_summary(np.zeros((5, 3)), list("abc"), np.zeros(4))
        with pytest.raises(ValueError):
            contribution_summary(np.zeros((5, 3)), list("ab"), np.zeros(5))


class TestVarianceDecomposition:
    def test_shares_sum_to_one_exactly(self):
        rng = np.random.default_rng(0)
        c = rng.standard_normal((200, 5))
        r = variance_decomposition(c, list("abcde"), c.sum(axis=1))
        assert r["shares_sum"] == pytest.approx(1.0)
        assert r["shares_sum_exact"] is True

    def test_constant_offset_gets_zero_share(self):
        # THE reason this replaces the std ranking: a huge constant contributes
        # nothing to the decision and must score zero, not top.
        rng = np.random.default_rng(1)
        c = np.zeros((200, 3))
        c[:, 0] = 100.0                       # enormous, constant
        c[:, 1] = rng.standard_normal(200)    # the real signal
        c[:, 2] = 0.01 * rng.standard_normal(200)
        r = variance_decomposition(c, ["const", "signal", "tiny"], c.sum(axis=1))
        by = {e["component"]: e["var_share"] for e in r["ranking"]}
        assert by["const"] == pytest.approx(0.0, abs=1e-12)
        assert by["signal"] > 0.9
        assert r["ranking"][0]["component"] == "signal"

    def test_suppression_shows_as_negative_share(self):
        rng = np.random.default_rng(2)
        base = rng.standard_normal(200)
        c = np.stack([2.0 * base, -0.5 * base], axis=1)
        r = variance_decomposition(c, ["driver", "suppressor"], c.sum(axis=1))
        by = {e["component"]: e["var_share"] for e in r["ranking"]}
        assert by["suppressor"] < 0
        assert by["driver"] > 1.0  # exceeds 1 because the suppressor cancels part
        assert r["n_negative_share"] == 1

    def test_dense_computation_needs_many_components(self):
        rng = np.random.default_rng(3)
        c = rng.standard_normal((400, 20))
        r = variance_decomposition(c, [f"c{i}" for i in range(20)], c.sum(axis=1))
        assert r["n_for_90pct_variance"] > 10

    def test_sparse_computation_needs_few(self):
        rng = np.random.default_rng(4)
        c = 0.01 * rng.standard_normal((400, 20))
        c[:, 7] = rng.standard_normal(400)
        r = variance_decomposition(c, [f"c{i}" for i in range(20)], c.sum(axis=1))
        assert r["n_for_90pct_variance"] == 1
        assert r["ranking"][0]["component"] == "c7"

    def test_label_correlation_separates_correct_from_merely_active(self):
        rng = np.random.default_rng(5)
        y = rng.integers(0, 2, 300).astype(float)
        useful = y + 0.1 * rng.standard_normal(300)
        noise = rng.standard_normal(300)
        c = np.stack([useful, noise], axis=1)
        r = variance_decomposition(c, ["useful", "noise"], c.sum(axis=1), labels=y)
        by = {e["component"]: e for e in r["ranking"]}
        assert abs(by["useful"]["label_corr"]) > 0.9
        assert abs(by["noise"]["label_corr"]) < 0.3

    def test_rejects_constant_projection(self):
        with pytest.raises(ValueError):
            variance_decomposition(np.zeros((5, 2)), list("ab"), np.ones(5))

    def test_shape_validation(self):
        with pytest.raises(ValueError):
            variance_decomposition(np.zeros((5, 2)), list("ab"), np.zeros(4))


class TestAblationFlipProfile:
    COMBOS = [("a",), ("b",), ("c",), ("a", "b"), ("a", "c"), ("b", "c"), ("a", "b", "c")]

    def test_dense_redundancy_only_flips_at_high_order(self):
        # nothing flips at k=1 or k=2; only removing all three crosses 0.5
        base = np.array([0.9, 0.9])
        occ = np.array([
            [0.8, 0.8, 0.8, 0.7, 0.7, 0.7, 0.1],
            [0.8, 0.8, 0.8, 0.7, 0.7, 0.7, 0.2],
        ])
        r = ablation_flip_profile(base, self.COMBOS, occ)
        assert r["by_order"][1]["any_flip_rate"] == 0.0
        assert r["by_order"][2]["any_flip_rate"] == 0.0
        assert r["by_order"][3]["any_flip_rate"] == 1.0
        assert r["cumulative_flippable"][3] == 1.0
        assert r["min_flip_set_hist"] == {1: 0, 2: 0, 3: 2}

    def test_two_sparse_decision_is_detected(self):
        # no single field flips, but a pair does -> decomposable, not 1-sparse
        base = np.array([0.9])
        occ = np.array([[0.8, 0.8, 0.8, 0.2, 0.7, 0.7, 0.1]])
        r = ablation_flip_profile(base, self.COMBOS, occ)
        assert r["by_order"][1]["any_flip_rate"] == 0.0
        assert r["by_order"][2]["any_flip_rate"] == 1.0
        assert r["min_flip_set_hist"][2] == 1
        assert r["by_order"][2]["best_combo"] == ["a", "b"]

    def test_one_sparse_decision_counts_at_k1(self):
        base = np.array([0.9])
        occ = np.array([[0.1, 0.8, 0.8, 0.1, 0.1, 0.7, 0.05]])
        r = ablation_flip_profile(base, self.COMBOS, occ)
        assert r["by_order"][1]["any_flip_rate"] == 1.0
        assert r["min_flip_set_hist"][1] == 1  # credited to the SMALLEST set

    def test_never_flipped_is_reported_not_hidden(self):
        base = np.array([0.9, 0.9])
        occ = np.full((2, 7), 0.8)
        r = ablation_flip_profile(base, self.COMBOS, occ)
        assert r["never_flipped"] == 2
        assert r["never_flipped_frac"] == 1.0
        assert r["cumulative_flippable"][3] == 0.0

    def test_cumulative_is_monotone(self):
        rng = np.random.default_rng(0)
        base = rng.random(50)
        occ = rng.random((50, 7))
        r = ablation_flip_profile(base, self.COMBOS, occ)
        vals = [r["cumulative_flippable"][k] for k in sorted(r["cumulative_flippable"])]
        assert vals == sorted(vals)

    def test_shape_validation(self):
        with pytest.raises(ValueError):
            ablation_flip_profile(np.zeros(3), self.COMBOS, np.zeros((3, 2)))


class TestCorruptionMatching:
    def _corpus(self):
        # The probe confound in miniature: MOST non-matches are unrelated strings
        # (high corruption) while matches are near-copies (low corruption), so
        # overall string distance separates the classes. A minority of non-matches
        # sit at the SAME corruption as the matches -- those are the ones matching
        # should retain, leaving a set the shortcut cannot separate.
        rows, pairs = {}, []
        for k in range(30):
            base = f"aaaa{k:02d}"
            rows[4 * k] = {"first_name": base, "surname": base, "dob": base}
            rows[4 * k + 1] = {"first_name": base + "x", "surname": base,
                               "dob": base}            # near copy -> MATCH
            rows[4 * k + 2] = {"first_name": f"zzzz{k:02d}", "surname": f"yyyy{k:02d}",
                               "dob": f"wwww{k:02d}"}   # unrelated -> NON-match
            rows[4 * k + 3] = {"first_name": base + "y", "surname": base,
                               "dob": base}            # SAME corruption -> NON-match
            pairs.append((4 * k, 4 * k + 1, 1))
            pairs.append((4 * k, 4 * k + 2, 0))
            pairs.append((4 * k, 4 * k + 3, 0))
        return rows, pairs

    def test_confound_exists_before_matching(self):
        rows, pairs = self._corpus()
        c = pair_corruption(rows, pairs, FIELDS)
        y = np.array([t for *_, t in pairs])
        assert abs(np.corrcoef(c, y)[0, 1]) > 0.5  # the shortcut is real

    def test_matching_removes_the_confound(self):
        rows, pairs = self._corpus()
        sel, diag = corruption_matched_pairs(rows, pairs, FIELDS, tol=0.05)
        assert diag["n_out"] > 0
        assert abs(diag["corr_after"]) < abs(diag["corr_before"])
        assert abs(diag["corr_after"]) < 0.35
        # the unrelated (high-corruption) non-matches are exactly what gets dropped
        assert diag["n_out"] < diag["n_in"]

    def test_output_is_class_balanced_and_a_subset(self):
        rows, pairs = self._corpus()
        sel, _ = corruption_matched_pairs(rows, pairs, FIELDS, tol=0.05)
        labels = [t for *_, t in sel]
        assert labels.count(1) == labels.count(0)
        assert all(p in pairs for p in sel)

    def test_deterministic(self):
        rows, pairs = self._corpus()
        a, _ = corruption_matched_pairs(rows, pairs, FIELDS, tol=0.05)
        b, _ = corruption_matched_pairs(rows, pairs, FIELDS, tol=0.05)
        assert a == b

    def test_no_negative_reused(self):
        rows, pairs = self._corpus()
        sel, _ = corruption_matched_pairs(rows, pairs, FIELDS, tol=0.05)
        negs = [(a, b) for a, b, t in sel if t == 0]
        assert len(negs) == len(set(negs))

    def test_raises_without_both_classes(self):
        rows, pairs = self._corpus()
        only_pos = [p for p in pairs if p[2] == 1]
        with pytest.raises(ValueError):
            corruption_matched_pairs(rows, only_pos, FIELDS)


class TestSpearman:
    def test_perfect_and_inverted(self):
        a = np.array([1.0, 2.0, 3.0, 4.0])
        assert spearman(a, a) == pytest.approx(1.0)
        assert spearman(a, -a) == pytest.approx(-1.0)

    def test_monotone_but_nonlinear_is_still_one(self):
        a = np.array([1.0, 2.0, 3.0, 4.0])
        assert spearman(a, a**5) == pytest.approx(1.0)

    def test_constant_input_is_zero_not_nan(self):
        r = spearman(np.array([1.0, 2.0, 3.0]), np.array([7.0, 7.0, 7.0]))
        assert r == 0.0

    def test_ties_are_averaged(self):
        # without tie-averaging, a flat vector would fake a perfect correlation
        r = spearman(np.array([1.0, 1.0, 2.0, 2.0]), np.array([5.0, 5.0, 9.0, 9.0]))
        assert r == pytest.approx(1.0)


class TestAttributionSummary:
    def _case(self):
        # field 0 is load-bearing (removing it drops P a lot and flips verdicts),
        # field 2 is inert (removing it changes nothing).
        base = np.array([0.9, 0.8, 0.95, 0.85])
        occ = np.array([
            [0.1, 0.7, 0.9],
            [0.2, 0.6, 0.8],
            [0.05, 0.9, 0.95],
            [0.3, 0.75, 0.85],
        ])
        return base, occ

    def test_ranks_the_load_bearing_field_first(self):
        base, occ = self._case()
        res = attribution_summary(base, occ, FIELDS)
        assert res["ranking"][0] == "first_name"
        assert res["ranking"][-1] == "dob"

    def test_inert_field_has_zero_delta_and_no_flips(self):
        base, occ = self._case()
        by = {e["field"]: e for e in attribution_summary(base, occ, FIELDS)["per_field"]}
        assert by["dob"]["mean_abs_delta"] == pytest.approx(0.0)
        assert by["dob"]["flip_rate"] == 0.0

    def test_flip_rate_counts_verdict_crossings(self):
        base, occ = self._case()
        by = {e["field"]: e for e in attribution_summary(base, occ, FIELDS)["per_field"]}
        # every pair starts >= 0.5 and drops below when first_name is removed
        assert by["first_name"]["flip_rate"] == 1.0
        assert by["surname"]["flip_rate"] == 0.0
        assert attribution_summary(base, occ, FIELDS)["any_flip_rate"] == 1.0

    def test_signed_delta_shows_direction(self):
        # a field that pushes AWAY from match: removing it RAISES P
        base = np.array([0.4, 0.3])
        occ = np.array([[0.9, 0.4, 0.4], [0.8, 0.3, 0.3]])
        by = {e["field"]: e for e in attribution_summary(base, occ, FIELDS)["per_field"]}
        assert by["first_name"]["mean_delta"] < 0
        assert by["surname"]["mean_delta"] == pytest.approx(0.0)

    def test_spearman_against_learned_weights(self):
        base, occ = self._case()
        agree = attribution_summary(
            base, occ, FIELDS, weights={"first_name": 0.9, "surname": 0.3, "dob": 0.0}
        )
        assert agree["spearman_vs_learned_weights"] == pytest.approx(1.0)
        disagree = attribution_summary(
            base, occ, FIELDS, weights={"first_name": 0.0, "surname": 0.3, "dob": 0.9}
        )
        assert disagree["spearman_vs_learned_weights"] == pytest.approx(-1.0)

    def test_shape_validation(self):
        with pytest.raises(ValueError):
            attribution_summary(np.zeros(4), np.zeros((3, 3)), FIELDS)
        with pytest.raises(ValueError):
            attribution_summary(np.zeros(4), np.zeros((4, 2)), FIELDS)


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
