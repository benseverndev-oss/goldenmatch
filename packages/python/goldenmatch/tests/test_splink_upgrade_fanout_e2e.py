"""Task F6: the fan-out lever E2E success bar.

Spec: docs/superpowers/specs/2026-07-14-fanout-ne-upgrade-lever-design.md
("Testing / success bar")

The deterministic end-to-end test the fan_out lever exists to satisfy: a
homonym-shaped fixture in the ``tests/test_fs_ne_e2e.py`` style -- distinct
people sharing name+city but differing on a ``phone`` column the Splink
settings never referenced -- run through convert -> ``upgrade_splink_conversion``
with labels. The baseline conversion merges the homonym traps; the lever
detects the risk and adds phone negative evidence with estimated weights; the
measured upgraded run separates the traps while true duplicates still merge;
and ``vs_labels`` pairwise F1 STRICTLY improves baseline -> upgraded.

Fixture design (why the upgraded run genuinely separates the traps -- every
number below is load-bearing, worked out against the real calibration
geometry rather than hoped for):

- The lever pipeline separates traps through the CALIBRATED link threshold:
  runtime FS scoring merges pairs with normalized score >= link, where
  normalized = (total_weight - min) / (max - min) over ``fs_weight_range``
  (NE-inclusive). Trap pairs (all regular fields agree, NE fires) normalize
  to R / (R + W) where R is the regular-field weight range and W = |w_fired|;
  true-duplicate pairs (NE never fires) normalize to exactly 1.0. The
  calibration lever's percentile cut (``compute_thresholds``: rank
  ``1 - 2 * match_rate`` clamped to [0.40, 0.95]) always lands AT or BELOW
  the trap band -- the 2x headroom factor guarantees it -- so the only
  reliable separation regime is trap_norm < 0.40 (the link clamp floor),
  i.e. W > 1.5 * R. The whole fixture is engineered to reach that regime.
- W is estimated from the data as |log2(m_fire / u_fire)| with m_fire ~= the
  trap fraction among confident pairs and u_fire ~= 1 (random pairs almost
  always differ on phone), so W tops out near log2(1/0.02) ~= 5.6 (the risk
  gate's 2% rate floor caps how small m_fire can be). With 12 trap pairs
  among 192 blocked pairs, m_fire ~= 0.0625 -> W ~= 4 bits. R must therefore
  stay well under ~2.7 bits.
- Weak-agreement / near-zero-disagreement trained weights keep R tiny while
  the model stays usable: exact-agree m=0.10/u=0.05 (+1.0 bit) with ELSE
  m=0.90/u=0.95 (-0.078 bits) per name field. A LOW m is a model that says
  "agreement is rare even among matches" -- the data's true duplicates still
  agree on everything; m/u only set the weights. R ~= 2.22, so
  trap_norm ~= 2.22 / (2.22 + 4.0) ~= 0.36 < 0.40 while dup pairs sit at 1.0
  and the calibrated link lands exactly on the 0.40 floor.
- Every block is one identity group (each group gets a unique city; blocking
  is on city). Weak fields cannot tell background pairs from matches (an
  all-differ pair scores ~-0.16 bits -> equal-odds posterior ~0.47), so
  mixed blocks would wreck the within-block prior estimate and drop group
  pairs below the fan-out gate's 0.9 confidence floor. Pure-group blocks
  keep every blocked pair at the same (agreeing) weight: the equal-odds
  prior estimate lands at ~0.81, and group-pair posteriors at ~0.945 >= 0.9,
  so the risk gate sees all 192 pairs as confident merges. (This differs
  from ``test_fs_ne_e2e``'s mixed-identity-block rule, which exists for
  EM-TRAINED configs where saturation is the failure mode; here the model
  is IMPORTED, never trained, so there is no EM to starve.)
- ``phone`` appears in the DATA but not in the Splink settings, is fully
  populated, and lands mid-range on the candidate cardinality gate
  (144 distinct / 264 rows = 0.545 in [0.5, 0.999]); the 60 unique-phone
  filler singletons exist to hold that ratio up (dup groups share phones,
  which alone would sink it below 0.5). Fillers get unique cities ->
  singleton blocks -> zero pairs, so they never disturb the pair geometry.
- Baseline failure is real, not vacuous: the baseline config has no
  link_threshold, so runtime falls back to the fixed 0.50 default, and both
  trap and dup pairs normalize to 1.0 -> both merge -> baseline pairwise F1
  = 180/(180+12) precision < 1.0. An in-test precondition assert pins this.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.from_splink import from_splink
from goldenmatch.config.splink_upgrade import upgrade_splink_conversion

# ── Trained Splink settings (given_name + surname + city, blocking on city) ──
# Level m/u values sum to 1.0 across the non-null levels of each comparison,
# so import_em's re-normalization is a no-op (mirrors
# tests/test_from_splink_model_import.py's settings shape).

N_DUP_GROUPS = 60      # true-duplicate groups of 3 (one city block each)
N_TRAP_GROUPS = 12     # homonym-trap pairs of 2 (one city block each)
N_FILLERS = 60         # unique-everything singletons (cardinality ballast)


def _trained_exact_comparison(column: str, m_agree: float, u_agree: float) -> dict:
    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "m_probability": m_agree,
                "u_probability": u_agree,
            },
            {
                "sql_condition": "ELSE",
                "m_probability": round(1.0 - m_agree, 12),
                "u_probability": round(1.0 - u_agree, 12),
            },
        ],
    }


def _trained_settings() -> dict:
    return {
        "comparisons": [
            # +1.0 / -0.078 bits: weak agree, near-zero disagree (see module
            # docstring for why R must stay small).
            _trained_exact_comparison("given_name", 0.10, 0.05),
            _trained_exact_comparison("surname", 0.10, 0.05),
            # city always agrees within a block; keep its span negligible.
            _trained_exact_comparison("city", 0.05, 0.048),
        ],
        "blocking_rules_to_generate_predictions": ['l."city" = r."city"'],
        "probability_two_random_records_match": 0.01,
    }


def _build_fixture() -> tuple[pl.DataFrame, pl.DataFrame]:
    """264-row frame + ground-truth labels (see module docstring).

    - 60 true-duplicate groups of 3: identical given_name/surname/city and
      the SAME phone (NE never fires) -> one labeled cluster each.
    - 12 homonym traps of 2: identical given_name/surname/city (two distinct
      people colliding on name+city) but DIFFERENT phones -> two singleton
      labels each.
    - 60 fillers: unique everything (incl. city -> singleton blocks).
    """
    rows: list[dict] = []
    clusters: list[str] = []

    for g in range(N_DUP_GROUPS):
        for _ in range(3):
            rows.append(
                {
                    "given_name": f"dupfn{g}",
                    "surname": f"dupsn{g}",
                    "city": f"dupcity{g}",
                    "phone": f"555-1{g:06d}",
                }
            )
            clusters.append(f"dup{g}")

    for t in range(N_TRAP_GROUPS):
        for side in ("a", "b"):
            rows.append(
                {
                    "given_name": f"trapfn{t}",
                    "surname": f"trapsn{t}",
                    "city": f"trapcity{t}",
                    "phone": f"555-2{t:04d}{'1' if side == 'a' else '2'}",
                }
            )
            clusters.append(f"trap{t}{side}")

    for i in range(N_FILLERS):
        rows.append(
            {
                "given_name": f"fillfn{i}",
                "surname": f"fillsn{i}",
                "city": f"fillcity{i}",
                "phone": f"555-3{i:06d}",
            }
        )
        clusters.append(f"fill{i}")

    df = pl.DataFrame(rows).with_row_index("rec_id")
    labels = pl.DataFrame(
        {"rec_id": list(range(len(rows))), "cluster": clusters}
    ).with_columns(pl.col("rec_id").cast(df["rec_id"].dtype))
    return df, labels


def test_fanout_lever_success_bar():
    df, labels = _build_fixture()
    conversion = from_splink(_trained_settings())
    assert conversion.em_model is not None  # trained input imported a model

    res = upgrade_splink_conversion(
        conversion, df, labels=labels, id_column="rec_id"
    )  # measure=True default: full baseline + upgraded dedupe runs

    # ── The lever added phone negative evidence with estimated weights ──────
    mk = res.upgraded_config.get_matchkeys()[0]
    assert mk.negative_evidence is not None, (
        "fan_out lever did not add negative evidence -- the risk gate "
        "(confident-pair contradiction rate / support floors) did not fire "
        "on the homonym fixture"
    )
    assert [ne.field for ne in mk.negative_evidence] == ["phone"]
    ne = mk.negative_evidence[0]
    assert ne.penalty_bits is None  # EM-learned shape, not a fixed override

    assert res.em_model is not None
    key = "__ne__phone"
    assert key in res.em_model.match_weights
    assert res.em_model.match_weights[key][0] < 0, (
        "estimated NE fired-weight must be negative (firing is rarer among "
        "likely matches than among random pairs)"
    )
    assert res.em_model.match_weights[key][1] == 0.0

    # ── Copy-on-write: baseline config + model stay untouched ───────────────
    assert conversion.config.get_matchkeys()[0].negative_evidence is None
    assert res.baseline_config.get_matchkeys()[0].negative_evidence is None
    assert not any(
        k.startswith("__ne__") for k in conversion.em_model.match_weights
    )
    assert not any(k.startswith("__ne__") for k in conversion.em_model.m_probs)
    assert not any(k.startswith("__ne__") for k in conversion.em_model.u_probs)

    # ── Guard tuning from labels ─────────────────────────────────────────────
    gr = res.upgraded_config.golden_rules
    assert gr is not None
    # Largest true cluster is a 3-row dup group -> max(10, 2 * 3) = 10.
    assert gr.max_cluster_size == 10
    guard_findings = [
        f
        for f in res.report.findings
        if f.splink_path == "upgrade:fan_out"
        and "max_cluster_size" in f.message
        and "labels" in f.message
    ]
    assert len(guard_findings) == 1

    # ── The measured success bar ─────────────────────────────────────────────
    m = res.measurement
    assert m is not None
    assert m.vs_labels is not None

    baseline_f1 = m.vs_labels.baseline["pairwise_f1"]
    upgraded_f1 = m.vs_labels.upgraded["pairwise_f1"]

    # Fixture-validity PRECONDITION: the baseline run demonstrably merges the
    # homonym traps (otherwise the improvement below would be vacuous).
    assert baseline_f1 < 1.0, (
        f"baseline pairwise F1 is {baseline_f1} -- the baseline conversion "
        "no longer merges the homonym traps, so the fixture no longer "
        "demonstrates the fan-out failure this lever exists to fix; "
        "strengthen the shared name+city evidence"
    )
    # The baseline still finds the true duplicates (recall failure would be a
    # different, unrelated fixture defect).
    assert baseline_f1 > 0.5

    # THE BAR (spec "Testing / success bar"): strict pairwise-F1 improvement.
    assert upgraded_f1 > baseline_f1, (
        f"upgraded pairwise F1 ({upgraded_f1}) did not strictly improve on "
        f"baseline ({baseline_f1}) -- the suggested negative evidence and "
        "calibrated threshold failed to separate the homonym traps"
    )
