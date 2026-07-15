"""rule_precision_anchor_threshold_raise trigger-matrix tests (#1319).

The #1207 over-merge shape: the controller commits a config whose weighted
matchkey scores NAMES ONLY, and identical common full names over-merge
distinct people (crafted 2600-row fixture: P 0.009 at the committed 0.8
threshold). The rule raises the name-only weighted threshold to 0.9 in a
single shot when the config shape is pathological AND a strong-identifier
exact anchor keeps recall free AND the #1318 tf_freqs downweight is live.

Each of the five trigger conditions is independently falsified here, and
the through-the-real-controller integration lives at the bottom of the
file (``TestThroughRealController``).
"""
from __future__ import annotations

import random

import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import (
    DEFAULT_RULES,
    rule_precision_anchor_threshold_raise,
    rule_sparse_match_expand,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ColumnPrior,
    ComplexityProfile,
    DataProfile,
    FieldStats,
    MatchkeyProfile,
    ScoringProfile,
    SparsityVerdict,
)

_TF_FREQS = {"smith": 0.21, "jones": 0.15, "zelinski": 0.001}


def _name_field(field: str, scorer: str, tf_freqs: dict[str, float] | None) -> MatchkeyField:
    return MatchkeyField(field=field, scorer=scorer, weight=1.0, tf_freqs=tf_freqs)


def _weighted_mk(
    fields: list[MatchkeyField] | None = None,
    threshold: float = 0.8,
) -> MatchkeyConfig:
    if fields is None:
        fields = [
            _name_field("first_name", "given_name_aliased_jw", None),
            _name_field("last_name", "name_freq_weighted_jw", _TF_FREQS),
        ]
    return MatchkeyConfig(
        name="fuzzy_name", type="weighted", threshold=threshold, fields=fields,
    )


def _exact_mk(field: str = "email") -> MatchkeyConfig:
    return MatchkeyConfig(
        name=f"exact_{field}", type="exact",
        fields=[MatchkeyField(field=field)],
    )


def _cfg(matchkeys: list[MatchkeyConfig]) -> GoldenMatchConfig:
    """Minimal valid config. Pydantic requires `blocking` whenever a
    weighted matchkey is configured; the rule under test doesn't read it."""
    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"])],
        ),
    )


def _profile_with_mass_above(mass_above: float) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(
            n_rows=2600, n_cols=4,
            column_types={"email": "id-like", "first_name": "text",
                          "last_name": "text", "city": "geo"},
        ),
        blocking=BlockingProfile(
            keys_used=[["last_name"]], n_blocks=100,
            total_comparisons=15058, reduction_ratio=0.95, block_sizes_p99=40,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=15058, candidates_compared=15058,
            mass_above_threshold=mass_above,
            mass_in_borderline=0.02, dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
        matchkey=MatchkeyProfile(per_field={
            "last_name": FieldStats(
                post_transform_cardinality_ratio=0.1,
                post_transform_null_rate=0.0,
                post_transform_value_length_p50=8,
            ),
        }),
    )


def _ctx_with_priors(priors: dict[str, ColumnPrior]):
    # Function-scoped import: the repo test idiom for IndicatorContext
    # (see test_autoconfig_rules.py / test_autoconfig_controller.py) --
    # keeps the heavy autoconfig_controller module off collection-time
    # import paths for runs that never build a ctx.
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    return IndicatorContext(
        df=pl.DataFrame(),
        column_priors=priors,
        sparsity_verdict=SparsityVerdict(is_sparse=False, estimated_n_true_pairs=100),
    )


def _strong_email_ctx():
    return _ctx_with_priors(
        {"email": ColumnPrior(identity_score=0.9, corruption_score=0.0)}
    )


def _call(profile, cfg, ctx):
    return rule_precision_anchor_threshold_raise(profile, cfg, RunHistory(), ctx=ctx)


