"""End-to-end Track D: the CLEAR composite ranks the full-pipeline stacks
correctly, and the harmonic mean punishes hollowness on EITHER moat -- perfect
extraction + perfect ER cannot rescue a system that grounds distractors."""
from pipeline_data import generate_pipeline_corpus
from run_track_d import SYSTEMS, run


def test_clear_ranks_stacks_and_punishes_hollow_axes():
    corpus = generate_pipeline_corpus(seed=0)
    res = run(corpus)
    inc, er_only, gm = res["incumbent"], res["er_only"], res["goldenmatch"]

    # extraction is shared across all stacks (table stakes)
    assert inc["extraction_f1"] == er_only["extraction_f1"] == gm["extraction_f1"] > 0.9

    # the moats separate: goldenmatch tops ER and grounding
    assert gm["er_f1"] > inc["er_f1"]                     # neighborhood ER vs name-merge
    assert gm["grounded_correct"] > inc["grounded_correct"]  # relation-aware vs presence

    # the composite is monotone in capability
    assert gm["clear"] > er_only["clear"] > inc["clear"]

    # THE point: er_only has perfect ER but hollow grounding -> its CLEAR is
    # dragged below goldenmatch's despite matching it on two of three axes
    assert er_only["er_f1"] == gm["er_f1"]
    assert er_only["grounded_correct"] < gm["grounded_correct"]
    assert er_only["clear"] < gm["clear"]

    # winning BOTH moats yields the top composite
    assert gm["clear"] > 0.99


def test_systems_are_the_documented_stacks():
    assert set(SYSTEMS) == {"incumbent", "er_only", "goldenmatch"}
