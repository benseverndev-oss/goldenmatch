"""Task N2: EM-learned negative-evidence dimensions in the FS trainer.

Spec: docs/superpowers/specs/2026-07-14-fs-negative-evidence-design.md
Plan: docs/superpowers/plans/2026-07-14-fs-negative-evidence.md (Task N2)

Fixture rule (feedback_synthetic_surname_fixtures): surnames are spread
across distinct values so blocking-style within-group sampling doesn't hang
or degenerate on a single soundex code.
"""
from __future__ import annotations

import logging

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField, NegativeEvidenceField
from goldenmatch.core.blocker import BlockResult
from goldenmatch.core.probabilistic import (
    _fallback_result,
    _ne_fired,
    enforce_weight_monotonicity,
    train_em,
)

# ── Fixture helpers ─────────────────────────────────────────────────────────

# Distinct surnames spread across different soundex codes, per the synthetic-
# fixture rule -- avoids any blocking-adjacent hang/degeneracy in this test.
_SURNAMES = [
    "Nguyen", "Okafor", "Petrov", "Alvarez", "Kowalski",
    "Haddad", "Mendez", "Tanaka", "Larsen", "Fitzgerald",
]
_FIRST_NAMES = [
    "Alex", "Priya", "Marco", "Fatima", "Liam",
    "Sofia", "Ken", "Nadia", "Omar", "Grace",
]
_ZIPS = ["10001", "20002", "30303", "40404", "50505"]


def _phone(seed: int) -> str:
    """A digits-only-stable 10-digit phone string derived from seed."""
    return f"{2000000000 + seed}"


def _make_ne_mk(**ne_kwargs) -> MatchkeyConfig:
    """Probabilistic matchkey on name+last_name+zip with phone as NE."""
    ne = NegativeEvidenceField(
        field="phone", transforms=["digits_only"], scorer="exact",
        threshold=1.0, **ne_kwargs,
    )
    return MatchkeyConfig(
        name="fs_ne",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=2),
            MatchkeyField(field="last_name", scorer="exact", levels=2),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
        negative_evidence=[ne],
    )


def _make_homonym_fixture():
    """~200 rows: planted true-duplicate pairs (name+last+zip+phone all
    agree) + cross-entity homonym traps (name+last+zip agree, phone
    DIFFERS) + a diverse background of singleton rows.

    Returns (df, blocks) where ``blocks`` groups exactly the 40 duplicate
    pairs + 10 homonym-trap pairs -- the m-estimation sample train_em uses
    when ``blocks`` is provided. Random pairs (used for u) are drawn from
    the full 200-row df, so u reflects the background population where
    NE-field (phone) firing is common (distinct people, distinct phones).
    """
    rows: list[dict] = []
    blocks: list[BlockResult] = []
    row_id = 1

    # 40 true-duplicate pairs: agree on everything, including phone.
    for i in range(40):
        fn = _FIRST_NAMES[i % len(_FIRST_NAMES)]
        ln = _SURNAMES[i % len(_SURNAMES)]
        zip_ = _ZIPS[i % len(_ZIPS)]
        phone = _phone(1_000_000 + i)
        pair_ids = []
        for _ in range(2):
            rows.append({
                "__row_id__": row_id, "first_name": fn, "last_name": ln,
                "zip": zip_, "phone": phone,
            })
            pair_ids.append(row_id)
            row_id += 1
        blocks.append(BlockResult(
            block_key=f"dup-{i}",
            df=pl.DataFrame({"__row_id__": pair_ids}),
        ))

    # 10 homonym traps: agree on name/last/zip, DIFFERENT phone.
    for i in range(10):
        fn = _FIRST_NAMES[i % len(_FIRST_NAMES)]
        ln = _SURNAMES[(i + 3) % len(_SURNAMES)]
        zip_ = _ZIPS[(i + 1) % len(_ZIPS)]
        pair_ids = []
        for k in range(2):
            rows.append({
                "__row_id__": row_id, "first_name": fn, "last_name": ln,
                "zip": zip_, "phone": _phone(2_000_000 + i * 10 + k),
            })
            pair_ids.append(row_id)
            row_id += 1
        blocks.append(BlockResult(
            block_key=f"homonym-{i}",
            df=pl.DataFrame({"__row_id__": pair_ids}),
        ))

    # 100 diverse background singletons -- distinct name/last/zip/phone
    # combinations so random-pair sampling sees a realistic non-match
    # population (phone almost never coincidentally matches -> NE fires).
    for i in range(100):
        rows.append({
            "__row_id__": row_id,
            "first_name": _FIRST_NAMES[(i * 7) % len(_FIRST_NAMES)],
            "last_name": _SURNAMES[(i * 3 + 1) % len(_SURNAMES)],
            "zip": _ZIPS[(i * 5 + 2) % len(_ZIPS)],
            "phone": _phone(3_000_000 + i),
        })
        row_id += 1

    df = pl.DataFrame(rows)
    return df, blocks


