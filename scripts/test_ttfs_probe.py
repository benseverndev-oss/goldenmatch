"""Unit tests for the ttfs-probe PURE logic. No Docker, no goldenmatch, no
network -- exercises the cluster->pairs expansion, the P/R/F1 math, and the
honest-failure row encoding. Run:
    python -m pytest scripts/test_ttfs_probe.py -q
"""

import importlib.util
import pathlib
import sys

import pytest

_spec = importlib.util.spec_from_file_location(
    "ttfs_probe", pathlib.Path(__file__).parent / "ttfs_probe.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


# --------------------------------------------------------------------------
# Cluster CSV -> predicted pairs
# --------------------------------------------------------------------------

_CLUSTERS_CSV = (
    "__cluster_id__,__row_id__,__cluster_size__,__oversized__\n"
    "0,0,2,false\n"
    "0,1,2,false\n"
    "1,2,1,false\n"
    "2,3,3,false\n"
    "2,4,3,false\n"
    "2,5,3,false\n"
)


def test_parse_clusters_csv_reads_id_and_row():
    rows = mod.parse_clusters_csv(_CLUSTERS_CSV)
    assert len(rows) == 6
    assert rows[0] == (0, 0)
    assert rows[-1] == (2, 5)


def test_parse_clusters_csv_empty_is_empty_not_an_error():
    # A run that clustered nothing still writes a header (or nothing at all).
    assert mod.parse_clusters_csv("__cluster_id__,__row_id__\n") == []
    assert mod.parse_clusters_csv("") == []


def test_pairs_from_cluster_rows_expands_and_skips_singletons():
    pairs = mod.pairs_from_cluster_rows(mod.parse_clusters_csv(_CLUSTERS_CSV))
    # cluster 0 -> (0,1); cluster 1 is a singleton -> nothing; cluster 2 -> 3 pairs
    assert pairs == {(0, 1), (3, 4), (3, 5), (4, 5)}


def test_pairs_are_canonically_ordered_regardless_of_member_order():
    rows = [(7, 9), (7, 4)]  # members arrive high-then-low
    assert mod.pairs_from_cluster_rows(rows) == {(4, 9)}


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------


def test_load_ground_truth_reads_canonical_pairs():
    gt = mod.load_ground_truth("id_a,id_b\n1,0\n3,4\n")
    assert gt == {(0, 1), (3, 4)}


def test_load_ground_truth_rejects_missing_columns():
    with pytest.raises(ValueError, match="id_a"):
        mod.load_ground_truth("left,right\n0,1\n")


# --------------------------------------------------------------------------
# P/R/F1
# --------------------------------------------------------------------------


def test_prf1_perfect():
    truth = {(0, 1), (2, 3)}
    r = mod.prf1(set(truth), truth)
    assert r["tp"] == 2 and r["fp"] == 0 and r["fn"] == 0
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["f1"] == 1.0


def test_prf1_partial():
    truth = {(0, 1), (2, 3), (4, 5)}
    pred = {(0, 1), (9, 9)}
    r = mod.prf1(pred, truth)
    assert (r["tp"], r["fp"], r["fn"]) == (1, 1, 2)
    assert r["precision"] == pytest.approx(0.5)
    assert r["recall"] == pytest.approx(1 / 3)
    assert r["f1"] == pytest.approx(0.4)


def test_prf1_empty_prediction_is_zero_not_a_crash():
    r = mod.prf1(set(), {(0, 1)})
    assert r["f1"] == 0.0 and r["precision"] == 0.0 and r["recall"] == 0.0


def test_prf1_empty_truth_is_zero_not_a_crash():
    # Guards the degenerate fixture case: never fabricate a 1.0 from no labels.
    r = mod.prf1({(0, 1)}, set())
    assert r["f1"] == 0.0


# --------------------------------------------------------------------------
# Row-id alignment guard
# --------------------------------------------------------------------------
# The probe keys ground truth on INPUT ROW INDEX and trusts `__row_id__` to be
# that same index. If a pipeline change ever renumbers or filters rows, the F1
# would silently collapse and read as a quality regression. This turns that
# into a loud, named failure instead.


def test_check_row_ids_accepts_ids_within_range():
    mod.check_row_ids([(0, 0), (0, 4)], n_input_rows=5)


def test_check_row_ids_rejects_out_of_range():
    with pytest.raises(ValueError, match="row_id"):
        mod.check_row_ids([(0, 0), (0, 5)], n_input_rows=5)


def test_check_row_ids_rejects_negative():
    with pytest.raises(ValueError, match="row_id"):
        mod.check_row_ids([(0, -1)], n_input_rows=5)


# --------------------------------------------------------------------------
# The honest-failure row contract
# --------------------------------------------------------------------------
# Three distinguishable states, never conflated:
#   ok=True             -- installed, ran, cleared the floor
#   ok=False + ttfs_fail -- it ran and we learned something bad
#   ok=None             -- the probe itself could not run (no Docker); NOT a failure


def test_build_row_success():
    row = mod.build_row(install_s=40.0, run_s=5.0, f1=0.94, floor=0.90)
    assert row["ttfs_ok"] is True
    assert row["ttfs_fail"] is None
    assert row["ttfs_install_s"] == 40.0
    assert row["ttfs_run_s"] == 5.0
    assert row["ttfs_total_s"] == 45.0
    assert row["ttfs_f1"] == 0.94


def test_build_row_install_failure_keeps_the_time_it_burned():
    row = mod.build_row(install_s=12.5, run_s=None, f1=None, floor=0.90, fail="install")
    assert row["ttfs_ok"] is False
    assert row["ttfs_fail"] == "install"
    assert row["ttfs_install_s"] == 12.5  # time to the failure is still data
    assert row["ttfs_run_s"] is None
    assert row["ttfs_total_s"] is None  # no total without both halves
    assert row["ttfs_f1"] is None


def test_build_row_run_failure():
    row = mod.build_row(install_s=40.0, run_s=3.0, f1=None, floor=0.90, fail="run")
    assert row["ttfs_ok"] is False
    assert row["ttfs_fail"] == "run"
    assert row["ttfs_total_s"] == 43.0
    assert row["ttfs_f1"] is None


def test_build_row_below_floor_is_a_named_failure_not_a_missing_value():
    # The bug class this repo keeps hitting: a bad result that renders as "—"
    # and reads as "we didn't measure" instead of "we measured, it was bad".
    row = mod.build_row(install_s=40.0, run_s=5.0, f1=0.31, floor=0.90)
    assert row["ttfs_ok"] is False
    assert row["ttfs_fail"] == "f1_below_floor"
    assert row["ttfs_f1"] == 0.31  # the number survives
    assert row["ttfs_total_s"] == 45.0  # so do the timings


def test_build_row_exactly_at_the_floor_passes():
    row = mod.build_row(install_s=1.0, run_s=1.0, f1=0.90, floor=0.90)
    assert row["ttfs_ok"] is True


def test_unavailable_row_is_distinct_from_a_failure():
    row = mod.unavailable_row("docker not found")
    assert row["ttfs_ok"] is None  # NOT False
    assert row["ttfs_fail"] is None  # nothing was learned about the product
    assert row["ttfs_install_s"] is None
    assert row["ttfs_f1"] is None
    assert "docker" in row["ttfs_note"]


def test_every_row_shape_carries_the_same_keys():
    # The scoreboard renders these positionally; a missing key would KeyError
    # the nightly rather than degrade.
    ok = mod.build_row(install_s=1.0, run_s=1.0, f1=1.0, floor=0.9)
    bad = mod.build_row(install_s=1.0, run_s=None, f1=None, floor=0.9, fail="install")
    none = mod.unavailable_row("no docker")
    assert set(ok) == set(bad) == set(none) == set(mod.ROW_KEYS)


# ---------------------------------------------------------------------------
# diagnostic_note -- the probe's only window into WHY a first run failed
# ---------------------------------------------------------------------------


def test_note_takes_the_run_log_for_a_run_failure():
    """The regression this function exists for.

    The 2026-08-29 nightly recorded `ttfs_fail: "run"` with a note containing
    only pip's "A new release of pip is available" notice -- the real cause
    ("Auto-config error: No module named 'polars'") never reached the board,
    because the note was the tail of stdout+stderr and pip's stderr came last.
    """
    note = mod.diagnostic_note(
        phase="run",
        install_log="Successfully installed goldenmatch-3.16.0\n"
        "[notice] A new release of pip is available: 25.0.1 -> 26.2.1",
        run_log="No config file - auto-detecting column types...\n"
        "Auto-config error: No module named 'polars'",
        run_rc=1,
    )
    assert "No module named 'polars'" in note
    assert "new release of pip" not in note


def test_note_takes_the_install_log_for_an_install_failure():
    note = mod.diagnostic_note(
        phase="install",
        install_log="ERROR: Could not find a version that satisfies goldenmatch",
        run_log="",
    )
    assert "Could not find a version" in note


def test_note_carries_the_exit_code_for_a_run_failure():
    """"Exited 1 and said nothing" is itself the finding, so the code is kept
    even when the log is empty."""
    note = mod.diagnostic_note(phase="run", install_log="", run_log="", run_rc=1)
    assert note.startswith("[exit 1]")


def test_note_on_an_empty_run_log_says_so_rather_than_recording_nothing():
    note = mod.diagnostic_note(phase="run", install_log="x", run_log="", run_rc=1)
    assert note is not None
    assert "no output" in note


def test_note_falls_back_to_the_docker_stream_when_no_container_log_exists():
    """A daemon-level failure writes neither in-container log; the docker
    client's own output is then the only evidence there is."""
    note = mod.diagnostic_note(
        phase="install",
        install_log="",
        run_log="",
        fallback="docker: Error response from daemon: no such image",
    )
    assert "no such image" in note


def test_note_is_tail_truncated_to_the_budget():
    note = mod.diagnostic_note(
        phase="run", install_log="", run_log="A" * 5000 + "THE_ACTUAL_ERROR", run_rc=2
    )
    assert len(note) <= mod._NOTE_CHARS
    # Truncation keeps the END, where the error is.
    assert note.endswith("THE_ACTUAL_ERROR")


def test_note_install_phase_omits_the_run_exit_code():
    """An install failure never reached the run, so there is no run status to
    report -- labelling one would be inventing a fact."""
    note = mod.diagnostic_note(
        phase="install", install_log="pip exploded", run_log="", run_rc=None
    )
    assert not note.startswith("[exit")