class TestFires:
    def test_all_five_conditions_satisfied(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk(threshold=0.8)])
        result = _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx())
        assert result is not None
        new_cfg, decision = result
        raised = [mk for mk in new_cfg.matchkeys if mk.type == "weighted"][0]
        assert raised.threshold == 0.9
        # copy-on-write: input config unmutated
        original = [mk for mk in cfg.matchkeys if mk.type == "weighted"][0]
        assert original.threshold == 0.8
        assert decision.rule_name == "precision_anchor_threshold_raise"
        assert "name-only" in decision.rationale
        assert "#1319" in decision.rationale
        assert "email" in decision.rationale
        assert decision.config_diff  # names the threshold change
        assert any("threshold" in k for k in decision.config_diff)

    def test_single_name_field_carrying_tf_freqs_fires(self):
        """The measured fixture shape: only last_name carries the table.
        The ANY-field formulation is load-bearing."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(fields=[
            _name_field("first_name", "given_name_aliased_jw", None),
            _name_field("last_name", "name_freq_weighted_jw", _TF_FREQS),
        ])])
        result = _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx())
        assert result is not None
        new_cfg, _ = result
        assert [mk for mk in new_cfg.matchkeys if mk.type == "weighted"][0].threshold == 0.9


class TestConditionFalsification:
    def test_mass_below_gate_returns_none(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk()])
        assert _call(_profile_with_mass_above(0.94), cfg, _strong_email_ctx()) is None

    def test_non_name_scorer_in_weighted_mk_returns_none(self):
        """The NCVR shape: weighted mk carries an address field too."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(fields=[
            _name_field("first_name", "given_name_aliased_jw", None),
            _name_field("last_name", "name_freq_weighted_jw", _TF_FREQS),
            _name_field("address", "jaro_winkler", None),
        ])])
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_no_exact_matchkey_returns_none(self):
        cfg = _cfg([_weighted_mk()])
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_exact_anchor_identity_score_too_low_returns_none(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk()])
        ctx = _ctx_with_priors(
            {"email": ColumnPrior(identity_score=0.5, corruption_score=0.0)}
        )
        assert _call(_profile_with_mass_above(1.0), cfg, ctx) is None

    def test_exact_anchor_field_missing_from_priors_returns_none(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk()])
        ctx = _ctx_with_priors(
            {"phone": ColumnPrior(identity_score=0.9, corruption_score=0.0)}
        )
        assert _call(_profile_with_mass_above(1.0), cfg, ctx) is None

    def test_no_tf_freqs_on_any_name_field_returns_none(self):
        """Identical names without the table score 1.0 and clear any
        threshold < 1 -- the raise would be pure recall risk."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(fields=[
            _name_field("first_name", "given_name_aliased_jw", None),
            _name_field("last_name", "name_freq_weighted_jw", None),
        ])])
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_threshold_none_returns_none(self):
        """The rule's ``threshold is None`` guard. The schema validator
        rejects a weighted mk constructed with threshold=None, so this can
        only arise via post-construction mutation (the validator-bypass
        case the MatchkeyConfig docstring warns about) -- the rule must
        decline rather than crash comparing None < 0.9."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(threshold=0.8)])
        weighted = [mk for mk in cfg.matchkeys if mk.type == "weighted"][0]
        weighted.threshold = None
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_threshold_already_raised_returns_none(self):
        """Convergence: single-fire by construction."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(threshold=0.9)])
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_ctx_none_returns_none(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk()])
        assert rule_precision_anchor_threshold_raise(
            _profile_with_mass_above(1.0), cfg, RunHistory()
        ) is None


class TestRegistration:
    def test_registered_directly_before_sparse_match_expand(self):
        assert (
            DEFAULT_RULES.index(rule_precision_anchor_threshold_raise)
            == DEFAULT_RULES.index(rule_sparse_match_expand) - 1
        )


# ---------------------------------------------------------------------------
# Through the REAL controller (#1319 P2).
#
# WHY THIS EXISTS: the parked B1 rule (feat/1207-pr2b-precision-anchor)
# passed its unit fixtures but triggered on a config shape the real
# controller never emits, so it could never fire in production. These tests
# pin the rule to what `auto_configure_df` actually commits, so that
# failure mode cannot recur silently.
# ---------------------------------------------------------------------------

# Reduced-scale (#1319) over-merge fixture. Shape lifted from the 2600-row
# measurement harness (scratchpad 1319/measure_1319.py, 2026-07-15
# measurement pass: P 0.0305 post-#1782 at the committed 0.8 threshold,
# overall_health YELLOW, mass_above_threshold 1.0): a common first/last
# name pool over DISTINCT people, each with a UNIQUE strong email (so the
# email column profiles as an identity anchor, priors identity_score >=
# 0.75), plus planted true duplicates that keep the email.
#
# Reduction to ~400 rows changes the controller dynamics (measured with
# the P2 probe, 2026-07-15), so the reduced fixture is stratified to keep
# every non-target subprofile healthy at BOTH thresholds:
#
# - one MEGA same-name group (80 distinct "John Smith"s + 20 exact-copy
#   dups): union-find merges it into a 100-record cluster at threshold
#   0.8 -> cluster RED via cluster_size_max > 0.1*n_rows (the over-merge
#   pathology that makes the controller iterate). Exact copies only, so
#   in-cluster triangles stay closed and transitivity stays >= 0.85
#   (otherwise rule_low_transitivity, earlier in DEFAULT_RULES, wins the
#   iteration instead).
# - 8 medium common-name groups (10 people each + 24 exact dups): more
#   same-name over-merge mass; small enough (< 0.1*n) to stay below the
#   cluster RED line on their own.
# - 130 rare-surname people (soundex-spread pool, repo fixture
#   discipline) with 33 exact + 32 light-typo dups: after the raise these
#   are the pairs still above 0.9, keeping the post-raise score
#   distribution populated and multi-modal (dip_statistic >= 0.005 --
#   without them the raised profile reads scoring RED via the unimodality
#   gate and pick_committed walks back to the 0.8 config).
# - 20 cities so last_name+city blocks stay small (block_sizes_p99 must
#   hold p99 <= 10 * n_rows/n_blocks, else blocking reads RED at every
#   iteration and no raise can ever be committed).
#
# Measured trajectory (P2 probe): iter0 RED (cluster, max_cl=100,
# trans=1.0, dip=0.048) -> rule fires -> iter1 at 0.9: max_cl=15,
# dip=0.12, everything GREEN except the mk-cardinality YELLOW ->
# POLICY_SATISFIED, raised entry outranks the RED v0 -> committed
# threshold 0.9.

_FIRSTS = ["John", "Jane", "Mary", "James", "Robert", "Linda",
           "Michael", "Patricia", "David", "Susan", "William", "Karen"]
_COMMON_LASTS = ["Johnson", "Williams", "Brown", "Jones", "Garcia",
                 "Miller", "Davis", "Rodriguez", "Martinez", "Wilson"]
_RARE_LASTS = [
    "Abernathy", "Balthazar", "Cavendish", "Delacroix", "Ellsworth",
    "Fairbanks", "Galbraith", "Hollingsworth", "Ibarra", "Jankowski",
    "Kowalczyk", "Lindqvist", "Mercier", "Nakamura", "Oyelaran",
    "Pemberton", "Quintanilla", "Rutherford", "Szymanski", "Thackeray",
    "Umberger", "Vasquez", "Wetherell", "Xiong", "Yamaguchi", "Zelinski",
    "Ashcroft", "Bergstrom", "Castellano", "Drummond", "Eastwood",
    "Feldman", "Goldberg", "Hathaway", "Ingersoll", "Jorgensen",
    "Kettering", "Lombardi", "Montgomery", "Norwood", "Ostrowski",
    "Prendergast", "Quimby", "Ravenscroft", "Silverstein", "Tremblay",
    "Underhill", "Villanueva", "Whitfield", "Yarborough",
]
_CITIES = ["Springfield", "Madison", "Franklin", "Georgetown", "Clinton",
           "Salem", "Fairview", "Bristol", "Dover", "Hudson",
           "Arlington", "Burlington", "Chester", "Dayton", "Easton",
           "Florence", "Greenville", "Harrison", "Jackson", "Kingston"]


def _light_typo(rng: random.Random, val: str) -> str:
    if len(val) < 3:
        return val
    op = rng.choice(["typo", "swap", "drop"])
    pos = rng.randint(1, len(val) - 2)
    if op == "typo":
        return val[:pos] + rng.choice("abcdefghijklmnopqrstuvwxyz") + val[pos + 1:]
    if op == "swap":
        return val[:pos] + val[pos + 1] + val[pos] + val[pos + 2:]
    return val[:pos] + val[pos + 1:]


def _overmerge_fixture_df() -> pl.DataFrame:
    """Deterministic 399-row #1319 over-merge frame (see block comment)."""
    rng = random.Random(1319)
    rows: list[dict] = []
    pid = 0

    def person(first: str, last: str) -> dict:
        nonlocal pid
        row = {"first_name": first, "last_name": last,
               "email": f"{first[0].lower()}{last.lower()}{pid}@example.com",
               "city": rng.choice(_CITIES)}
        pid += 1
        rows.append(row)
        return row

    mega = [person("John", "Smith") for _ in range(80)]
    medium: list[dict] = []
    for i in range(8):
        first = _FIRSTS[(i + 1) % len(_FIRSTS)]
        last = _COMMON_LASTS[i % len(_COMMON_LASTS)]
        medium += [person(first, last) for _ in range(10)]
    rare = [
        person(_FIRSTS[i % len(_FIRSTS)], _RARE_LASTS[i % len(_RARE_LASTS)])
        for i in range(130)
    ]

    for p in rng.sample(mega, 20):
        rows.append(dict(p))
    for p in rng.sample(medium, 24):
        rows.append(dict(p))
    rare_dups = rng.sample(rare, 65)
    for p in rare_dups[:33]:
        rows.append(dict(p))
    for p in rare_dups[33:]:
        d = dict(p)
        d["last_name"] = _light_typo(rng, d["last_name"])
        rows.append(d)
    return pl.DataFrame(rows)


