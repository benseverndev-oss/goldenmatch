"""#2637: the committed config records the FS link cutoff it actually used.

A probabilistic matchkey cuts on ``link_threshold``. Auto-config never set it,
so the number was resolved three layers down at scoring time and the config
handed back was silent about the most consequential Fellegi-Sunter decision.

Two costs, both measured in #2636:

- ``mk.cutoff`` was ``None``, so ``_perturbable_matchkeys`` was empty and
  ``ThresholdShift`` returned ``None``. The healer's ONLY FS-specific lever was
  inert on every auto-config FS config, and ``threshold_perturbations`` yielded
  nothing, leaving ``perturbation_stability`` permanently unmeasured there.
- the #2483 reporter swept a field that does not cut and measured nothing, with
  no way to see that no decision had ever been made.

The stamp records the value the run ALREADY used, so results must not move.
That equivalence is the load-bearing assertion here; the reachability
assertions are worth nothing if the cutoff silently changed.
"""
from __future__ import annotations

import warnings

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_controller import (
    AutoConfigController,
    ControllerBudget,
    resolve_planning_effort,
)
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
from goldenmatch.core.config_edits import ThresholdShift, _perturbable_matchkeys
from goldenmatch.core.probabilistic import (
    LINK_THRESHOLD_CALIBRATED,
    LINK_THRESHOLD_CONFIGURED,
    LINK_THRESHOLD_FALLBACK,
)
from goldenmatch.core.zero_label_confidence import threshold_perturbations


def _controller() -> AutoConfigController:
    return AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget.for_dataset(1000, resolve_planning_effort("fast")),
    )


def _cfg(mtype: str = "probabilistic", **mk_kw) -> GoldenMatchConfig:
    field = MatchkeyField(
        field="name",
        scorer="jaro_winkler",
        **({"weight": 1.0} if mtype == "weighted" else {}),
    )
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="p", type=mtype, fields=[field], **mk_kw)],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["name"], transforms=["lowercase"])]
        ),
    )


# ── the stamp writes what the run resolved ─────────────────────────────────

def test_stamps_a_calibrated_cutoff():
    """A calibrated source is a real decision the data made -- record it."""
    ctrl = _controller()
    ctrl._last_fs_link_thresholds = {
        "p": {"link_threshold": 0.62, "source": LINK_THRESHOLD_CALIBRATED}
    }
    cfg = _cfg()
    assert cfg.get_matchkeys()[0].link_threshold is None
    ctrl._stamp_resolved_link_thresholds(cfg)
    assert cfg.get_matchkeys()[0].link_threshold == pytest.approx(0.62)


def test_does_not_stamp_a_fallback_cutoff():
    """A fallback is a fixed default, not a decision (zero-config recall
    incident, defect 4, 2026-08-18). Stamping it sets `link_threshold`, which
    makes the matchkey's source read as `configured` and skips the full-data
    threshold refit -- pinning behaviour to a number resolved from whatever
    sample happened to run first. Measured cost at person@100K: precision
    0.9308 (fallback stamped, refit skipped) vs 0.9992 (left None, refit
    runs). Leaving it `None` here is what lets the refit still happen; see
    `docs/superpowers/notes/2026-08-18-zeroconfig-recall-incident.md`.
    """
    ctrl = _controller()
    ctrl._last_fs_link_thresholds = {
        "p": {"link_threshold": 0.62, "source": LINK_THRESHOLD_FALLBACK}
    }
    cfg = _cfg()
    ctrl._stamp_resolved_link_thresholds(cfg)
    assert cfg.get_matchkeys()[0].link_threshold is None


def test_never_overwrites_a_user_value():
    """`configured` means the user chose it. Overwriting would be a silent
    behaviour change on an explicit instruction."""
    ctrl = _controller()
    ctrl._last_fs_link_thresholds = {
        "p": {"link_threshold": 0.62, "source": LINK_THRESHOLD_CONFIGURED}
    }
    cfg = _cfg(link_threshold=0.80)
    ctrl._stamp_resolved_link_thresholds(cfg)
    assert cfg.get_matchkeys()[0].link_threshold == pytest.approx(0.80)


def test_no_measurement_invents_nothing():
    """A missing measurement must NOT produce a cutoff.

    Fabricating one here would be the same defect this exists to remove: a
    number in the config that nothing about the data chose.
    """
    ctrl = _controller()
    ctrl._last_fs_link_thresholds = {}
    cfg = _cfg()
    ctrl._stamp_resolved_link_thresholds(cfg)
    assert cfg.get_matchkeys()[0].link_threshold is None


