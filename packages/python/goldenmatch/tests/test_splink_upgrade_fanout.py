"""Tests for the fan_out upgrade lever -- Task F1 scaffold (registry wiring,
lever ordering, bare-settings skip, the shared within-block prior helper,
reference-input context threading), Task F2 NE candidate eligibility, Task
F3 risk gate + posterior-weighted NE estimation, and Task F4 cluster-guard
tuning.

Spec: docs/superpowers/specs/2026-07-14-fanout-ne-upgrade-lever-design.md
"""
from __future__ import annotations

import math

import polars as pl
import pytest
from goldenmatch.config.from_splink import (
    ConversionReport,
    SplinkConversion,
    from_splink,
)
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.config.splink_upgrade import (
    SplinkUpgradeError,
    _estimate_within_block_prior,
    _resolve_levers,
    upgrade_splink_conversion,
)
from goldenmatch.config.splink_upgrade_fanout import _ne_candidates
from goldenmatch.core.probabilistic import EMResult

# ── Fixtures (mirror tests/test_splink_upgrade_levers.py bare-settings) ──────


def _jw_comparison():
    return {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": '"first_name_l" = "first_name_r"'},
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                )
            },
            {"sql_condition": "ELSE"},
        ],
    }


def _exact_only_comparison(column="surname"):
    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": f'"{column}_l" = "{column}_r"'},
            {"sql_condition": "ELSE"},
        ],
    }


def _bare_settings():
    return {
        "comparisons": [_jw_comparison(), _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
    }


def _sample_df(n=30):
    first_names = ["alice", "bob", "carol", "dave", "erin", "frank"]
    surnames = ["smith", "jones", "brown", "davis", "wilson", "moore"]
    return pl.DataFrame(
        {
            "rec_id": list(range(n)),
            "first_name": [first_names[i % len(first_names)] for i in range(n)],
            "surname": [surnames[i % len(surnames)] for i in range(n)],
        }
    )


# ── Registry wiring / lever order ─────────────────────────────────────────────


def test_fan_out_in_default_lever_order():
    assert _resolve_levers(None) == [
        "tf_tables",
        "distance_thresholds",
        "fan_out",
        "calibration",
    ]


def test_fan_out_selectable_alone():
    assert _resolve_levers({"fan_out"}) == ["fan_out"]

    with pytest.raises(SplinkUpgradeError):
        _resolve_levers({"fan_out", "nope"})


# ── Bare-settings skip ────────────────────────────────────────────────────────


def test_fan_out_bare_settings_skip():
    conversion = from_splink(_bare_settings())
    baseline_dump = conversion.config.model_dump()

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers={"fan_out"}, measure=False
    )

    findings = [
        f for f in result.report.findings if f.splink_path == "upgrade:fan_out"
    ]
    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert "no imported model" in findings[0].message
    # A skipped lever must leave the config untouched.
    assert result.upgraded_config.model_dump() == baseline_dump


# ── Shared within-block prior helper ─────────────────────────────────────────


def test_estimate_within_block_prior():
    # 2^0 / (1 + 2^0) == 0.5 exactly.
    assert _estimate_within_block_prior([0.0]) == pytest.approx(0.5)

    # A strongly negative weight (~0) and a strongly positive one (~1)
    # average to ~0.5.
    assert _estimate_within_block_prior([-20.0, 20.0]) == pytest.approx(
        0.5, abs=1e-5
    )

    with pytest.raises(ValueError):
        _estimate_within_block_prior([])


# ── Context threading of reference inputs ────────────────────────────────────