def _committed_name_only_weighted_mk(cfg):
    """Fixture-validity guard shared by both controller tests.

    Returns the committed weighted matchkey after asserting the fixture
    still produces the #1319 trigger shape. If auto-config stops emitting
    this shape, these tests must say so loudly instead of vacuously
    passing on assertions about a config that no longer looks like the
    measured pathology."""
    from goldenmatch.core.autoconfig_rules import (
        _ANCHOR_NAME_SCORERS,  # pyright: ignore[reportPrivateUsage]
    )

    matchkeys = cfg.get_matchkeys()
    weighted = [mk for mk in matchkeys if mk.type == "weighted"]
    assert weighted, (
        "FIXTURE INVALID: auto-config no longer commits a weighted "
        "matchkey on the #1319 over-merge fixture; the trigger shape is "
        "gone and these controller tests no longer test the rule."
    )
    mk = weighted[0]
    scorers = [f.scorer for f in mk.fields or []]
    assert scorers and all(s in _ANCHOR_NAME_SCORERS for s in scorers), (
        f"FIXTURE INVALID: committed weighted matchkey is no longer "
        f"name-only (scorers={scorers}); auto-config stopped emitting "
        f"the #1319 trigger shape on this fixture."
    )
    assert any(f.tf_freqs for f in mk.fields or []), (
        "FIXTURE INVALID: no tf_freqs on any name field of the committed "
        "weighted matchkey; the #1318 downweight is not live on this "
        "fixture and the rule's trigger shape is gone."
    )
    assert any(m.type == "exact" for m in matchkeys), (
        "FIXTURE INVALID: no exact matchkey committed; the strong-email "
        "anchor half of the #1319 trigger shape is gone."
    )
    return mk