def test_weighted_matchkey_is_untouched():
    """`link_threshold` is not the operative cutoff on a weighted matchkey."""
    ctrl = _controller()
    ctrl._last_fs_link_thresholds = {
        "p": {"link_threshold": 0.62, "source": LINK_THRESHOLD_FALLBACK}
    }
    cfg = _cfg("weighted", threshold=0.85)
    ctrl._stamp_resolved_link_thresholds(cfg)
    mk = cfg.get_matchkeys()[0]
    assert mk.link_threshold is None
    assert mk.threshold == pytest.approx(0.85)


def test_unknown_matchkey_name_is_ignored():
    ctrl = _controller()
    ctrl._last_fs_link_thresholds = {
        "other": {"link_threshold": 0.62, "source": LINK_THRESHOLD_FALLBACK}
    }
    cfg = _cfg()
    ctrl._stamp_resolved_link_thresholds(cfg)
    assert cfg.get_matchkeys()[0].link_threshold is None


@pytest.mark.parametrize("entry", [
    {"link_threshold": None, "source": LINK_THRESHOLD_FALLBACK},
    {"source": LINK_THRESHOLD_FALLBACK},
    {"link_threshold": "not-a-number", "source": LINK_THRESHOLD_FALLBACK},
    "not-a-dict",
])
def test_malformed_telemetry_is_a_no_op(entry):
    """Telemetry must never fail a run or write junk into a config."""
    ctrl = _controller()
    ctrl._last_fs_link_thresholds = {"p": entry}
    cfg = _cfg()
    ctrl._stamp_resolved_link_thresholds(cfg)
    assert cfg.get_matchkeys()[0].link_threshold is None


# ── what the stamp unlocks (the point of #2637) ────────────────────────────
#
# Only a CALIBRATED source unlocks the lever below -- a fallback is not a
# decision, so it must stay dark rather than pinning the healer to a number
# nothing chose. See `test_does_not_stamp_a_fallback_cutoff` above.

def test_the_healers_only_fs_lever_goes_live():
    """Before: ThresholdShift returned None on every auto-config FS config."""
    ctrl = _controller()
    cfg = _cfg()
    assert _perturbable_matchkeys(cfg) == []
    assert ThresholdShift(0.05).apply(cfg) is None

    ctrl._last_fs_link_thresholds = {
        "p": {"link_threshold": 0.50, "source": LINK_THRESHOLD_CALIBRATED}
    }
    ctrl._stamp_resolved_link_thresholds(cfg)

    assert len(_perturbable_matchkeys(cfg)) == 1
    shifted = ThresholdShift(0.05).apply(cfg)
    assert shifted is not None
    assert shifted.get_matchkeys()[0].link_threshold == pytest.approx(0.55)


def test_a_fallback_is_recorded_without_being_pinned():
    """The other half of #2637, closed by splitting provenance from value.

    This test used to assert the opposite -- that a fallback run gets no
    working FS lever, "same as before this fix existed". That was the
    limitation, not a property worth keeping: it left `ThresholdShift` inert
    and `perturbation_stability` unmeasurable on 4 of 4 datasets.

    What must NOT change is the incident guard. `resolve_thresholds`
    short-circuits on `link_threshold`, so pinning a fallback there skips the
    full-data refit and costs precision 0.9308 vs 0.9992 at person@100K
    (recall-incident note, defect 4). `link_threshold_observed` is read by
    nothing in the resolver, so recording it cannot do that -- and the
    assertion that it stays `None` on the operative field is the load-bearing
    one here.
    """
    ctrl = _controller()
    cfg = _cfg()
    ctrl._last_fs_link_thresholds = {
        "p": {"link_threshold": 0.50, "source": LINK_THRESHOLD_FALLBACK}
    }
    ctrl._stamp_resolved_link_thresholds(cfg)
    mk = cfg.get_matchkeys()[0]

    # The guard: nothing decided this number, so it must not count as a choice.
    assert mk.link_threshold is None, (
        "a fallback was pinned to the operative field, which skips the "
        "full-data refit -- this is defect 4 of the recall incident"
    )
    assert mk.cutoff is None, "`cutoff` must still answer 'nothing chose a cut'"

    # And the lever it unblocks.
    assert mk.link_threshold_observed == pytest.approx(0.50)
    assert _perturbable_matchkeys(cfg) == [mk]
    shifted = ThresholdShift(0.05).apply(cfg)
    assert shifted is not None
    assert shifted.get_matchkeys()[0].link_threshold == pytest.approx(0.55)
    assert len(threshold_perturbations(cfg)) == 2