# ── 1. _ne_fired unit tests ─────────────────────────────────────────────────


def _phone_ne(threshold: float = 1.0, transforms: list[str] | None = None) -> NegativeEvidenceField:
    return NegativeEvidenceField(
        field="phone",
        transforms=transforms if transforms is not None else ["digits_only"],
        scorer="exact",
        threshold=threshold,
    )


class TestNeFired:
    def test_fires_when_both_present_and_score_below_threshold(self):
        ne = _phone_ne(threshold=1.0)
        assert _ne_fired(
            {"phone": "555-1234"}, {"phone": "555-9999"}, ne,
        ) is True  # exact score 0.0 < 1.0

    def test_does_not_fire_at_exact_threshold_boundary(self):
        ne = _phone_ne(threshold=1.0)
        # Same phone -> exact score 1.0, NOT < 1.0 (strict).
        assert _ne_fired(
            {"phone": "555-1234"}, {"phone": "555-1234"}, ne,
        ) is False

    def test_does_not_fire_on_null_either_side(self):
        ne = _phone_ne(threshold=1.0)
        assert _ne_fired({"phone": None}, {"phone": "555-1234"}, ne) is False
        assert _ne_fired({"phone": "555-1234"}, {"phone": None}, ne) is False
        assert _ne_fired({}, {"phone": "555-1234"}, ne) is False

    def test_does_not_fire_on_empty_string_either_side(self):
        ne = _phone_ne(threshold=1.0)
        assert _ne_fired({"phone": ""}, {"phone": "555-1234"}, ne) is False

    def test_transforms_applied_before_scoring(self):
        # digits_only normalizes formatting differences away -> same digits
        # -> exact score 1.0 -> NOT fired despite different raw formatting.
        ne = _phone_ne(threshold=1.0, transforms=["digits_only"])
        assert _ne_fired(
            {"phone": "(555) 123-4567"}, {"phone": "555-123-4567"}, ne,
        ) is False


# ── 2. train_em on the homonym fixture ──────────────────────────────────────


class TestTrainEmNeDimensions:
    def test_ne_entries_present_and_well_formed(self):
        df, blocks = _make_homonym_fixture()
        mk = _make_ne_mk()
        result = train_em(
            df, mk, n_sample_pairs=2000, blocks=blocks,
            blocking_fields=["last_name", "zip"],
        )
        assert "__ne__phone" in result.m_probs
        assert "__ne__phone" in result.u_probs
        assert "__ne__phone" in result.match_weights

    def test_ne_m_and_u_sum_to_one(self):
        df, blocks = _make_homonym_fixture()
        mk = _make_ne_mk()
        result = train_em(
            df, mk, n_sample_pairs=2000, blocks=blocks,
            blocking_fields=["last_name", "zip"],
        )
        assert abs(sum(result.m_probs["__ne__phone"]) - 1.0) < 1e-6
        assert abs(sum(result.u_probs["__ne__phone"]) - 1.0) < 1e-6
        assert len(result.m_probs["__ne__phone"]) == 2
        assert len(result.u_probs["__ne__phone"]) == 2

    def test_not_fired_weight_is_exactly_zero(self):
        df, blocks = _make_homonym_fixture()
        mk = _make_ne_mk()
        result = train_em(
            df, mk, n_sample_pairs=2000, blocks=blocks,
            blocking_fields=["last_name", "zip"],
        )
        weights = result.match_weights["__ne__phone"]
        assert len(weights) == 2
        assert weights[1] == 0.0  # the negative-evidence clamp, not log2(m1/u1)

    def test_fired_weight_is_negative_on_this_fixture(self):
        # True duplicates dominate the "match" sample and mostly agree on
        # phone (not fired), while random background pairs fire often
        # (distinct people, distinct phones) -> fired is rarer in matches.
        df, blocks = _make_homonym_fixture()
        mk = _make_ne_mk()
        result = train_em(
            df, mk, n_sample_pairs=2000, blocks=blocks,
            blocking_fields=["last_name", "zip"],
        )
        assert result.match_weights["__ne__phone"][0] < 0.0


# ── 3. penalty_bits NE fields are excluded from EM entirely ─────────────────


class TestPenaltyBitsExcludedFromEm:
    def test_no_ne_entries_for_penalty_bits_field(self):
        df, blocks = _make_homonym_fixture()
        mk = _make_ne_mk(penalty_bits=3.0)
        result = train_em(
            df, mk, n_sample_pairs=2000, blocks=blocks,
            blocking_fields=["last_name", "zip"],
        )
        assert "__ne__phone" not in result.m_probs
        assert "__ne__phone" not in result.u_probs
        assert "__ne__phone" not in result.match_weights


