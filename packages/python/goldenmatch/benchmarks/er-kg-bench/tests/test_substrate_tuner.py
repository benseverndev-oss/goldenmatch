"""SP-B2 substrate staged tuner: gate / escalate / run_staged (pure, box-safe with a fake scorer)."""
from __future__ import annotations

import pytest
from erkgbench import substrate_tuner as st
from goldengraph.config import SubstrateConfig


def _sc(presence, rel_f1, *, conn_cov=0.5):
    """A minimal scorecard dict in the substrate_scorecard shape."""
    return {
        "presence": None if presence is None else {"coverage": presence},
        "relational": {"f1": rel_f1, "recall": rel_f1, "precision": 1.0},
        "connectivity": {"coverage": conn_cov, "f1": conn_cov, "edge_recall": 0.9},
        "coherence": {"components": 1, "largest_fraction": 1.0},
    }


# --- Task 1: gate -------------------------------------------------------------------------------------
def test_gate_passes_when_both_axes_clear():
    g = st.evaluate_gate(_sc(0.95, 0.60), st.GateThresholds())
    assert g.passed is True and g.failing_axis is None


def test_gate_routes_presence_before_relational():
    g = st.evaluate_gate(_sc(0.10, 0.10), st.GateThresholds())
    assert g.passed is False and g.failing_axis == "presence"


def test_gate_relational_when_presence_ok():
    g = st.evaluate_gate(_sc(0.95, 0.10), st.GateThresholds())
    assert g.failing_axis == "relational"


def test_gate_skips_presence_when_none():
    g = st.evaluate_gate(_sc(None, 0.60), st.GateThresholds())
    assert g.passed is True
    g2 = st.evaluate_gate(_sc(None, 0.10), st.GateThresholds())
    assert g2.failing_axis == "relational"


# --- Task 2: escalate ---------------------------------------------------------------------------------
def test_escalate_presence_enables_chunking_first():
    step = st.escalate(SubstrateConfig(), "presence", set())
    assert step is not None
    lever, cfg = step
    assert lever == "chunk_extract" and cfg.chunk_extract is True


def test_escalate_relational_ladder_bumps_xdoc_key_twice():
    tried = set()
    l1, c1 = st.escalate(SubstrateConfig(), "relational", tried)
    assert l1 == "xdoc_key" and c1.xdoc_key == "name_ci"
    l2, c2 = st.escalate(c1, "relational", tried)
    assert l2 == "xdoc_key" and c2.xdoc_key == "name_ci_type" and c2.entity_type_canon is True


def test_escalate_schema_canon_sets_vocab():
    c = SubstrateConfig(xdoc_key="name_ci_type", entity_type_canon=True)
    tried = {("xdoc_key", "name_ci"), ("xdoc_key", "name_ci_type"), ("entity_type_canon", "True")}
    step = st.escalate(c, "relational", tried, relation_vocab=("acquired",))
    assert step is not None
    lever, cfg = step
    assert lever == "schema_canon" and cfg.schema_canon is True and cfg.relation_vocab == ("acquired",)


def test_escalate_schema_canon_skipped_without_vocab():
    c = SubstrateConfig(xdoc_key="name_ci_type", entity_type_canon=True)
    tried = {("xdoc_key", "name_ci"), ("xdoc_key", "name_ci_type"), ("entity_type_canon", "True")}
    step = st.escalate(c, "relational", tried, relation_vocab=())
    assert step is None


def test_escalate_skips_refuted():
    c = SubstrateConfig(chunk_extract=True, extractor="gliner")
    step = st.escalate(c, "presence", {("chunk_extract", "True"), ("extractor", "gliner")})
    assert step is None


def test_escalate_returns_none_when_exhausted():
    c = SubstrateConfig(chunk_extract=True, extractor="gliner")
    step = st.escalate(c, "presence", {("chunk_extract", "True"), ("extractor", "gliner")})
    assert step is None


def test_escalate_returns_lever_and_config():
    step = st.escalate(SubstrateConfig(), "presence", set())
    assert isinstance(step, tuple) and step[0] == "chunk_extract" and isinstance(step[1], SubstrateConfig)


# --- Task 3: run_staged -------------------------------------------------------------------------------
def _tiny_corpus():
    return ["doc one.", "doc two."], [("Q1", "a", "d1")], {"Q1": ["a"]}


def _scripted(*cards, seen=None):
    """A fake build_and_score that returns `cards` by call order, clamping to the last once exhausted
    (run_staged makes one extra call for the full build after the rounds). Records configs in `seen`."""
    state = {"i": 0}

    def fake(cfg, ds):
        if seen is not None:
            seen.append(cfg)
        card = cards[min(state["i"], len(cards) - 1)]
        state["i"] += 1
        return card

    return fake


def test_run_staged_rejects_budget_below_one():
    docs, gold, al = _tiny_corpus()
    with pytest.raises(ValueError):
        st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: _sc(1.0, 1.0), budget=0)


def test_run_staged_passes_first_round():
    docs, gold, al = _tiny_corpus()
    calls = []

    def fake(cfg, ds):
        calls.append(ds)
        return _sc(0.95, 0.60)

    res = st.run_staged(docs, gold, al, build_and_score=fake, budget=3)
    assert res.stopped_reason == "passed"
    assert len(res.trace) == 1
    assert len(calls) == 2  # 1 slice round + 1 full build


def test_run_staged_escalates_then_passes():
    docs, gold, al = _tiny_corpus()
    fake = _scripted(_sc(0.95, 0.10), _sc(0.95, 0.60))  # fail relational, then pass after escalate
    res = st.run_staged(docs, gold, al, build_and_score=fake, budget=5)
    assert res.stopped_reason == "passed"
    assert len(res.trace) == 2
    assert res.trace[0].escalated_to == "xdoc_key"


def test_run_staged_budget_exhausted():
    docs, gold, al = _tiny_corpus()
    # Bare config -> a 2-step relational ladder (""->name_ci->name_ci_type). budget=2: both rounds fail
    # + escalate successfully -> for-loop reaches its cap -> "budget" (not "exhausted").
    res = st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: _sc(0.95, 0.10),
                        budget=2, initial_config=SubstrateConfig())
    assert res.stopped_reason == "budget" and len(res.trace) == 2


def test_run_staged_promotes_argmax_not_last():
    docs, gold, al = _tiny_corpus()
    # round0 FAILS (0.40<0.50) but is the highest scorer; later rounds regress lower. best-so-far = round0.
    seen = []
    fake = _scripted(_sc(0.95, 0.40), _sc(0.95, 0.10), _sc(0.95, 0.10), seen=seen)
    res = st.run_staged(docs, gold, al, build_and_score=fake, budget=3, initial_config=SubstrateConfig())
    assert res.config == res.trace[0].config   # argmax = round0, not the last (regressed) config
    assert seen[-1] == res.config              # the full build used the argmax config


def test_run_staged_terminal_eject():
    docs, gold, al = _tiny_corpus()
    cfg0 = SubstrateConfig(chunk_extract=True, extractor="gliner")
    res = st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: _sc(0.10, 0.95),
                        budget=5, initial_config=cfg0)
    assert res.stopped_reason == "exhausted"


def test_tuner_result_trace_is_serializable():
    docs, gold, al = _tiny_corpus()
    res = st.run_staged(docs, gold, al, build_and_score=lambda cfg, ds: _sc(0.95, 0.60), budget=1)
    r0 = res.trace[0]
    assert r0.round == 0 and isinstance(r0.config, SubstrateConfig) and "relational" in r0.scorecard
    assert r0.escalated_to is None
