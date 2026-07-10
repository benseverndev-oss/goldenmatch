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


def test_issue_body_carries_marker_and_rows():
    body = mod._issue_body(
        [{"name": "goldengraph-pipeline", "conclusion": "failure",
          "run_number": 42, "html_url": "http://x/42"}]
    )
    assert body.startswith(mod.TRACKER_MARKER)
    assert "goldengraph-pipeline" in body
    assert "[#42](http://x/42)" in body
