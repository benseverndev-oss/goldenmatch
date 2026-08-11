"""Every FS scoring path must resolve the link cutoff the same way.

`_fs_link_threshold` applies three steps in order:

    configured `mk.link_threshold`  ->  EM-calibrated per-dataset cutoff  ->  default

Its docstring says it was "extracted verbatim from the scalar / vectorized /
batched scorers (#1804 item 4) so they cannot drift on how the cutoff is
resolved". Two paths then bypassed it and hand-rolled only the FIRST and THIRD
steps: `fused_match._match_fused_fs` and
`probabilistic_fast._resolve_probabilistic_fast_path`.

So whenever EM produced a calibrated cutoff -- which is what
`GOLDENMATCH_FS_CALIBRATE_THRESHOLD` exists to do, and is worth +0.49 F1 on
dblp_acm per that flag's own docstring -- the answer depended on which scorer
the router happened to pick. Same config, same data, different cut (#2483).

These tests pin the CONTRACT (all six paths agree with the helper) rather than
any particular number, so they keep holding if the default or the calibration
strategy is retuned later.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import MatchkeyConfig
from goldenmatch.core.probabilistic import _fs_link_threshold


class _EM:
    """Minimal EMResult stand-in: only the attributes the resolver reads."""

    def __init__(self, calibrated_link_threshold=None, proportion_matched=0.01):
        if calibrated_link_threshold is not None:
            self.calibrated_link_threshold = calibrated_link_threshold
        self.proportion_matched = proportion_matched


def _mk(**kw) -> MatchkeyConfig:
    kw.setdefault("name", "p")
    kw.setdefault("type", "probabilistic")
    kw.setdefault("fields", [{"field": "a", "scorer": "exact"}])
    return MatchkeyConfig(**kw)


class TestResolverPrecedence:
    def test_configured_wins_over_everything(self):
        mk = _mk(link_threshold=0.77)
        assert _fs_link_threshold(mk, _EM(calibrated_link_threshold=0.91), False) == 0.77

    def test_calibrated_wins_over_the_default(self):
        """The step both bypassing paths skipped."""
        got = _fs_link_threshold(_mk(), _EM(calibrated_link_threshold=0.91), False)
        assert got == 0.91

    def test_default_when_neither_is_set(self):
        got = _fs_link_threshold(_mk(), _EM(), False)
        assert got == 0.50  # the documented fixed default for pre-blocked pairs


class TestNoPathSkipsTheCalibratedCutoff:
    """The regression: a path that resolves the cutoff must go through the
    helper. Asserted structurally, because the alternative -- driving each
    scorer end to end -- needs a native kernel and a fitted EM, and would test
    the kernels rather than the resolution order that actually drifted."""

    @pytest.mark.parametrize("module_path", [
        "goldenmatch.core.fused_match",
        "goldenmatch.core.probabilistic_fast",
    ])
    def test_path_imports_the_shared_resolver(self, module_path):
        import importlib
        import inspect

        src = inspect.getsource(importlib.import_module(module_path))
        assert "_fs_link_threshold" in src, (
            f"{module_path} resolves a link threshold without the shared helper; "
            "a hand-rolled copy is how the EM-calibrated cutoff got skipped (#2483)"
        )

    @pytest.mark.parametrize("module_path", [
        "goldenmatch.core.fused_match",
        "goldenmatch.core.probabilistic_fast",
    ])
    def test_path_no_longer_hand_rolls_the_two_step_form(self, module_path):
        """Both bypassing paths had the same shape:

            if mk.link_threshold is not None: ... else: compute_thresholds(...)

        which is precedence step 1 then step 3, silently dropping step 2.
        """
        import importlib
        import inspect

        src = inspect.getsource(importlib.import_module(module_path))
        assert "compute_thresholds(" not in src, (
            f"{module_path} still calls compute_thresholds directly; that is the "
            "two-step form that skips the EM-calibrated cutoff (#2483)"
        )


class TestInertThresholdIsReported:
    """`threshold` is the WEIGHTED cutoff and does nothing on a probabilistic
    matchkey. Silently ignoring it cost the #2483 reporter a sweep of 0.90-0.99
    that returned byte-identical results, from which they reasonably concluded
    the cut did not matter on their data."""

    def test_threshold_on_a_probabilistic_matchkey_warns(self):
        with pytest.warns(UserWarning, match="link_threshold"):
            _mk(threshold=0.95)

    def test_the_warning_names_the_field_to_use_instead(self):
        with pytest.warns(UserWarning) as rec:
            _mk(threshold=0.95)
        msg = str(rec[0].message)
        assert "IGNORED" in msg and "#2483" in msg

    def test_it_warns_rather_than_rejecting(self):
        """A stray key is a usage mistake, not a corrupt config -- raising would
        break configs that merely carry one."""
        with pytest.warns(UserWarning):
            mk = _mk(threshold=0.95)
        assert mk.type == "probabilistic"
        assert mk.threshold == 0.95  # preserved, just not consulted

    def test_link_threshold_alone_is_silent(self, recwarn):
        _mk(link_threshold=0.95)
        assert not [w for w in recwarn if "#2483" in str(w.message)]

    def test_weighted_matchkeys_are_untouched(self, recwarn):
        """`threshold` is REQUIRED there; warning would be nonsense."""
        MatchkeyConfig(
            name="w", type="weighted", threshold=0.9,
            fields=[{"field": "a", "scorer": "jaro_winkler", "weight": 1.0}],
        )
        assert not [w for w in recwarn if "#2483" in str(w.message)]