def test_lever_context_carries_reference_inputs(monkeypatch):
    from goldenmatch.config import splink_upgrade

    conversion = from_splink(_bare_settings())
    clusters_df = pl.DataFrame({"rec_id": [0, 1], "cluster_id": [0, 0]})
    labels_df = pl.DataFrame({"rec_id": [0, 1], "cluster_id": [0, 1]})

    seen = {}

    def _spy_fan_out(ctx):
        seen["splink_clusters"] = ctx.splink_clusters
        seen["labels"] = ctx.labels
        seen["id_column"] = ctx.id_column

    monkeypatch.setitem(splink_upgrade._LEVER_REGISTRY, "fan_out", _spy_fan_out)

    upgrade_splink_conversion(
        conversion,
        _sample_df(),
        splink_clusters=clusters_df,
        labels=labels_df,
        id_column="rec_id",
        levers={"fan_out"},
        measure=False,
    )

    assert seen["splink_clusters"] is clusters_df
    assert seen["labels"] is labels_df
    assert seen["id_column"] == "rec_id"


# ── NE candidate eligibility (Task F2) ───────────────────────────────────────


def _prob_matchkey(field_names=("given_name", "surname", "city")):
    return MatchkeyConfig(
        name="prob",
        type="probabilistic",
        fields=[MatchkeyField(field=f, scorer="jaro_winkler") for f in field_names],
    )


def _blocking(*key_fields):
    return BlockingConfig(keys=[BlockingKeyConfig(fields=[f]) for f in key_fields])


def _candidate_df(n=20, extra=None):
    """Sampled-frame stand-in exercising every eligibility gate.

    - ``phone``: identity-named, fully populated, 19/20 distinct -> ELIGIBLE.
    - ``email_null_heavy``: identity-named but 75% null -> excluded (non-null floor).
    - ``phone_low_card``: identity-named but 3 distinct values -> excluded
      (min-cardinality floor).
    - ``ssn``: identity-named but all-unique (ratio 1.0) -> excluded by the
      max-cardinality gate (a plain ``rec_id`` surrogate would be excluded by
      the name filter first and never exercise it).
    - ``notes``: non-identity name -> excluded.
    - ``given_name``/``surname``/``city``: comparison/blocking fields on the
      default matchkey/blocking used by the tests.
    """
    data = {
        "given_name": [f"name{i}" for i in range(n)],
        "surname": [f"sur{i % 6}" for i in range(n)],
        "city": [f"city{i % 4}" for i in range(n)],
        # One duplicate value -> 19/20 = 0.95, inside [0.5, 0.999].
        "phone": [f"555-01{max(i, 1):02d}" for i in range(n)],
        "email_null_heavy": [f"a{i}@x.com" if i < 5 else None for i in range(n)],
        "phone_low_card": [f"555-000{i % 3}" for i in range(n)],
        "ssn": [f"{i:09d}" for i in range(n)],
        "notes": [f"note {i}" for i in range(n)],
    }
    if extra:
        data.update(extra)
    return pl.DataFrame(data)


def test_ne_candidates_returns_only_eligible_phone():
    cands = _ne_candidates(_candidate_df(), _prob_matchkey(), _blocking("surname"))

    assert [c.column for c in cands] == ["phone"]
    # Transforms/scorer come from _pick_scorer_for_column.
    assert cands[0].transforms == ["digits_only"]
    assert cands[0].scorer == "exact"


def test_ne_candidates_excludes_matchkey_comparison_field():
    n = 20
    df = _candidate_df(extra={"phone2": [f"555-02{max(i, 1):02d}" for i in range(n)]})
    mk = _prob_matchkey(("given_name", "surname", "city", "phone2"))

    cands = _ne_candidates(df, mk, _blocking("surname"))

    assert [c.column for c in cands] == ["phone"]


def test_ne_candidates_excludes_blocking_key_field():
    n = 20
    df = _candidate_df(extra={"phone3": [f"555-03{max(i, 1):02d}" for i in range(n)]})

    cands = _ne_candidates(df, _prob_matchkey(), _blocking("surname", "phone3"))

    assert [c.column for c in cands] == ["phone"]


def test_ne_candidates_name_match_is_case_insensitive():
    n = 20
    df = _candidate_df(
        extra={"Phone_Number": [f"555-04{max(i, 1):02d}" for i in range(n)]}
    )

    cands = _ne_candidates(df, _prob_matchkey(), _blocking("surname"))

    # Deterministic df-column order: phone before Phone_Number.
    assert [c.column for c in cands] == ["phone", "Phone_Number"]