class TestThroughRealController:
    def test_rule_fires_through_real_controller(self, monkeypatch):
        """The parked-B1 lesson, executable: the previous precision-anchor
        rule passed its unit fixtures but triggered on a config shape the
        real controller never emitted, so it could never fire. This test
        runs the rule through `auto_configure_df` on the reduced #1319
        over-merge fixture and requires the RAISED config to be the one
        the controller actually COMMITS (not merely that the rule fired
        mid-loop -- a fired-then-discarded raise is the same failure mode
        wearing a different hat)."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        from goldenmatch.core.autoconfig import (
            _LAST_CONTROLLER_RUN,
            auto_configure_df,
        )
        from goldenmatch.core.autoconfig_rules import _ANCHOR_RAISED_THRESHOLD

        cfg = auto_configure_df(_overmerge_fixture_df())

        mk = _committed_name_only_weighted_mk(cfg)
        assert mk.threshold == _ANCHOR_RAISED_THRESHOLD, (
            f"rule did not land through the real controller: committed "
            f"weighted matchkey threshold is {mk.threshold}, expected "
            f"{_ANCHOR_RAISED_THRESHOLD}"
        )

        run = _LAST_CONTROLLER_RUN.get()
        assert run is not None, "controller run left no telemetry"
        _profile, history = run
        rule_names = [d.rule_name for d in history.decisions]
        assert "precision_anchor_threshold_raise" in rule_names, (
            f"raise committed but the rule's decision is missing from "
            f"controller history (decisions={rule_names})"
        )

    def test_without_rule_controller_commits_low_threshold(self, monkeypatch):
        """Sensitivity pin for the test above: the same fixture through
        the same controller with the rule removed from DEFAULT_RULES must
        commit a threshold below 0.9. Proves the integration test is
        genuinely sensitive to the rule (it cannot pass because the
        controller lands at 0.9 for some unrelated reason)."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        import goldenmatch.core.autoconfig_rules as rules_mod
        from goldenmatch.core.autoconfig import auto_configure_df
        from goldenmatch.core.autoconfig_rules import _ANCHOR_RAISED_THRESHOLD

        monkeypatch.setattr(
            rules_mod,
            "DEFAULT_RULES",
            [r for r in rules_mod.DEFAULT_RULES
             if r is not rule_precision_anchor_threshold_raise],
        )

        cfg = auto_configure_df(_overmerge_fixture_df())

        mk = _committed_name_only_weighted_mk(cfg)
        assert mk.threshold is not None
        assert mk.threshold < _ANCHOR_RAISED_THRESHOLD, (
            f"threshold reached {mk.threshold} WITHOUT the rule -- the "
            f"integration test above is not sensitive to the rule"
        )
