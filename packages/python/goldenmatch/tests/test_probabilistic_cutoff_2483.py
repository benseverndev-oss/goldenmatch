"""#2483: a probabilistic matchkey cuts on ``link_threshold``, not ``threshold``.

The reported cost of that distinction was concrete: a user swept
``mk.threshold`` from 0.90 to 0.99, got byte-identical results every time, and
concluded the cutoff did not matter on their data. It was measuring nothing.

Two things are pinned here.

**The warning reaches the workflow that actually hurt.** A constructor-time
guard already existed, but ``validate_assignment`` is off, so a Pydantic
model-validator never sees ``mk.threshold = 0.95`` on an object returned by
``auto_configure_probabilistic_df``. That assignment is exactly the reported
path, and it warned nothing.

**The library does not make the same mistake internally.** Two perturbation
helpers shifted ``threshold`` on probabilistic matchkeys to build "variant"
configs. Since that field is inert there, the variants matched IDENTICALLY to
the baseline -- so the zero-label stability signal computed from them saw no
change and reported maximum confidence. A falsely confident number is worse
than no number, which is why these are regression tests rather than a tidy-up.
"""
from __future__ import annotations

import warnings

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.config_edits import ThresholdShift, _perturbable_matchkeys
from goldenmatch.core.zero_label_confidence import threshold_perturbations


def _mk(mtype: str = "probabilistic", **kw) -> MatchkeyConfig:
    scorer_field = MatchkeyField(
        field="name",
        scorer="jaro_winkler",
        **({"weight": 1.0} if mtype == "weighted" else {}),
    )
    return MatchkeyConfig(name="p", type=mtype, fields=[scorer_field], **kw)


def _cfg(mk: MatchkeyConfig) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[mk],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["name"], transforms=["lowercase"])]
        ),
    )


# ── the warning reaches the reported workflow ─────────────────────────────

def test_threshold_assignment_on_probabilistic_warns():
    """The #2483 workflow: build the config, then assign and sweep."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mk = _mk()
        mk.threshold = 0.95
    messages = [str(w.message) for w in caught]
    assert any("link_threshold" in m for m in messages), messages
    assert any("IGNORED" in m for m in messages), messages


def test_threshold_at_construction_still_warns():
    """The pre-existing guard must keep working."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _mk(threshold=0.95)
    assert any("link_threshold" in str(w.message) for w in caught)


def test_both_guards_emit_the_same_message():
    """One wording, so the two paths cannot drift apart."""
    def _capture(fn):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn()
        return [str(w.message) for w in caught if "link_threshold" in str(w.message)]

    at_construction = _capture(lambda: _mk(threshold=0.95))
    at_assignment = _capture(lambda: setattr(_mk(), "threshold", 0.95))
    assert at_construction == at_assignment


def test_weighted_matchkey_threshold_is_silent():
    """`threshold` is the operative cutoff on a weighted matchkey."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mk = _mk("weighted", threshold=0.85)
        mk.threshold = 0.9
    assert not [w for w in caught if "link_threshold" in str(w.message)]


def test_setting_link_threshold_is_silent():
    """The field the user is being redirected to must not itself warn."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mk = _mk()
        mk.link_threshold = 0.95
    assert not [w for w in caught if "IGNORED" in str(w.message)]


# ── which field actually cuts ─────────────────────────────────────────────

def test_cutoff_field_by_matchkey_type():
    assert _mk().cutoff_field == "link_threshold"
    assert _mk("weighted", threshold=0.85).cutoff_field == "threshold"


def test_cutoff_is_none_when_no_decision_was_made():
    """The #2483 config: nothing in it says what the cut will be."""
    assert _mk().cutoff is None
    assert _mk(link_threshold=0.9).cutoff == pytest.approx(0.9)


def test_cutoff_ignores_the_inert_field():
    """A probabilistic matchkey carrying `threshold` still has no cutoff."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mk = _mk(threshold=0.85)
    assert mk.cutoff is None


# ── the library must not perturb an inert field ───────────────────────────

def test_no_inert_variants_for_the_reported_config():
    """Probabilistic + `threshold` + no `link_threshold` is NOT perturbable.

    This previously produced two variants that matched identically to the
    baseline, which a stability signal reads as perfect stability.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = _cfg(_mk(threshold=0.85))
    assert threshold_perturbations(cfg) == []
    assert _perturbable_matchkeys(cfg) == []
    assert ThresholdShift(delta=0.05).apply(cfg) is None


def test_link_threshold_is_what_gets_perturbed():
    cfg = _cfg(_mk(link_threshold=0.90))
    variants = threshold_perturbations(cfg)
    assert len(variants) == 2
    shifted = sorted(v.get_matchkeys()[0].link_threshold for v in variants)
    assert shifted == pytest.approx([0.85, 0.95])
    # The inert field is never written to.
    assert all(v.get_matchkeys()[0].threshold is None for v in variants)


def test_weighted_perturbation_is_unchanged():
    """Regression guard: the weighted path must behave exactly as before."""
    cfg = _cfg(_mk("weighted", threshold=0.85))
    variants = threshold_perturbations(cfg)
    assert len(variants) == 2
    assert sorted(v.get_matchkeys()[0].threshold for v in variants) == pytest.approx(
        [0.80, 0.90]
    )


def test_perturbable_gate_agrees_with_what_is_shifted():
    """A disagreement here means a matchkey is reported non-perturbable and
    then perturbed anyway (or the reverse)."""
    for mk in (_mk(link_threshold=0.9), _mk("weighted", threshold=0.85)):
        cfg = _cfg(mk)
        assert _perturbable_matchkeys(cfg)
        assert ThresholdShift(delta=0.05).apply(cfg) is not None