def test_perturbation_stability_stops_being_dark():
    """`threshold_perturbations` yielded 0 variants on the FS path, so the
    zero-label stability signal was never measured (correctly `None`, but
    never computed). It has inputs now -- for a calibrated cutoff."""
    ctrl = _controller()
    cfg = _cfg()
    assert threshold_perturbations(cfg) == []
    ctrl._last_fs_link_thresholds = {
        "p": {"link_threshold": 0.50, "source": LINK_THRESHOLD_CALIBRATED}
    }
    ctrl._stamp_resolved_link_thresholds(cfg)
    variants = threshold_perturbations(cfg)
    assert len(variants) == 2
    # The inert field is still never written (#2483).
    assert all(v.get_matchkeys()[0].threshold is None for v in variants)


def test_stamped_config_reports_a_cutoff():
    """#2483's user-facing complaint: the config said nothing about the cut.
    Resolved for the calibrated case; the fallback case still says nothing
    (see `test_the_healers_lever_stays_dark_on_a_fallback`) -- reporting a
    fallback without pinning it needs a state beyond configured/calibrated/
    fallback, which is not built yet (2026-08-18 #2637 follow-up)."""
    ctrl = _controller()
    cfg = _cfg()
    assert cfg.get_matchkeys()[0].cutoff is None
    ctrl._last_fs_link_thresholds = {
        "p": {"link_threshold": 0.50, "source": LINK_THRESHOLD_CALIBRATED}
    }
    ctrl._stamp_resolved_link_thresholds(cfg)
    mk = cfg.get_matchkeys()[0]
    assert mk.cutoff_field == "link_threshold"
    assert mk.cutoff == pytest.approx(0.50)


# ── equivalence: recording the number must not change the answer ───────────

def _person_df(n: int = 240) -> pl.DataFrame:
    first = ["ada", "grace", "alan", "edsger", "barbara", "donald"]
    last = ["lovelace", "hopper", "turing", "dijkstra", "liskov", "knuth"]
    rows = []
    for i in range(n):
        f, l = first[i % len(first)], last[(i // 2) % len(last)]
        rows.append({
            "rid": f"r{i}",
            "name": f"{f} {l}" if i % 3 else f"{f[0]}. {l}",
            "city": ["leeds", "york", "hull"][i % 3],
        })
    return pl.DataFrame(rows)


def test_stamping_the_resolved_value_does_not_change_results(monkeypatch):
    """The load-bearing assertion.

    A run whose config carries the cutoff it resolved must produce exactly the
    output of the run that resolved it implicitly. Verified here on a synthetic
    frame; also checked out-of-test on ncvr_synthetic (F1 0.966000 both ways),
    historical_50k @8k (0.858600) and the synthetic anchor (1.000000).

    Forces `GOLDENMATCH_FS_CALIBRATE_THRESHOLD=1` so the resolved source is
    `calibrated`, not the default `fallback` -- only a calibrated cutoff is
    stamped since the zero-config recall incident (defect 4), so this test's
    own premise (something got stamped) needs calibration to hold in the
    first place. See `test_does_not_stamp_a_fallback_cutoff`.
    """
    import goldenmatch

    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATE_THRESHOLD", "1")

    df = _person_df()
    cfg = _cfg(link_threshold=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base = goldenmatch.dedupe_df(df, config=cfg)
        resolved = (base.stats or {}).get("fs_link_thresholds") or {}

        # Nothing to compare if this path never reached FS scoring, or if EM
        # didn't produce a calibrated cutoff on this frame (only calibrated
        # values are stamped -- a fallback here would correctly stay None).
        if not resolved or not any(
            v.get("source") == LINK_THRESHOLD_CALIBRATED
            for v in resolved.values() if isinstance(v, dict)
        ):
            pytest.skip("no calibrated FS cutoff resolved on this path")

        stamped_cfg = cfg.model_copy(deep=True)
        ctrl = _controller()
        ctrl._last_fs_link_thresholds = resolved
        ctrl._stamp_resolved_link_thresholds(stamped_cfg)
        assert stamped_cfg.get_matchkeys()[0].link_threshold is not None

        after = goldenmatch.dedupe_df(df, config=stamped_cfg)

    def _pairs(res):
        return sorted(
            (min(a, b), max(a, b), round(s, 10)) for a, b, s in res.scored_pairs
        )

    assert _pairs(after) == _pairs(base)
    assert sorted(map(sorted, base.clusters.values())) == sorted(
        map(sorted, after.clusters.values())
    )
