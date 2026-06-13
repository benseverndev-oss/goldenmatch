"""#510 quality-invariant scale harness tests. Imports the repo-root script
(scripts/quality_invariant_scale.py) by path; runs in the `python` lane (no Ray)."""
from __future__ import annotations

import json
import os
import subprocess
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


def test_corrupt_cell_types_are_deterministic_and_never_empty():
    # transpose (type_sel < 1/3): "abcd" pos 0 -> "bacd"
    assert qis._corrupt_cell("abcd", 0.10, 0.0) == "bacd"
    # delete (1/3 <= type_sel < 2/3): "abcd" pos 0 -> "bcd"
    assert qis._corrupt_cell("abcd", 0.50, 0.0) == "bcd"
    # token drop (type_sel >= 2/3) on multi-token: "12 main st" drop tok 0
    assert qis._corrupt_cell("12 main st", 0.80, 0.0) == "main st"
    # token-drop range on a SINGLE-token string falls back to delete -> never "".
    out = qis._corrupt_cell("abcd", 0.90, 0.5)
    assert out and out != "abcd" and len(out) == 3
    # NO corruption ever returns an empty string (the mega-block hazard).
    for t in (0.10, 0.50, 0.90):
        assert qis._corrupt_cell("abcdef", t, 0.5) != ""
    # <2-char inputs are left unchanged (nothing safe to do; never null).
    assert qis._corrupt_cell("", 0.10, 0.0) == ""
    assert qis._corrupt_cell("x", 0.90, 0.0) == "x"


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
    # 0.90-0.95 band (with a small tolerance for float/RNG jitter). Uses the
    # FROZEN config -- the published-ladder methodology -- which is also what
    # keeps this fast (frozen apply ~25s on Linux vs ~150s for the per-rung
    # auto-config search on the ambiguous data). If this fails after a deliberate
    # corruption change, re-tune, rebuild the frozen config
    # (`--rebuild-frozen-config`), AND update the report.
    out = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate", frozen=True)
    f1 = out["pairwise"]["f1"]
    assert 0.88 <= f1 <= 0.96, f"moderate 1K pairwise F1 out of band: {f1:.4f}"
    assert out["cluster"]["f1"] > 0.5, f"cluster F1 degenerate: {out['cluster']['f1']:.4f}"
    assert out["corruption"] == "moderate"
    assert out["config_mode"] == "frozen"


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
    # partition is identical. That survivorship-ordering gap is tracked as
    # goldenmatch issue #870; clustering reproducibility is what #510 needs and
    # is what's asserted here. (golden_hash stays in the artifact as a per-run
    # content fingerprint, not a cross-run determinism witness.)
    a = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate", frozen=True)
    b = qis.run_rung(1000, seed=0, shape="realistic", corruption="moderate", frozen=True)
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
    # 1M rung deltas vs the 1K oracle, all within targets -> PASS. Assert all
    # three delta paths arithmetically (0.918-0.920, 0.929-0.930, 0.695-0.700).
    row_1m = next(r for r in report["rows"] if r["rows"] == 1000000)
    assert row_1m["pairwise_delta"] == pytest.approx(-0.002, abs=1e-9)
    assert row_1m["b_cubed_delta"] == pytest.approx(-0.001, abs=1e-9)
    assert row_1m["cluster_delta"] == pytest.approx(-0.005, abs=1e-9)
    assert row_1m["passed"] is True
    assert report["verdict_passed"] is True
    assert "| rows |" in report["markdown"].lower()


def test_aggregate_empty_rungs_is_a_safe_noop():
    agg = _load_aggregate()
    report = agg.build_report([])
    assert report["oracle_rows"] is None
    assert report["rows"] == []
    assert report["verdict_passed"] is True  # vacuous PASS sentinel


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


def _run_harness_subprocess(native_env: str, tmp_path):
    """Run the harness in a subprocess under GOLDENMATCH_NATIVE=native_env and
    return its parsed JSON, or None if the run failed (e.g. a stale/skewed native
    wheel) so the caller can skip rather than flake."""
    out = tmp_path / f"native_{native_env}.json"
    env = dict(os.environ)
    env["GOLDENMATCH_NATIVE"] = native_env
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    script = _SCRIPTS / "quality_invariant_scale.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--rows", "1000", "--corruption", "moderate",
         "--frozen", "--out", str(out)],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    if proc.returncode != 0 or not out.exists():
        # Surface a tail of the failure for local triage (e.g. an unexpected
        # pure-Python failure that trips `assert py is not None`); the None
        # return still drives the caller's skip on a stale/skewed native wheel.
        print(f"[qis native subprocess GOLDENMATCH_NATIVE={native_env} rc={proc.returncode}] "
              f"stderr tail: {(proc.stderr or '')[-500:]}", flush=True)
        return None
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.slow
def test_qis_native_parity(tmp_path):
    # native == pure-Python must produce the SAME cluster PARTITION and equal F1.
    # Golden VALUES are deliberately NOT compared: golden survivorship tie-order
    # is nondeterministic (issue #870), so the golden frame can differ native vs
    # pure even when both are correct. The partition (clusters_signature) + F1 are
    # the scale-independent parity claim, so 1K suffices. Skips cleanly when the
    # native kernel is absent OR present-but-skewed (a stale local wheel returns a
    # different arity than the in-tree caller expects); in CI's native lane the
    # kernel is freshly built and the body runs.
    from goldenmatch.core._native_loader import native_available

    py = _run_harness_subprocess("0", tmp_path)
    assert py is not None, "pure-Python harness run failed"
    assert "available" in py["native"]  # the native-witness field is always present
    if not native_available():
        pytest.skip("native kernel unavailable; pure-Python witness asserted")
    nat = _run_harness_subprocess("1", tmp_path)
    if nat is None:
        pytest.skip("native kernel present but errored (stale/skewed wheel); "
                    "native parity is validated in CI's native lane")
    assert nat["native"]["available"] is True
    assert py["pairwise"]["f1"] == pytest.approx(nat["pairwise"]["f1"], abs=1e-9)
    assert py["clusters_signature"] == nat["clusters_signature"]