def test_ne_candidates_excludes_record_pseudo_column():
    """Third leg of the exclusion set: a df column literally named
    ``__record__`` (the synthesized record_embedding pseudo-field name) is
    never a candidate."""
    n = 20
    df = _candidate_df(
        extra={"__record__": [f"555-09{max(i, 1):02d}" for i in range(n)]}
    )

    cands = _ne_candidates(df, _prob_matchkey(), _blocking("surname"))

    assert [c.column for c in cands] == ["phone"]


# ── Risk gate + posterior-weighted NE estimation (Task F3) ───────────────────


def _fanout_matchkey():
    return MatchkeyConfig(
        name="prob",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="exact"),
            MatchkeyField(field="surname", scorer="exact"),
        ],
    )


def _fanout_em_model(fields=("first_name", "surname")):
    """Hand-built trained model (NO EM training in unit tests): each field
    contributes +-log2(0.99/0.01) ~ +-6.63 bits, so a both-agree pair sits
    ~13.3 bits above a both-disagree pair -- comfortably confident under the
    within-block prior re-estimate on the fixtures below."""
    agree_w = math.log2(0.99 / 0.01)
    return EMResult(
        m_probs={f: [0.01, 0.99] for f in fields},
        u_probs={f: [0.99, 0.01] for f in fields},
        match_weights={f: [-agree_w, agree_w] for f in fields},
        converged=True,
        iterations=5,
        proportion_matched=0.01,
    )


def _fanout_conversion(*, em=None):
    config = GoldenMatchConfig(
        matchkeys=[_fanout_matchkey()],
        blocking=_blocking("city"),
    )
    return SplinkConversion(
        config=config,
        report=ConversionReport(),
        em_model=em if em is not None else _fanout_em_model(),
    )


def _fanout_df(*, homonym_groups=6, homonym_phones_differ=True, dup_phones_differ=False):
    """200 rows over 4 ``city`` blocks (53/53/47/47 with the defaults):

    - ``homonym_groups`` groups of 3 sharing first_name+surname+city -- the
      fan-out traps. Phones DIFFER within a group when
      ``homonym_phones_differ`` (risk present), else the group shares one.
    - 10 true-duplicate groups of 3 sharing first_name+surname+city AND
      phone (unless ``dup_phones_differ`` -- the nondiscriminating shape
      where phone differs on everything).
    - fillers with unique names and unique phones.

    Every confident-merge pair (both fields agree) is a within-group pair;
    fillers never collide on either comparison field.
    """
    cities = [f"city{c}" for c in range(4)]
    rows: list[dict] = []
    counter = 0

    def next_phone() -> str:
        nonlocal counter
        counter += 1
        return f"555-{counter:07d}"

    for g in range(homonym_groups):
        shared = None if homonym_phones_differ else next_phone()
        for _ in range(3):
            rows.append(
                {
                    "first_name": f"homfn{g}",
                    "surname": f"homsn{g}",
                    "city": cities[g % 4],
                    "phone": next_phone() if homonym_phones_differ else shared,
                }
            )
    for g in range(10):
        shared = None if dup_phones_differ else next_phone()
        for _ in range(3):
            rows.append(
                {
                    "first_name": f"dupfn{g}",
                    "surname": f"dupsn{g}",
                    "city": cities[g % 4],
                    "phone": next_phone() if dup_phones_differ else shared,
                }
            )
    i = 0
    while len(rows) < 200:
        rows.append(
            {
                "first_name": f"fillfn{i}",
                "surname": f"fillsn{i}",
                "city": cities[i % 4],
                "phone": next_phone(),
            }
        )
        i += 1
    if homonym_phones_differ and dup_phones_differ:
        # All-distinct phones would trip the max-cardinality candidate gate
        # (ratio 1.0 > 0.999). Duplicate one phone across two FILLERS in
        # DIFFERENT cities (never blocked together, never a confident pair)
        # so the column stays eligible while still firing on every blocked
        # confident pair.
        rows[-1]["phone"] = rows[-2]["phone"]
    return pl.DataFrame(rows)


