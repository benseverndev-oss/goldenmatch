"""Task N1: schema surface for negative evidence on FS (probabilistic) matchkeys.

Spec: docs/superpowers/specs/2026-07-14-fs-negative-evidence-design.md
Plan: docs/superpowers/plans/2026-07-14-fs-negative-evidence.md (Task N1)

Validation matrix: penalty/penalty_bits x matchkey type (weighted/exact/
probabilistic), both-set-is-impossible-by-construction, neither-set.
"""
import pydantic
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField, NegativeEvidenceField


def _mk(mk_type: str, **ne_kwargs) -> MatchkeyConfig:
    """Build a single-field matchkey of ``mk_type`` with one NE field on 'phone'."""
    field_kwargs: dict = {"field": "name"}
    if mk_type == "weighted":
        field_kwargs.update(scorer="ensemble", weight=1.0)
        threshold = 0.85
    elif mk_type == "probabilistic":
        field_kwargs.update(scorer="ensemble")
        threshold = None
    else:  # exact
        threshold = None

    ne = NegativeEvidenceField(
        field="phone", transforms=["digits_only"], scorer="exact",
        threshold=0.5, **ne_kwargs,
    )
    return MatchkeyConfig(
        name="mk",
        type=mk_type,
        threshold=threshold,
        fields=[MatchkeyField(**field_kwargs)],
        negative_evidence=[ne],
    )


# 1. probabilistic + NE with penalty_bits=3.0 (no penalty) -> validates
def test_probabilistic_ne_penalty_bits_validates():
    mk = _mk("probabilistic", penalty_bits=3.0)
    assert mk.negative_evidence is not None
    assert mk.negative_evidence[0].penalty_bits == 3.0
    assert mk.negative_evidence[0].penalty is None


# 2. probabilistic + NE with penalty=0.4 -> ValidationError naming penalty_bits
def test_probabilistic_ne_penalty_rejected():
    with pytest.raises(pydantic.ValidationError, match=r"penalty_bits"):
        _mk("probabilistic", penalty=0.4)


# 3. weighted + NE WITHOUT penalty -> ValidationError (moved to matchkey validator)
def test_weighted_ne_missing_penalty_rejected():
    with pytest.raises(pydantic.ValidationError, match=r"penalty"):
        _mk("weighted")


# 4. weighted + NE with penalty_bits=2.0 -> ValidationError (weighted rejects penalty_bits)
def test_weighted_ne_penalty_bits_rejected():
    with pytest.raises(pydantic.ValidationError, match=r"penalty_bits"):
        _mk("weighted", penalty=0.4, penalty_bits=2.0)


# 5. exact + NE with penalty=0.4 -> validates (byte-unchanged semantics)
def test_exact_ne_penalty_validates():
    mk = _mk("exact", penalty=0.4)
    assert mk.negative_evidence is not None
    assert mk.negative_evidence[0].penalty == 0.4


def test_exact_ne_missing_penalty_rejected():
    with pytest.raises(pydantic.ValidationError, match=r"penalty"):
        _mk("exact")


def test_exact_ne_penalty_bits_rejected():
    with pytest.raises(pydantic.ValidationError, match=r"penalty_bits"):
        _mk("exact", penalty=0.4, penalty_bits=1.0)


# 6. probabilistic + NE with NEITHER penalty nor penalty_bits -> validates (EM-learned default)
def test_probabilistic_ne_neither_set_validates():
    mk = _mk("probabilistic")
    assert mk.negative_evidence is not None
    assert mk.negative_evidence[0].penalty is None
    assert mk.negative_evidence[0].penalty_bits is None


# 7. penalty_bits=-2.0 on probabilistic -> validates (any float accepted; abs() at scoring)
def test_probabilistic_ne_negative_penalty_bits_validates():
    mk = _mk("probabilistic", penalty_bits=-2.0)
    assert mk.negative_evidence is not None
    assert mk.negative_evidence[0].penalty_bits == -2.0


