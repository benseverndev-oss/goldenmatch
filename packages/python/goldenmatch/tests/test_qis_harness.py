"""#510 quality-invariant scale harness tests. Imports the repo-root script
(scripts/quality_invariant_scale.py) by path; runs in the `python` lane (no Ray)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest  # noqa: F401  # used by slow-marked tests added in later tasks

# Repo root is 4 parents up from this file:
# packages/python/goldenmatch/tests/<this> -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import quality_invariant_scale as qis  # noqa: E402


def test_corrupt_cell_types_are_deterministic_and_string_valued():
    # transpose (type_sel<0.25): "abcd" with pos 0 -> "bacd"
    assert qis._corrupt_cell("abcd", 0.10, 0.0) == "bacd"
    # delete (0.25<=type_sel<0.50): "abcd" pos 0 -> "bcd"
    assert qis._corrupt_cell("abcd", 0.30, 0.0) == "bcd"
    # token drop (0.50<=type_sel<0.75) on multi-token: "12 main st" drop tok 0
    out = qis._corrupt_cell("12 main st", 0.60, 0.0)
    assert out == "main st"
    # whole-field null (type_sel>=0.75) -> empty
    assert qis._corrupt_cell("abcd", 0.90, 0.5) == ""
    # empty / single-char inputs never raise
    assert qis._corrupt_cell("", 0.10, 0.0) == ""
    assert qis._corrupt_cell("x", 0.10, 0.0) in ("x", "")


def test_apply_field_corruption_deterministic_given_same_stream():
    # Same field stream + same input -> identical corruption. (Cross-n prefix
    # stability is a separate, stronger property asserted at the generator level
    # in test_generate_corruption_prefix_stable_across_n; the (n,3) block draw is
    # what guarantees it. This test only pins the determinism of one column pass.)
    base = [f"value{i:04d}" for i in range(50)]
    rng_a = np.random.default_rng(np.random.SeedSequence([0, 1]).spawn(1)[0])
    rng_b = np.random.default_rng(np.random.SeedSequence([0, 1]).spawn(1)[0])
    a = qis._apply_field_corruption(list(base), 0.5, rng_a)
    b = qis._apply_field_corruption([f"value{i:04d}" for i in range(50)], 0.5, rng_b)
    assert a == b


def test_generate_corruption_is_deterministic():
    df1, c1 = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    df2, c2 = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    assert df1.equals(df2)
    assert (c1 == c2).all()


def test_generate_corruption_prefix_stable_across_n():
    # The scale-invariance precondition: row i is byte-identical whether the
    # dataset is 1000 rows or 5000 rows (same seed, same corruption level).
    small, cs = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    big, cb = qis.generate_with_gt(5000, seed=0, shape="realistic", corruption="moderate")
    assert small.equals(big.head(1000))
    assert (cs == cb[:1000]).all()


def test_generate_corruption_preserves_oracle():
    # Corruption never moves a row's ground-truth cluster id; only the displayed
    # fields change. cids must equal the light-shape cids exactly.
    _, c_light = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="light")
    _, c_mod = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    assert (c_light == c_mod).all()


def test_generate_light_is_the_default_no_extra_corruption():
    # `light` is the INTERNAL default: no extra corruption on top of the existing
    # 10% a->@ typo. The default-arg call and explicit corruption="light" must
    # agree at the same N. NOTE: this does NOT claim parity with pre-#510 cached
    # numbers -- the prefix-stability refactor (per-field RNG streams) deliberately
    # reset the realistic-shape field VALUES (distribution unchanged). Task 3 must
    # tune `moderate` against a FRESH 1K oracle run, not a pre-branch JSON.
    df_default, _ = qis.generate_with_gt(1000, seed=0, shape="realistic")
    df_light, _ = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="light")
    assert df_default.equals(df_light)


def test_moderate_actually_corrupts_some_rows():
    df_light, _ = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="light")
    df_mod, _ = qis.generate_with_gt(1000, seed=0, shape="realistic", corruption="moderate")
    # At least the first_name column must differ on a meaningful fraction.
    diff = (df_light["first_name"] != df_mod["first_name"]).sum()
    assert diff > 50  # rate ~0.3 over 1000 rows; comfortably > 50


@pytest.mark.slow
def test_moderate_oracle_f1_in_target_band():
    # The tuning gate: `moderate` must land the 1K oracle in the drift-sensitive
    # 0.90-0.95 band (with a small tolerance so CI native/py float jitter and
    # platform RNG don't flake). If this fails after a deliberate rate change,
    # re-tune AND update the report.
    out = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate")
    f1 = out["pairwise"]["f1"]
    assert 0.88 <= f1 <= 0.96, f"moderate 1K pairwise F1 out of band: {f1:.4f}"
    assert out["cluster"]["f1"] > 0.5, f"cluster F1 degenerate: {out['cluster']['f1']:.4f}"
    assert out["corruption"] == "moderate"


@pytest.mark.slow
def test_qis_run_determinism_and_golden_shape():
    # #510 determinism check, scoped honestly. Same (seed, corruption) gives:
    #   - identical metrics (pairwise / B-cubed / cluster F1),
    #   - identical cluster PARTITION (clusters_signature) -- the load-bearing
    #     "deterministic clustering" claim,
    #   - identical golden SHAPE + a present golden frame.
    # It does NOT assert byte-identical golden VALUES: goldenmatch's golden
    # survivorship breaks value ties by input row order, which is not stably
    # sorted run-to-run, so heavily-corrupted text fields (first_name, email)
    # can pick different equally-valid survivors across reruns even though the
    # partition is identical. That survivorship-ordering gap is a tracked
    # goldenmatch follow-up; clustering reproducibility is what #510 needs and
    # is what's asserted here. (golden_hash stays in the artifact as a per-run
    # content fingerprint, not a cross-run determinism witness.)
    a = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate")
    b = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate")
    assert a["pairwise"] == b["pairwise"]
    assert a["b_cubed"] == b["b_cubed"]
    assert a["cluster"] == b["cluster"]
    assert a["clusters_signature"] == b["clusters_signature"]
    assert a["golden_shape"] is not None
    assert a["golden_shape"] == b["golden_shape"]


def _load_aggregate():
    import qis_aggregate  # on sys.path via _SCRIPTS
    return qis_aggregate


def test_aggregate_oracle_deltas_and_verdict():
    agg = _load_aggregate()
    rungs = [
        {"rows": 1000, "corruption": "moderate",
         "pairwise": {"f1": 0.920}, "b_cubed": {"f1": 0.930}, "cluster": {"f1": 0.700},
         "wall_s": {"total": 1.0}, "rss_mb_peak": 100.0,
         "predicted_clusters": 200, "multi_member_clusters": 180,
         "bench": {"scored_pair_count": 500}},
        {"rows": 1000000, "corruption": "moderate",
         "pairwise": {"f1": 0.918}, "b_cubed": {"f1": 0.929}, "cluster": {"f1": 0.695},
         "wall_s": {"total": 40.0}, "rss_mb_peak": 4000.0,
         "predicted_clusters": 200000, "multi_member_clusters": 180000,
         "bench": {"scored_pair_count": 500000}},
    ]
    report = agg.build_report(rungs)
    assert report["oracle_rows"] == 1000
    # 1M rung deltas vs the 1K oracle, all within targets -> PASS
    row_1m = next(r for r in report["rows"] if r["rows"] == 1000000)
    assert abs(row_1m["pairwise_delta"]) == pytest.approx(0.002, abs=1e-9)
    assert row_1m["passed"] is True
    assert report["verdict_passed"] is True
    assert "| rows |" in report["markdown"].lower()


def test_aggregate_flags_drift_as_fail():
    agg = _load_aggregate()
    rungs = [
        {"rows": 1000, "pairwise": {"f1": 0.920}, "b_cubed": {"f1": 0.930},
         "cluster": {"f1": 0.700}, "wall_s": {"total": 1.0}, "rss_mb_peak": 1.0,
         "predicted_clusters": 1, "multi_member_clusters": 1, "bench": {}},
        {"rows": 100000000, "pairwise": {"f1": 0.800}, "b_cubed": {"f1": 0.900},
         "cluster": {"f1": 0.690}, "wall_s": {"total": 500.0}, "rss_mb_peak": 1.0,
         "predicted_clusters": 1, "multi_member_clusters": 1, "bench": {}},
    ]
    report = agg.build_report(rungs)
    row = next(r for r in report["rows"] if r["rows"] == 100000000)
    assert row["passed"] is False               # pairwise delta 0.12 > 0.005
    assert report["verdict_passed"] is False
