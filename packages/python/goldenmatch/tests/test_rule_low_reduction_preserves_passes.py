"""`rule_low_reduction_ratio` rebuilds `passes` from `keys` and drops the rest.

The rule means to ADD one soundex pass. It builds the replacement list from
``current.blocking.keys``:

    existing_keys = list(current.blocking.keys)
    new_blocking = current.blocking.model_copy(update={
        "strategy": "multi_pass",
        "passes": existing_keys + [new_pass],
    })

For a keys-driven config those are the same thing. For a ``multi_pass`` config
the plan lives in ``passes`` and ``keys`` holds only the primary key, so every
other pass is silently discarded.

Measured, person @ 100,000 rows (run 32084546976). Auto-config emits eight
passes; the controller commits two:

    _legacy_auto_configure_v0 (traced)      8 passes
        [city, first_name] | [city, first_name]+substring5 | first_name soundex
        | surname soundex | surname substring5 | dob | dob substring4 | postcode

    controller committed config              2 passes
        [city, first_name] | dob soundex

`[city, first_name]` is exactly `blocking.keys`, and `dob soundex` is this
rule's addition. The six survivors of neither list are gone.

What it costs, same fixture, same run:

    lane                     scored pairs  pairwise P       R      F1
    gm_probabilistic_shipped      142,933      0.9992  0.9949  0.9970
    gm_zeroconfig                  11,319      1.0000  0.4684  0.6380

Precision 1.0000 with recall 0.4684 is the signature: a discarded pass removes
candidates, never adds them, so the damage is recall-only and no threshold can
recover it. The rule fires because the reduction ratio is LOW (too many
candidate pairs) and then removes 75% of the blocking plan, which does reduce
comparisons -- it just reduces the true ones too.

The repo already knows this hazard. `_carries_own_blocking_plan` (#2488) exists
because ``not blocking.keys`` "is only correct for the keys-driven strategies".
This rule was never updated.

The existing coverage
(`test_autoconfig_policy.py::test_rule_low_reduction_fires_and_adds_multi_pass`)
asserts `len(passes) >= 2` on a config that has keys and NO passes, so the two
lists coincide and the bug is invisible there. It stays green after the fix.
"""
from __future__ import annotations

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import rule_low_reduction_ratio
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
)

_PASSES = [
    BlockingKeyConfig(fields=["city", "first_name"], transforms=["lowercase", "strip"]),
    BlockingKeyConfig(fields=["city", "first_name"], transforms=["lowercase", "substring:0:5"]),
    BlockingKeyConfig(fields=["first_name"], transforms=["lowercase", "soundex"]),
    BlockingKeyConfig(fields=["surname"], transforms=["lowercase", "soundex"]),
    BlockingKeyConfig(fields=["surname"], transforms=["lowercase", "substring:0:5"]),
    BlockingKeyConfig(fields=["dob"], transforms=["lowercase", "strip"]),
    BlockingKeyConfig(fields=["dob"], transforms=["substring:0:4"]),
    BlockingKeyConfig(fields=["postcode"], transforms=["strip"]),
]


def _multipass_config() -> GoldenMatchConfig:
    """The shape auto-config actually emits: the plan in `passes`, the primary
    key alone in `keys`."""
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="probabilistic_auto", type="probabilistic",
            fields=[MatchkeyField(field="first_name", scorer="jaro_winkler"),
                    MatchkeyField(field="surname", scorer="jaro_winkler")],
        )],
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["city", "first_name"],
                                    transforms=["lowercase", "strip"])],
            passes=list(_PASSES),
        ),
    )


def _low_reduction_profile() -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=100_000, n_cols=6,
                         column_types={"first_name": "name", "surname": "name",
                                       "city": "geo", "dob": "text"}),
        blocking=BlockingProfile(keys_used=[["city", "first_name"]], n_blocks=14_081,
                                 total_comparisons=4000, reduction_ratio=0.1,
                                 block_sizes_p99=15),
        scoring=ScoringProfile(n_pairs_scored=4000, mass_above_threshold=0.4,
                               dip_statistic=0.05),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )


def _sigs(blocking):
    return [(tuple(p.fields), tuple(p.transforms or []))
            for p in (blocking.passes or [])]


def test_the_rule_still_fires_on_this_shape():
    """Guard the guard: if it stopped firing, the assertions below would pass
    for the wrong reason."""
    assert rule_low_reduction_ratio(
        _low_reduction_profile(), _multipass_config(), RunHistory(),
    ) is not None


def test_existing_passes_survive():
    """The rule is additive by intent. It must not delete the plan it is
    augmenting."""
    cfg = _multipass_config()
    before = _sigs(cfg.blocking)
    new_cfg, _decision = rule_low_reduction_ratio(
        _low_reduction_profile(), cfg, RunHistory(),
    )
    after = _sigs(new_cfg.blocking)

    missing = [s for s in before if s not in after]
    assert not missing, (
        f"dropped {len(missing)} of {len(before)} blocking passes: {missing}"
    )


def test_it_still_adds_the_soundex_pass():
    new_cfg, _ = rule_low_reduction_ratio(
        _low_reduction_profile(), _multipass_config(), RunHistory(),
    )
    assert [p for p in new_cfg.blocking.passes if "soundex" in (p.transforms or [])]
    assert new_cfg.blocking.strategy == "multi_pass"


def test_it_adds_exactly_one_pass():
    """Pinned so 'nothing was dropped' cannot be satisfied by duplicating the
    plan (keys + passes concatenated would also keep everything)."""
    cfg = _multipass_config()
    new_cfg, _ = rule_low_reduction_ratio(
        _low_reduction_profile(), cfg, RunHistory(),
    )
    assert len(new_cfg.blocking.passes or []) == len(_PASSES) + 1


def test_a_keys_only_config_is_unchanged():
    """The keys-driven shape the rule was written against must behave exactly as
    before: `passes` becomes keys + the soundex pass."""
    cfg = _multipass_config()
    cfg.blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
    )
    new_cfg, _ = rule_low_reduction_ratio(
        _low_reduction_profile(), cfg, RunHistory(),
    )
    assert _sigs(new_cfg.blocking)[0] == (("city",), ("lowercase",))
    assert len(new_cfg.blocking.passes) == 2
