"""Stamping a FALLBACK cutoff pins it and silently disables the refit.

`_stamp_resolved_link_thresholds` (#2637) writes the sample-run's resolved FS
cutoff onto the committed config so the value is explicit and tunable. Its
docstring claimed:

    "This does NOT change the cutoff. It records the value the run already
     used, so behaviour is byte-identical and the lever becomes reachable."

That holds for a CALIBRATED value. It is false for a FALLBACK. Once
`link_threshold` is set the value counts as `configured`, so the full-data run
skips the threshold refit entirely (`refit: {"reason": "explicit-link-threshold"}`)
and is pinned to a number derived from a ~6K sample -- while a `fallback` source
means precisely that nothing decided the value.

Measured, person@10K zero-config, identical in every other respect:

    stamped fallback 0.50    P 0.9947  R 1.0000  F1 0.9973
    left None (refit runs)   P 1.0000  R 0.9979  F1 0.9990   <- == probabilistic lane

At person@100K the same pin cost pairwise precision 0.9308 against the
probabilistic lane's 0.9992 -- over-merge, with recall already at 0.9995.

A calibrated value IS a real data-driven decision, so it is still stamped and
#2637's lever (a reachable `mk.cutoff` keeps `ThresholdShift` and
`perturbation_stability` alive) survives wherever a decision actually happened.
"""
from __future__ import annotations

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_controller import AutoConfigController
from goldenmatch.core.probabilistic import (
    LINK_THRESHOLD_CALIBRATED,
    LINK_THRESHOLD_FALLBACK,
)


def _config():
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="probabilistic_auto", type="probabilistic",
            fields=[MatchkeyField(field="first_name", scorer="jaro_winkler"),
                    MatchkeyField(field="surname", scorer="jaro_winkler")],
        )],
        blocking=BlockingConfig(strategy="static", keys=[
            BlockingKeyConfig(fields=["city"], transforms=["lowercase"])]),
    )


def _stamp(source: str, value: float = 0.5) -> GoldenMatchConfig:
    ctrl = AutoConfigController.__new__(AutoConfigController)
    ctrl._last_fs_link_thresholds = {
        "probabilistic_auto": {"link_threshold": value, "source": source},
    }
    cfg = _config()
    AutoConfigController._stamp_resolved_link_thresholds(ctrl, cfg)
    return cfg


def _threshold(cfg):
    return cfg.get_matchkeys()[0].link_threshold


def test_a_fallback_is_not_stamped():
    """`fallback` means nothing decided the value. Pinning it converts a
    non-decision into a `configured` cutoff and skips the full-data refit."""
    assert _threshold(_stamp(LINK_THRESHOLD_FALLBACK)) is None


def test_a_calibrated_value_is_still_stamped():
    """The lever #2637 exists for must survive: a real EM-calibrated decision is
    recorded so `mk.cutoff` is reachable."""
    assert _threshold(_stamp(LINK_THRESHOLD_CALIBRATED, 0.87)) == 0.87


def test_a_user_value_is_never_overwritten():
    ctrl = AutoConfigController.__new__(AutoConfigController)
    ctrl._last_fs_link_thresholds = {
        "probabilistic_auto": {"link_threshold": 0.5, "source": LINK_THRESHOLD_CALIBRATED},
    }
    cfg = _config()
    cfg.get_matchkeys()[0].link_threshold = 0.91  # user's own choice
    AutoConfigController._stamp_resolved_link_thresholds(ctrl, cfg)
    assert _threshold(cfg) == 0.91


def test_no_recording_stamps_nothing():
    """A missing measurement must not invent a cutoff -- the fabricated-default
    problem this function exists to remove."""
    ctrl = AutoConfigController.__new__(AutoConfigController)
    ctrl._last_fs_link_thresholds = {}
    cfg = _config()
    AutoConfigController._stamp_resolved_link_thresholds(ctrl, cfg)
    assert _threshold(cfg) is None
