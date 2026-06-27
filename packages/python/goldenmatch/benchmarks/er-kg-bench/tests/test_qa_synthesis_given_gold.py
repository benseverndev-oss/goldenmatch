from __future__ import annotations

from erkgbench.qa_e2e import ablation
from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph, gold_chain
from erkgbench.qa_e2e.scorecard_llm import build_gold_subgraph, synthesis_given_gold


class _StubLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.response


def _setup():
    corpus = generate_engineered(seed=7, n_questions=10, ambiguity=0.3, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    return corpus, g, ablation._typ_of(g)


def test_build_gold_subgraph_carries_the_chain():
    corpus, g, typ_of = _setup()
    qa = corpus.questions[0]
    chain = gold_chain(g, qa)
    sub = build_gold_subgraph(chain, g, typ_of)
    ids = {e["entity_id"] for e in sub["entities"]}
    for (s, rel, o) in chain:
        assert s in ids and o in ids
        assert any(
            e["subj"] == s and e["obj"] == o and e["predicate"] == rel for e in sub["edges"]
        )
    assert all(e["canonical_name"] for e in sub["entities"])


def test_synthesis_given_gold_scores_answer_match():
    corpus, g, typ_of = _setup()
    qa = corpus.questions[0]
    chain = gold_chain(g, qa)
    llm = _StubLLM(f"reasoning...\nAnswer: {qa.gold_answer}")
    score = synthesis_given_gold(qa.question, chain, g, typ_of, qa.gold_answer, llm)
    assert score == 1.0
    # the synthesis prompt was handed the gold chain (a chain entity name appears)
    assert g.canonical_name(chain[-1][2]) in llm.prompts[-1]


def test_synthesis_given_gold_wrong_answer_scores_zero():
    corpus, g, typ_of = _setup()
    qa = corpus.questions[0]
    chain = gold_chain(g, qa)
    llm = _StubLLM("Answer: definitely-not-the-gold-xyzzy")
    assert synthesis_given_gold(qa.question, chain, g, typ_of, qa.gold_answer, llm) == 0.0
