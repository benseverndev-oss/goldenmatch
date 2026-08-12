"""Unit tests for the main-health classifier. Pure data -- no gh, no network.
Run: python -m pytest scripts/test_main_health.py -q"""
import importlib.util
import pathlib
import sys

_spec = importlib.util.spec_from_file_location(
    "check_main_health", pathlib.Path(__file__).parent / "check_main_health.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


def test_classify_red_conclusions():
    for c in ("failure", "timed_out", "startup_failure"):
        assert mod.classify(c) == "red", c


def test_classify_ok_conclusions():
    for c in ("success", "skipped", "neutral", "cancelled", "action_required", None):
        assert mod.classify(c) == "ok", c


def test_classify_unknown_is_red():
    # A conclusion string GitHub might add later must never pass as healthy.
    assert mod.classify("some_new_status") == "red"


def test_red_workflows_filters_and_sorts():
    runs = [
        {"name": "Zeta", "conclusion": "failure", "html_url": "z"},
        {"name": "alpha", "conclusion": "success", "html_url": "a"},
        {"name": "Beta", "conclusion": "timed_out", "html_url": "b"},
    ]
    reds = mod.red_workflows(runs)
    assert [r["name"] for r in reds] == ["Beta", "Zeta"]  # name-sorted, success dropped


def test_red_workflows_empty_when_all_green():
    runs = [
        {"name": "a", "conclusion": "success"},
        {"name": "b", "conclusion": "skipped"},
        {"name": "c", "conclusion": None},
    ]
    assert mod.red_workflows(runs) == []


def test_select_main_run_ignores_stale_workflow_dispatch():
    # Newest main run is a manual dispatch failure -> ignored; the latest
    # AUTOMATIC run is the real main-health signal.
    runs = [
        {"conclusion": "failure", "event": "workflow_dispatch", "run_number": 9},
        {"conclusion": "success", "event": "push", "run_number": 8},
    ]
    assert mod.select_main_run(runs)["run_number"] == 8


def test_select_main_run_none_when_only_dispatch():
    # A lane that only ever runs via manual dispatch is not "main health".
    runs = [
        {"conclusion": "failure", "event": "workflow_dispatch"},
        {"conclusion": "failure", "event": "workflow_dispatch"},
    ]
    assert mod.select_main_run(runs) is None


def test_select_main_run_keeps_automatic_events():
    for ev in ("push", "schedule", "release"):
        got = mod.select_main_run([{"event": ev, "conclusion": "failure"}])
        assert got is not None and got["event"] == ev


def test_select_main_run_takes_newest_automatic():
    runs = [
        {"event": "schedule", "conclusion": "failure", "run_number": 10},
        {"event": "push", "conclusion": "success", "run_number": 9},
    ]
    assert mod.select_main_run(runs)["run_number"] == 10


def test_select_main_run_empty():
    assert mod.select_main_run([]) is None


def test_issue_body_carries_marker_and_rows():
    body = mod._issue_body(
        [{"name": "goldengraph-pipeline", "conclusion": "failure",
          "run_number": 42, "html_url": "http://x/42"}]
    )
    assert body.startswith(mod.TRACKER_MARKER)
    assert "goldengraph-pipeline" in body
    assert "[#42](http://x/42)" in body


def test_issue_body_does_not_claim_the_run_is_the_latest_on_main():
    """`select_main_run` skips manual dispatches, so the run named in the table
    is the latest AUTOMATIC one and a newer green dispatch can sit above it.
    Calling it "Latest main run" is a false claim, and it reads as a stale or
    broken query -- on #2457 both listed lanes had newer passing dispatches and
    the header sent the reader off to debug the scan instead of the lane."""
    body = mod._issue_body(
        [{"name": "benchmarks", "conclusion": "failure",
          "run_number": 95, "html_url": "http://x/95"}]
    )
    assert "| Latest automatic main run |" in body
    assert "| Latest main run |" not in body


def test_issue_body_explains_why_a_green_dispatch_does_not_clear_it():
    """The exclusion is deliberate: a dispatch can run a narrower scope than the
    scheduled lane and pass. benchmarks #96 was a `datasets: products` dispatch
    (2 of 6 datasets) that passed while the full scheduled run was red."""
    body = mod._issue_body(
        [{"name": "benchmarks", "conclusion": "failure",
          "run_number": 95, "html_url": "http://x/95"}]
    )
    assert "workflow_dispatch" in body
    assert "narrower scope" in body
