"""End-to-end EM test for N-level probabilistic fields (Task 5).

Proves ``train_em`` (and the ``estimate_m_from_labels`` supervised sibling)
work correctly when a field declares more than the legacy 2/3 levels --
the N-level banding (Task 2), fallback/neutral-u generalization (Task 3),
and native guards (Task 4) are each unit-tested elsewhere; this file exercises
the full EM loop on a realistic ~200-row dataset with a 4-level field.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import EMResult, estimate_m_from_labels, train_em

# ── Synthetic ~200-row dataset with known duplicates ───────────────────────
#
# 100 unique "persons", each appearing twice (200 rows total): once as the
# original record and once as a deliberately-corrupted duplicate. The
# corruption strategy cycles through 4 buckets so the observed first_name
# similarity spans all 4 comparison levels of the jaro_winkler field below
# (level_thresholds=[1.0, 0.92, 0.85] on a 4-level field):
#   grp 0: no corruption            -> similarity 1.0    -> level 3 (top)
#   grp 1: transpose two mid chars  -> similarity ~0.97-0.98 -> level 2
#   grp 2: swap first two + delete  -> similarity ~0.89-0.94 -> level 1/2
#   grp 3: reverse the whole string -> similarity ~0.6-0.8   -> level 0
# (measured with goldenmatch.core.scorer.score_field; see test_nlevel_banding
# .py for the same jaro_winkler-similarity-measurement pattern).
#
# last_name uses a 2-level exact scorer and is assigned round-robin from a
# pool of surnames chosen to hit DIFFERENT soundex codes (project rule:
# clustered-soundex fixtures can hang blocking). This test doesn't block on
# last_name, but the fixture stays blocking-safe for reuse.

_FIRST_NAMES = [
    "michael", "jennifer", "christopher", "elizabeth", "alexander",
    "samantha", "nicholas", "stephanie", "jonathan", "patricia",
    "benjamin", "victoria", "sebastian", "katherine", "nathaniel",
    "gabriella", "theodore", "madeline", "maximilian", "anastasia",
    "frederick", "henrietta", "montgomery", "wilhelmina", "bartholomew",
    "constance", "archibald", "geraldine", "cornelius", "evangeline",
    "humphrey", "beatrice", "reginald", "marguerite", "ferdinand",
    "philippa", "leopold", "rosalind", "desmond", "clementine",
]

# Surnames spanning distinct soundex codes (S530, O200, N250, K422, F362,
# B654, D426, U536, Q535, V232, X150, Y553, Z200, A165, B422, C234, D653,
# E625, F421, G363) -- deliberately NOT clustered on one code.
_LAST_NAMES = [
    "smith", "ozawa", "nguyen", "kowalski", "fitzgerald",
    "bramhall", "delacroix", "underwood", "quintanilla", "vasquez",
    "xiong", "yamamoto", "zwicky", "abernathy", "blackwood",
    "castellano", "drummond", "eriksson", "falkenberg", "gutierrez",
]


def _transpose_mid(s: str) -> str:
    i = len(s) // 2
    chars = list(s)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def _swap_and_delete(s: str) -> str:
    chars = list(s)
    chars[0], chars[1] = chars[1], chars[0]
    del chars[len(chars) // 2]
    return "".join(chars)


def _reverse(s: str) -> str:
    return s[::-1]


_CORRUPTORS = [lambda s: s, _transpose_mid, _swap_and_delete, _reverse]


def _make_dedupe_df(n_persons: int = 100) -> pl.DataFrame:
    """~200-row DataFrame: n_persons originals + n_persons duplicates."""
    row_ids: list[int] = []
    first_names: list[str] = []
    last_names: list[str] = []

    row_id = 1
    for i in range(n_persons):
        base = _FIRST_NAMES[i % len(_FIRST_NAMES)]
        last = _LAST_NAMES[i % len(_LAST_NAMES)]
        corrupt = _CORRUPTORS[i % len(_CORRUPTORS)]

        # original
        row_ids.append(row_id)
        first_names.append(base)
        last_names.append(last)
        row_id += 1

        # duplicate (same last name, corrupted first name)
        row_ids.append(row_id)
        first_names.append(corrupt(base))
        last_names.append(last)
        row_id += 1

    return pl.DataFrame({
        "__row_id__": row_ids,
        "first_name": first_names,
        "last_name": last_names,
    })


def _make_nlevel_mk(**kwargs) -> MatchkeyConfig:
    defaults = dict(
        name="fs_nlevel",
        type="probabilistic",
        fields=[
            MatchkeyField(
                field="first_name", scorer="jaro_winkler", levels=4,
                level_thresholds=[1.0, 0.92, 0.85],
            ),
            MatchkeyField(field="last_name", scorer="exact", levels=2),
        ],
    )
    defaults.update(kwargs)
    return MatchkeyConfig(**defaults)


class TestNLevelEMEndToEnd:
    def test_train_em_returns_valid_4level_result(self, monkeypatch):
        # Force isotonic repair so the monotone assertion below is
        # deterministic regardless of which side of a rare EM inversion
        # this particular fixture happens to land on (see
        # enforce_weight_monotonicity / GOLDENMATCH_FS_MONOTONIC).
        monkeypatch.setenv("GOLDENMATCH_FS_MONOTONIC", "enforce")

        df = _make_dedupe_df()
        mk = _make_nlevel_mk()
        result = train_em(df, mk, n_sample_pairs=5000, max_iterations=50)

        assert isinstance(result, EMResult)

        jw_field = "first_name"
        assert len(result.m_probs[jw_field]) == 4
        assert len(result.u_probs[jw_field]) == 4

        # Every m/u list sums to ~1.0 for both fields.
        for field_name, probs in result.m_probs.items():
            assert abs(sum(probs) - 1.0) < 1e-6, f"m_probs[{field_name}] doesn't sum to 1"
        for field_name, probs in result.u_probs.items():
            assert abs(sum(probs) - 1.0) < 1e-6, f"u_probs[{field_name}] doesn't sum to 1"

        # match_weights per field are monotone non-decreasing across levels.
        for field_name, weights in result.match_weights.items():
            for a, b in zip(weights, weights[1:]):
                assert a <= b + 1e-9, (
                    f"match_weights[{field_name}] not non-decreasing: {weights}"
                )

        # Discriminative: matches concentrate mass at high levels, non-matches
        # at low. Shape/monotone asserts alone wouldn't catch a level-collapse
        # regression -- the fixture deliberately populates all 4 levels.
        assert result.m_probs["first_name"][3] > result.m_probs["first_name"][0]
        assert result.u_probs["first_name"][0] > result.u_probs["first_name"][3]

    def test_blocking_fields_neutral_u_4level(self):
        """N>3 neutral-u branch (Task 3) exercised directly via train_em."""
        df = _make_dedupe_df()
        mk = _make_nlevel_mk()
        result = train_em(
            df, mk, n_sample_pairs=5000, max_iterations=20,
            blocking_fields=["first_name"],
        )
        assert result.u_probs["first_name"] == [0.25, 0.25, 0.25, 0.25]

    def test_estimate_m_from_labels_neutral_u_5level(self):
        """N>3 neutral-u branch in estimate_m_from_labels (its own code path,
        not shared with train_em -- see probabilistic.py ~847)."""
        mk = MatchkeyConfig(
            name="fs_5level",
            type="probabilistic",
            fields=[
                MatchkeyField(
                    field="code", scorer="jaro_winkler", levels=5,
                    level_thresholds=[1.0, 0.9, 0.8, 0.7],
                ),
            ],
        )
        df = pl.DataFrame({
            "__row_id__": [1, 2, 3, 4, 5],
            "code": ["alpha", "alpha", "beta", "gamma", "delta"],
        })
        labels = [(1, 2)]
        result = estimate_m_from_labels(
            df, mk, labels, blocking_fields=["code"], n_sample_pairs=50,
        )
        assert result.u_probs["code"] == [0.2, 0.2, 0.2, 0.2, 0.2]


class TestNLevelEMSmallData:
    def test_train_em_handles_5level_field(self):
        """Sanity check at N=5 beyond the primary N=4 fixture above."""
        df = pl.DataFrame({
            "__row_id__": [1, 2, 3, 4, 5, 6],
            "code": ["alpha", "alpha", "beta", "beta", "gamma", "delta"],
        })
        mk = MatchkeyConfig(
            name="fs_5level",
            type="probabilistic",
            fields=[
                MatchkeyField(
                    field="code", scorer="jaro_winkler", levels=5,
                    level_thresholds=[1.0, 0.9, 0.8, 0.7],
                ),
            ],
        )
        result = train_em(df, mk, n_sample_pairs=100, max_iterations=20)
        assert isinstance(result, EMResult)
        assert len(result.m_probs["code"]) == 5
        assert len(result.u_probs["code"]) == 5
        assert abs(sum(result.m_probs["code"]) - 1.0) < 1e-6
        assert abs(sum(result.u_probs["code"]) - 1.0) < 1e-6