# 8. Existing-shape round-trip: a weighted matchkey dict with NE parses identically
def test_weighted_ne_round_trip_unchanged():
    payload = {
        "name": "test",
        "type": "weighted",
        "threshold": 0.85,
        "fields": [
            {"field": "email", "transforms": ["lowercase"], "scorer": "ensemble", "weight": 1.0}
        ],
        "negative_evidence": [
            {
                "field": "phone",
                "transforms": ["digits_only"],
                "scorer": "exact",
                "threshold": 0.5,
                "penalty": 0.3,
            }
        ],
    }
    mk = MatchkeyConfig.model_validate(payload)
    dumped = mk.model_dump(exclude_none=True)
    # The negative_evidence shape is the load-bearing back-compat surface this
    # task touches; compare it exactly (byte-unchanged for weighted/exact).
    assert dumped["negative_evidence"] == [
        {
            "field": "phone",
            "transforms": ["digits_only"],
            "scorer": "exact",
            "threshold": 0.5,
            "penalty": 0.3,
        }
    ]
    assert dumped["type"] == "weighted"
    assert dumped["threshold"] == 0.85


# ── Render/telemetry surfaces must not crash on probabilistic NE shapes ─────
# (penalty=None is now a valid schema state; these surfaces previously did
# float(field.penalty) / f"-{nf.penalty:.2f}" unconditionally.)


class _StubConfig:
    def __init__(self, matchkeys):
        self._matchkeys = matchkeys

    def get_matchkeys(self):
        return self._matchkeys


def _render_fixture_matchkeys() -> list[MatchkeyConfig]:
    """One matchkey per NE shape: weighted+penalty, probabilistic+penalty_bits,
    probabilistic EM-learned (neither set)."""
    return [
        _mk("weighted", penalty=0.4),
        _mk("probabilistic", penalty_bits=3.0),
        _mk("probabilistic"),
    ]


def test_web_telemetry_negative_evidence_handles_all_ne_shapes():
    from goldenmatch.web.controller_telemetry import _negative_evidence

    out = _negative_evidence(_StubConfig(_render_fixture_matchkeys()))
    assert len(out) == 3
    by_source = {row["weight_source"]: row for row in out}
    assert set(by_source) == {"penalty", "penalty_bits", "em_learned"}
    assert all(row["field"] == "phone" for row in out)
    assert by_source["penalty"]["penalty"] == 0.4
    assert by_source["penalty"]["penalty_bits"] is None
    assert by_source["penalty_bits"]["penalty"] is None
    assert by_source["penalty_bits"]["penalty_bits"] == 3.0
    assert by_source["em_learned"]["penalty"] is None
    assert by_source["em_learned"]["penalty_bits"] is None


def test_cli_controller_render_negative_evidence_handles_all_ne_shapes():
    from goldenmatch.cli._controller_render import _ne_penalty_label, _negative_evidence

    table = _negative_evidence(_StubConfig(_render_fixture_matchkeys()))
    assert table is not None  # built without crashing on penalty=None
    labels = [_ne_penalty_label(ne) for mk in _render_fixture_matchkeys()
              for ne in (mk.negative_evidence or [])]
    assert labels == ["-0.40", "-3.0 bits", "EM-learned"]


def test_tui_controller_tab_render_committed_handles_all_ne_shapes():
    textual = pytest.importorskip("textual")  # noqa: F841
    from goldenmatch.tui.engine import ControllerTelemetry
    from goldenmatch.tui.tabs.controller_tab import ControllerTab

    t = ControllerTelemetry(committed_config=_StubConfig(_render_fixture_matchkeys()))
    # _render_committed only reads `t`; call unbound so no widget mount needed.
    rendered = ControllerTab._render_committed(None, t)  # type: ignore[arg-type]
    assert "phone" in rendered
    assert "penalty" in rendered            # weighted flat penalty
    assert "penalty_bits" in rendered       # probabilistic fixed override
    assert "EM-learned" in rendered         # probabilistic learned weight
