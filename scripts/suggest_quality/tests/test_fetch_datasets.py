"""Tests for the fetch-skip guard.

`dblp_acm` is vendored (#2721) and `_dblp_acm_dir()` prefers the vendored copy,
so fetching it downloads a corpus nothing reads. `_already_loadable` suppresses
that. A skip guard that skips the wrong thing -- or fails to skip -- is the same
class of defect as a check that does not fire, so both directions are tested.
"""
from scripts.suggest_quality import fetch_datasets as fd


def test_skips_a_dataset_the_gate_can_already_load():
    """The live case: dblp_acm is vendored, so no fetch should be attempted."""
    assert fd._already_loadable("dblp_acm") is True


def test_does_not_skip_a_dataset_that_is_genuinely_absent():
    """ncvr_real has no public URL and is not vendored -- it must stay
    fetch-eligible rather than being silently treated as satisfied."""
    assert fd._already_loadable("ncvr_real") is False


def test_unknown_dataset_is_not_treated_as_satisfied():
    """A name absent from the REGISTRY must not report loadable; that would
    silently suppress a fetch for something nothing can load."""
    assert fd._already_loadable("no_such_dataset") is False


def test_a_raising_loader_is_not_treated_as_satisfied(monkeypatch):
    """A broken loader means 'fetch and see', never 'already fine' -- treating
    an exception as satisfied would suppress the fetch that might fix it."""
    class _Boom:
        name = "boom"

        @staticmethod
        def loader():
            raise RuntimeError("loader exploded")

    monkeypatch.setattr(fd, "REGISTRY", [_Boom()])
    assert fd._already_loadable("boom") is False