def _fan_out_findings(result, severity=None):
    return [
        f
        for f in result.report.findings
        if f.splink_path == "upgrade:fan_out"
        and (severity is None or f.severity == severity)
    ]


def test_fan_out_adds_ne_when_risk_present():
    conversion = _fanout_conversion()

    res = upgrade_splink_conversion(
        conversion, _fanout_df(), levers={"fan_out"}, measure=False
    )

    mk = res.upgraded_config.get_matchkeys()[0]
    assert mk.negative_evidence is not None
    assert [ne.field for ne in mk.negative_evidence] == ["phone"]
    ne = mk.negative_evidence[0]
    # EM-learned shape: no penalty, no penalty_bits; gate tuple from the
    # F2 candidate + the autoconfig default NE threshold.
    assert ne.penalty is None and ne.penalty_bits is None
    assert ne.threshold == pytest.approx(0.4)
    assert ne.transforms == ["digits_only"]
    assert ne.scorer == "exact"

    assert res.em_model is not None
    key = "__ne__phone"
    assert key in res.em_model.m_probs
    assert key in res.em_model.u_probs
    assert key in res.em_model.match_weights
    assert res.em_model.match_weights[key][0] < 0
    assert res.em_model.match_weights[key][1] == 0.0
    # [fired, not_fired] 2-lists sum to 1 (within clamp tolerance).
    assert sum(res.em_model.m_probs[key]) == pytest.approx(1.0, abs=1e-3)
    assert sum(res.em_model.u_probs[key]) == pytest.approx(1.0, abs=1e-3)

    # The upgraded config round-trips schema validation with NE attached.
    GoldenMatchConfig(**res.upgraded_config.model_dump())

    added = [f for f in _fan_out_findings(res, "info") if "added" in f.message]
    assert len(added) == 1
    assert "phone" in added[0].message
    assert "m_fire" in added[0].message


def test_fan_out_no_risk_no_ne():
    conversion = _fanout_conversion()

    res = upgrade_splink_conversion(
        conversion,
        _fanout_df(homonym_phones_differ=False),
        levers={"fan_out"},
        measure=False,
    )

    assert not res.upgraded_config.get_matchkeys()[0].negative_evidence
    assert res.em_model is not None
    assert not any(k.startswith("__ne__") for k in res.em_model.match_weights)
    # The measured (zero) contradiction rate is reported even when gated.
    rate_findings = [
        f for f in _fan_out_findings(res, "info") if "contradiction rate" in f.message
    ]
    assert len(rate_findings) == 1
    assert "contradiction rate 0.0000" in rate_findings[0].message


def test_fan_out_insufficient_support_skips():
    # 2 homonym groups of 3 -> only 6 firing confident pairs, below the
    # _FANOUT_MIN_FIRING_PAIRS=10 support floor (rate 6/36 clears the 2%
    # rate floor, so this isolates the support leg).
    conversion = _fanout_conversion()

    res = upgrade_splink_conversion(
        conversion,
        _fanout_df(homonym_groups=2),
        levers={"fan_out"},
        measure=False,
    )

    assert not res.upgraded_config.get_matchkeys()[0].negative_evidence
    assert res.em_model is not None
    assert not any(k.startswith("__ne__") for k in res.em_model.match_weights)
    rate_findings = [
        f for f in _fan_out_findings(res, "info") if "contradiction rate" in f.message
    ]
    assert len(rate_findings) == 1


def test_fan_out_nondiscriminating_dropped():
    # Phone differs on EVERYTHING (true dups included): the gate passes
    # (confident pairs fire), but the random-pair firing rate is just as
    # high -> w_fired >= 0 -> warn + drop, no NE.
    conversion = _fanout_conversion()

    res = upgrade_splink_conversion(
        conversion,
        _fanout_df(dup_phones_differ=True),
        levers={"fan_out"},
        measure=False,
    )

    assert not res.upgraded_config.get_matchkeys()[0].negative_evidence
    assert res.em_model is not None
    assert not any(k.startswith("__ne__") for k in res.em_model.match_weights)
    warns = _fan_out_findings(res, "warning")
    assert len(warns) == 1
    assert "does not discriminate" in warns[0].message
    assert "phone" in warns[0].message


