import arrow_finish_line_sweep as afl
from arrow_finish_line_sweep import (
    PHASE_CRITERIA,
    Criterion,
    PhaseVerdict,
    classify_phase,
    parse_bench_json,
    parse_native_speedup,
    render_markdown_table,
    run_phase_bench,
)

from tests.fixtures.realistic_person import realistic_person_df


# Task 1 tests: classifier
def test_pass_when_all_ratio_criteria_met():
    crits = [
        Criterion(name="wall", kind="ratio_le", target=0.50),
        Criterion(name="rss", kind="ratio_le", target=0.25),
        Criterion(name="parity", kind="bool_true", target=True),
    ]
    metrics = {"wall": {"new": 10.0, "legacy": 25.0},
               "rss":  {"new": 2.0,  "legacy": 10.0},
               "parity": True}
    assert classify_phase(crits, metrics).verdict == "PASS"


def test_close_when_perf_beats_legacy_but_misses_target():
    crits = [Criterion(name="wall", kind="ratio_le", target=0.50),
             Criterion(name="parity", kind="bool_true", target=True)]
    metrics = {"wall": {"new": 20.0, "legacy": 25.0}, "parity": True}
    assert classify_phase(crits, metrics).verdict == "CLOSE"


def test_blocked_when_parity_fails():
    crits = [Criterion(name="wall", kind="ratio_le", target=0.50),
             Criterion(name="parity", kind="bool_true", target=True)]
    metrics = {"wall": {"new": 5.0, "legacy": 25.0}, "parity": False}
    assert classify_phase(crits, metrics).verdict == "BLOCKED"


def test_blocked_when_metric_missing():
    crits = [Criterion(name="wall", kind="ratio_le", target=0.50)]
    assert classify_phase(crits, metrics={}).verdict == "BLOCKED"


def test_speedup_ge_pass_and_close():
    crits = [Criterion(name="build_clusters", kind="speedup_ge", target=2.0),
             Criterion(name="parity", kind="bool_true", target=True)]
    fast = {"build_clusters": {"new": 5.0, "legacy": 12.0}, "parity": True}
    slow = {"build_clusters": {"new": 9.0, "legacy": 12.0}, "parity": True}
    assert classify_phase(crits, fast).verdict == "PASS"
    assert classify_phase(crits, slow).verdict == "CLOSE"


def test_abs_le():
    crits = [Criterion(name="golden_wall_s", kind="abs_le", target=60.0),
             Criterion(name="parity", kind="bool_true", target=True)]
    assert classify_phase(crits, {"golden_wall_s": 55.0, "parity": True}).verdict == "PASS"
    assert classify_phase(crits, {"golden_wall_s": 80.0, "parity": True}).verdict == "CLOSE"


def test_unknown_kind_raises():
    import pytest
    bad = Criterion(name="x", kind="ratio_le", target=0.5)
    object.__setattr__(bad, "kind", "bogus")  # frozen dataclass bypass
    with pytest.raises(ValueError, match="unknown criterion kind"):
        classify_phase([bad], {"x": {"new": 1.0, "legacy": 2.0}})


# Task 2 tests: registry
def test_registry_covers_phases_1_through_6():
    assert set(PHASE_CRITERIA) == {"phase1", "phase2", "phase3", "phase4", "phase5", "phase6"}


def test_phase1_criteria_match_reframed_gate():
    # Reframed Phase 1 gate: feasibility (columnar completes 5M) + parity,
    # both bool_true binding criteria. Wall/RSS are no longer gating
    # (un-measurable at 5M where legacy OOMs).
    names = {c.name: c for c in PHASE_CRITERIA["phase1"]}
    assert names["columnar_completes_5m"].kind == "bool_true"
    assert names["columnar_completes_5m"].target is True
    assert names["parity"].kind == "bool_true"
    assert names["parity"].target is True
    # No ratio_le wall/rss gating criterion remains.
    assert all(c.kind != "ratio_le" for c in PHASE_CRITERIA["phase1"])
    assert "wall" not in names and "rss" not in names


def test_phase1_bench_scale_is_1m():
    assert afl.PHASE_BENCH_SCALE["phase1"] == 1_000_000


def test_phase3_has_three_speedup_gates():
    kinds = {(c.name, c.kind, c.target) for c in PHASE_CRITERIA["phase3"]}
    assert ("dedup", "speedup_ge", 5.0) in kinds
    assert ("build_clusters", "speedup_ge", 2.0) in kinds
    assert ("fingerprints", "speedup_ge", 3.0) in kinds


# Task 3 tests: parsers + emit
def test_parse_bench_json_extracts_last_marker_line():
    out = 'noise\n__BENCH_JSON__{"total_wall_s": 12.5, "peak_rss_mb": 800}\nmore'
    d = parse_bench_json(out)
    assert d["total_wall_s"] == 12.5 and d["peak_rss_mb"] == 800


def test_parse_bench_json_returns_none_when_absent():
    assert parse_bench_json("no marker here") is None


def test_parse_native_speedup_reads_table_line():
    out = "  native(Vec) speedup vs python          :     2.41x\n"
    assert parse_native_speedup(out, label="speedup vs python") == 2.41


def test_parse_native_speedup_none_when_absent():
    assert parse_native_speedup("no speedup here", label="speedup vs python") is None


def test_render_markdown_table_has_row_per_phase():
    rows = {"phase1": PhaseVerdict("PASS", ["wall: OK"]),
            "phase5": PhaseVerdict("BLOCKED", ["metric missing"])}
    md = render_markdown_table(rows)
    assert "| phase1 | PASS |" in md
    assert "| phase5 | BLOCKED |" in md
    assert md.startswith("| Phase | Verdict | Detail |")


# Task 5 tests: driver failure-isolation (no bench box / native ext needed)
def test_run_phase_bench_isolates_subprocess_failure(monkeypatch):
    # a failing bench subprocess must leave gating metrics ABSENT (-> BLOCKED),
    # never crash the sweep.
    monkeypatch.setattr(afl, "_run_subprocess", lambda cmd, timeout_s: (None, "injected failure"))
    metrics = run_phase_bench("phase1", "smoke")
    assert "wall" not in metrics and "rss" not in metrics
    assert "_note" in metrics
    assert classify_phase(PHASE_CRITERIA["phase1"], metrics).verdict == "BLOCKED"


def test_run_phase_bench_unknown_phase_raises():
    import pytest
    with pytest.raises(ValueError, match="unknown phase"):
        run_phase_bench("phase99", "smoke")


# Task 4 test: fixture smoke
def test_realistic_person_shape_and_identity_ratio():
    df = realistic_person_df(30_000, seed=42)
    assert df.height == 30_000
    # fixture guarantees a wide surname pool (>=5000 distinct at n>=15000);
    # assert well above the degenerate-block danger zone.
    assert df["last_name"].n_unique() >= 4_000