# ── 4. NE field also a blocking field -> warning ─────────────────────────────


class TestBlockingOverlapWarning:
    def test_warns_when_ne_field_is_also_blocking_field(self, caplog):
        df, blocks = _make_homonym_fixture()
        mk = _make_ne_mk()
        with caplog.at_level(logging.WARNING):
            train_em(
                df, mk, n_sample_pairs=2000, blocks=blocks,
                blocking_fields=["phone"],
            )
        assert any(
            "phone" in rec.message and "blocking" in rec.message.lower()
            for rec in caplog.records
        )

    def test_no_warning_when_ne_field_not_a_blocking_field(self, caplog):
        df, blocks = _make_homonym_fixture()
        mk = _make_ne_mk()
        with caplog.at_level(logging.WARNING):
            train_em(
                df, mk, n_sample_pairs=2000, blocks=blocks,
                blocking_fields=["last_name", "zip"],
            )
        assert not any(
            "phone" in rec.message and "blocking field" in rec.message.lower()
            for rec in caplog.records
        )


# ── 5. Monotone repair leaves __ne__ entries alone ──────────────────────────


class TestMonotoneRepairSkipsNe:
    def test_enforce_mode_leaves_ne_entries_untouched(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_MONOTONIC", "enforce")
        df, blocks = _make_homonym_fixture()
        mk = _make_ne_mk()
        result = train_em(
            df, mk, n_sample_pairs=2000, blocks=blocks,
            blocking_fields=["last_name", "zip"],
        )
        # [w_fired, 0.0] with w_fired < 0 is already non-decreasing, so
        # "untouched" and "PAV would leave it alone anyway" coincide here;
        # the load-bearing assertion is the exact clamp value survives.
        assert result.match_weights["__ne__phone"][1] == 0.0

    def test_warn_mode_detection_never_names_ne_key(self):
        # Construct an EMResult by hand with a contrived positive w_fired
        # (a degenerate case that would look "non-monotonic" to a level-
        # ordered check) and call the repair helper directly -- skip_fields
        # must exclude __ne__ keys so no detection message names one.
        match_weights = {
            "first_name": [-1.0, 1.0],
            "__ne__phone": [2.5, 0.0],  # w_fired > 0: pathological but must
                                        # still be skipped, not "detected".
        }
        repaired, adjusted = enforce_weight_monotonicity(
            match_weights, skip_fields=["__ne__phone"],
        )
        assert "__ne__phone" not in adjusted
        assert repaired["__ne__phone"] == [2.5, 0.0]  # untouched


# ── 6. _fallback_result NE entries ──────────────────────────────────────────


class TestFallbackResultNe:
    def test_fallback_emits_fixed_ne_entry_with_warning(self, caplog):
        mk = _make_ne_mk()
        with caplog.at_level(logging.WARNING):
            result = _fallback_result(mk)
        assert result.match_weights["__ne__phone"] == [-3.0, 0.0]
        assert abs(sum(result.m_probs["__ne__phone"]) - 1.0) < 1e-9
        assert abs(sum(result.u_probs["__ne__phone"]) - 1.0) < 1e-9
        # log2(m0/u0) == -3.0 exactly given the chosen m/u pair.
        import math
        m0 = result.m_probs["__ne__phone"][0]
        u0 = result.u_probs["__ne__phone"][0]
        assert math.log2(m0 / u0) == pytest.approx(-3.0)
        assert any("negative-evidence" in rec.message.lower() for rec in caplog.records)

    def test_fallback_penalty_bits_field_absent(self):
        mk = _make_ne_mk(penalty_bits=3.0)
        result = _fallback_result(mk)
        assert "__ne__phone" not in result.match_weights

    def test_fallback_triggered_by_train_em_on_tiny_data(self):
        df = pl.DataFrame({
            "__row_id__": [1, 2],
            "first_name": ["Alex", "Alex"],
            "last_name": ["Nguyen", "Nguyen"],
            "zip": ["10001", "10001"],
            "phone": ["2000000001", "2000000001"],
        })
        mk = _make_ne_mk()
        result = train_em(df, mk, n_sample_pairs=10)
        assert result.match_weights["__ne__phone"] == [-3.0, 0.0]


# ── 7. Without-NE behavior is unchanged ──────────────────────────────────────
# Regular-field EM posteriors legitimately SHIFT when NE evidence enters the
# E-step (the NE dimension contributes real log-likelihood mass), so this is
# NOT pinned by a byte-identical with/without-NE comparison here. Instead,
# the existing (unmodified) test_probabilistic.py / test_nlevel_em.py /
# test_nlevel_banding.py suites stay green -- proving the no-NE code path
# (mk.negative_evidence is None/empty) is untouched. See the test run in the
# PR/commit description for confirmation those suites pass unmodified.