def test_fan_out_no_blocking_warns():
    """GoldenMatchConfig forbids probabilistic matchkeys without blocking at
    construction time, so the no-blocking shape can only arise from
    post-construction mutation -- drive the lever directly through a
    hand-built _LeverContext to prove the guard warns + skips, no crash."""
    from goldenmatch.config.splink_upgrade import _LeverContext
    from goldenmatch.config.splink_upgrade_fanout import run_fan_out_lever

    conversion = _fanout_conversion()
    upgraded = GoldenMatchConfig(**conversion.config.model_dump())
    upgraded.blocking = None
    report = ConversionReport()
    ctx = _LeverContext(
        conversion=conversion,
        upgraded_config=upgraded,
        em_model=_fanout_em_model(),
        report=report,
        df=_fanout_df(),
        seed=42,
    )

    run_fan_out_lever(ctx)

    assert not upgraded.get_matchkeys()[0].negative_evidence
    warns = [
        f
        for f in report.findings
        if f.splink_path == "upgrade:fan_out" and f.severity == "warning"
    ]
    assert len(warns) == 1
    assert "blocking configuration" in warns[0].message
    assert "skipped" in warns[0].message.lower()


def test_fan_out_partial_model_skips_ne():
    # Only first_name carries imported m/u -- surname is uncovered (mixed
    # bare/trained input shape), so pairs cannot be scored.
    conversion = _fanout_conversion(em=_fanout_em_model(fields=("first_name",)))

    res = upgrade_splink_conversion(
        conversion, _fanout_df(), levers={"fan_out"}, measure=False
    )

    assert not res.upgraded_config.get_matchkeys()[0].negative_evidence
    warns = _fan_out_findings(res, "warning")
    assert len(warns) == 1
    assert "partial" in warns[0].message
    assert "surname" in warns[0].message
    assert "skipped" in warns[0].message.lower()


def test_fan_out_copy_on_write():
    conversion = _fanout_conversion()

    res = upgrade_splink_conversion(
        conversion, _fanout_df(), levers={"fan_out"}, measure=False
    )

    # The lever DID add NE on the upgraded copies...
    assert res.upgraded_config.get_matchkeys()[0].negative_evidence
    # ...while the baseline conversion stays untouched.
    assert conversion.config.get_matchkeys()[0].negative_evidence is None
    assert conversion.em_model is not None
    assert not any(k.startswith("__ne__") for k in conversion.em_model.m_probs)
    assert not any(k.startswith("__ne__") for k in conversion.em_model.u_probs)
    assert not any(k.startswith("__ne__") for k in conversion.em_model.match_weights)
    assert res.baseline_config.get_matchkeys()[0].negative_evidence is None


def test_fan_out_block_size_findings():
    conversion = _fanout_conversion()

    res = upgrade_splink_conversion(
        conversion, _fanout_df(), levers={"fan_out"}, measure=False
    )

    block_findings = [
        f for f in _fan_out_findings(res, "info") if "block size" in f.message
    ]
    assert len(block_findings) == 1
    msg = block_findings[0].message
    # 4 city blocks of 53/53/47/47 rows.
    assert "p50=53" in msg
    assert "p95=" in msg
    assert "max=53" in msg
    # Findings only -- max_block_size is untouched in v1.
    assert res.upgraded_config.blocking.max_block_size == conversion.config.blocking.max_block_size


# ── Posterior definition + weighting pin (F3 ride-along) ─────────────────────


def _mixed_pair_df():
    """One ``city`` block designed so the pinned within-block-prior posterior
    and an equal-odds (prior_w=0) posterior genuinely diverge around the 0.9
    confidence floor:

    - 6 homonym groups of 3 (both names shared, phones differ): confident
      FIRING pairs under either prior.
    - 10 true-duplicate groups of 3 (names + phone shared): confident
      non-firing pairs.
    - 4 MIXED rows (shared surname, distinct first names, distinct phones):
      C(4,2)=6 pairs whose total weight (+10 surname-agree, -6 first_name-
      disagree = +4 bits under ``_mixed_pair_em_model``) sits ABOVE the 0.9
      floor equal-odds (posterior ~0.94) but BELOW it under the re-estimated
      within-block prior -- an equal-odds substitution would misclassify
      them AND weight them differently in m_fire.
    """
    rows: list[dict] = []
    counter = 0

    def next_phone() -> str:
        nonlocal counter
        counter += 1
        return f"555-{counter:07d}"

    for g in range(6):
        for _ in range(3):
            rows.append(
                {
                    "first_name": f"homfn{g}",
                    "surname": f"homsn{g}",
                    "city": "c1",
                    "phone": next_phone(),
                }
            )
    for g in range(10):
        shared = next_phone()
        for _ in range(3):
            rows.append(
                {
                    "first_name": f"dupfn{g}",
                    "surname": f"dupsn{g}",
                    "city": "c1",
                    "phone": shared,
                }
            )
    for k in range(4):
        rows.append(
            {
                "first_name": f"mixfn{k}",
                "surname": "mixshared",
                "city": "c1",
                "phone": next_phone(),
            }
        )
    return pl.DataFrame(rows)


def _mixed_pair_em_model():
    """Asymmetric hand-built weights: surname-agree-only pairs land at
    10 - 6 = +4 bits -- confident equal-odds (0.941 >= 0.9) but not under
    the within-block prior re-estimate (prior << 0.5 on this block)."""
    return EMResult(
        m_probs={"first_name": [0.05, 0.95], "surname": [0.02, 0.98]},
        u_probs={"first_name": [0.9, 0.1], "surname": [0.95, 0.05]},
        match_weights={"first_name": [-6.0, 4.0], "surname": [-6.0, 10.0]},
        converged=True,
        iterations=5,
        proportion_matched=0.01,
    )


def test_fan_out_posterior_definition_pinned():
    """Pin the POSTERIOR DEFINITION (within-block prior re-estimate, not
    equal-odds) and the posterior WEIGHTING of m_fire (not count-based):
    replicate the pinned math by hand in the test and require the stored
    ``match_weights["__ne__phone"][0]`` to match it to 1e-9, while both an
    equal-odds substitution and a count-based m_fire come out different."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.config.splink_upgrade import _CALIBRATION_MAX_PAIRS
    from goldenmatch.config.splink_upgrade_fanout import (
        _PROB_CLAMP,
        _random_pair_firing_rate,
    )
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.probabilistic import (
        _ne_fired,
        _sample_blocked_pairs,
        comparison_vector,
        posterior_from_weight,
        prior_weight,
    )

    df = _mixed_pair_df()
    conversion = _fanout_conversion(em=_mixed_pair_em_model())

    res = upgrade_splink_conversion(
        conversion, df, levers={"fan_out"}, measure=False
    )

    key = "__ne__phone"
    assert res.em_model is not None and key in res.em_model.match_weights
    stored_w = res.em_model.match_weights[key][0]

    # ── Hand-replicate the pinned math on the same pair sample ──────────
    mk = res.upgraded_config.get_matchkeys()[0]
    em_weights = {"first_name": [-6.0, 4.0], "surname": [-6.0, 10.0]}
    lf = (
        df.lazy()
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )
    blocks = build_blocks(lf, _blocking("city"))
    pairs = _sample_blocked_pairs(blocks, n_pairs=_CALIBRATION_MAX_PAIRS, seed=42)
    row_lookup = {
        i: {**row, "__row_id__": i} for i, row in enumerate(df.to_dicts())
    }
    indexed = [(k, f.field) for k, f in enumerate(mk.fields)]
    totals = []
    for a, b in pairs:
        vec = comparison_vector(row_lookup[a], row_lookup[b], mk)
        totals.append(sum(em_weights[name][vec[k]] for k, name in indexed))

    # Pinned posterior: within-block prior re-estimate, NOT equal-odds.
    prior = _estimate_within_block_prior(totals)
    prior_w = prior_weight(prior)
    posteriors = [posterior_from_weight(w, prior_w) for w in totals]
    ne = NegativeEvidenceField(
        field="phone", transforms=["digits_only"], scorer="exact", threshold=0.4
    )
    fired = [_ne_fired(row_lookup[a], row_lookup[b], ne) for a, b in pairs]

    def clamp(p):
        return min(max(p, _PROB_CLAMP), 1.0 - _PROB_CLAMP)

    m_fire = clamp(
        sum(p for p, f in zip(posteriors, fired) if f) / sum(posteriors)
    )
    u_fire = clamp(_random_pair_firing_rate(row_lookup, ne, 42))
    expected_w = math.log2(m_fire / u_fire)
    assert stored_w == pytest.approx(expected_w, abs=1e-9)

    # ── The fixture genuinely separates the two priors at the 0.9 floor ──
    post_eq = [posterior_from_weight(w, 0.0) for w in totals]
    assert any(pe >= 0.9 > pp for pe, pp in zip(post_eq, posteriors))

    # ── An equal-odds substitution would change the stored value ────────
    m_eq = clamp(sum(p for p, f in zip(post_eq, fired) if f) / sum(post_eq))
    w_eq = math.log2(m_eq / u_fire)
    assert abs(w_eq - stored_w) > 1e-6

    # ── A count-based m_fire (fired/confident counts) would too ─────────
    confident = [p >= 0.9 for p in posteriors]
    m_count = clamp(
        sum(1 for f, c in zip(fired, confident) if f and c) / sum(confident)
    )
    w_count = math.log2(m_count / u_fire)
    assert abs(w_count - stored_w) > 1e-6


def test_fan_out_runs_under_posterior_mode(monkeypatch):
    """The lever's posterior math is mode-independent: it RUNS (does not
    skip) under GOLDENMATCH_FS_CALIBRATED=posterior -- only the calibration
    lever's skip is mode-sensitive."""
    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
    conversion = _fanout_conversion()

    res = upgrade_splink_conversion(
        conversion, _fanout_df(), levers={"fan_out"}, measure=False
    )

    mk = res.upgraded_config.get_matchkeys()[0]
    assert [ne.field for ne in (mk.negative_evidence or [])] == ["phone"]


# ── Cluster-guard tuning (Task F4) ───────────────────────────────────────────


def _guard_df():
    """The F3 fixture frame with a unique ``rec_id`` id column (0..199) so
    guard tuning can join a reference (``rec_id`` is not a default id-column
    name, so tests pass ``id_column="rec_id"`` explicitly)."""
    return _fanout_df().with_row_index("rec_id")


def _labels_df(n_rows, big_cluster_size, prefix="c"):
    """rec_id 0..n-1: one cluster of ``big_cluster_size``, rest singletons."""
    clusters = [
        f"{prefix}big" if i < big_cluster_size else f"{prefix}{i}"
        for i in range(n_rows)
    ]
    return pl.DataFrame({"rec_id": list(range(n_rows)), "cluster": clusters})


def _guard_findings(result, severity=None):
    return [
        f
        for f in _fan_out_findings(result, severity)
        if "max_cluster_size" in f.message or "guard tuning" in f.message
    ]


def test_guard_tuned_from_labels():
    # Symmetric clamp: the floor wins (max(10, 2*4)=10), mid-size doubles
    # (30 -> 60), and a genuinely-large reference RAISES the default 100
    # (80 -> 160).
    for ref_max, expected in ((4, 10), (30, 60), (80, 160)):
        res = upgrade_splink_conversion(
            _fanout_conversion(),
            _guard_df(),
            labels=_labels_df(200, ref_max),
            id_column="rec_id",
            levers={"fan_out"},
            measure=False,
        )

        gr = res.upgraded_config.golden_rules
        assert gr is not None
        assert gr.max_cluster_size == expected

        tuned = [
            f for f in _guard_findings(res, "info") if "->" in f.message
        ]
        assert len(tuned) == 1
        msg = tuned[0].message
        assert f"100 -> {expected}" in msg
        assert "labels" in msg
        assert str(ref_max) in msg


def test_guard_prefers_labels_over_splink_clusters():
    res = upgrade_splink_conversion(
        _fanout_conversion(),
        _guard_df(),
        labels=_labels_df(200, 4),
        splink_clusters=_labels_df(200, 30, prefix="s"),
        id_column="rec_id",
        levers={"fan_out"},
        measure=False,
    )

    gr = res.upgraded_config.golden_rules
    assert gr is not None
    # labels (max 4 -> cap 10) win over splink_clusters (max 30 -> cap 60).
    assert gr.max_cluster_size == 10
    tuned = [f for f in _guard_findings(res, "info") if "->" in f.message]
    assert len(tuned) == 1
    assert "labels" in tuned[0].message
    assert "splink_clusters" not in tuned[0].message


def test_guard_skips_without_reference():
    res = upgrade_splink_conversion(
        _fanout_conversion(), _guard_df(), id_column="rec_id",
        levers={"fan_out"}, measure=False,
    )

    # BASELINE default semantics preserved: no golden_rules invented, so the
    # runtime default max_cluster_size=100 stays in effect.
    assert res.upgraded_config.golden_rules is None
    skips = [
        f for f in _guard_findings(res, "info") if "no reference" in f.message
    ]
    assert len(skips) == 1


def test_guard_skips_on_unjoinable_ids():
    # Reference ids share nothing with the data's rec_id values.
    disjoint = pl.DataFrame(
        {"rec_id": [f"X{i}" for i in range(200)], "cluster": ["c"] * 200}
    )
    res = upgrade_splink_conversion(
        _fanout_conversion(),
        _guard_df(),
        labels=disjoint,
        id_column="rec_id",
        levers={"fan_out"},
        measure=False,
    )

    assert res.upgraded_config.golden_rules is None
    skips = [
        f for f in _guard_findings(res, "info") if "id_column" in f.message
    ]
    assert len(skips) == 1
    assert "labels" in skips[0].message

    # Positional fallback: no id_column and no id-ish column in the df --
    # positional indices cannot join a reference, even one keyed 0..n-1.
    res2 = upgrade_splink_conversion(
        _fanout_conversion(),
        _fanout_df(),  # first_name/surname/city/phone only
        labels=_labels_df(200, 30),
        levers={"fan_out"},
        measure=False,
    )

    assert res2.upgraded_config.golden_rules is None
    skips2 = [
        f for f in _guard_findings(res2, "info") if "id_column" in f.message
    ]
    assert len(skips2) == 1
    assert "positional" in skips2[0].message


def test_guard_baseline_untouched():
    conversion = _fanout_conversion()

    res = upgrade_splink_conversion(
        conversion,
        _guard_df(),
        labels=_labels_df(200, 30),
        id_column="rec_id",
        levers={"fan_out"},
        measure=False,
    )

    # The guard DID tune the upgraded copy...
    assert res.upgraded_config.golden_rules is not None
    assert res.upgraded_config.golden_rules.max_cluster_size == 60
    # ...while baseline + original conversion stay untouched (copy-on-write).
    assert conversion.config.golden_rules is None
    assert res.baseline_config.golden_rules is None


def test_guard_bad_id_column_warns():
    res = upgrade_splink_conversion(
        _fanout_conversion(),
        _guard_df(),
        labels=_labels_df(200, 30),
        id_column="nope",
        levers={"fan_out"},
        measure=False,
    )

    # Never-fail contract: _resolve_ids raises SplinkUpgradeError for a
    # missing id_column; the guard wraps it into a warn+skip.
    assert res.upgraded_config.golden_rules is None
    warns = [f for f in _guard_findings(res, "warning") if "nope" in f.message]
    assert len(warns) == 1
    assert "skipped" in warns[0].message.lower()
