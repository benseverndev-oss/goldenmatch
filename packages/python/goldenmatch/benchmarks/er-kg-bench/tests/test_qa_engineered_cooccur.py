"""Co-occurrence corpus rendering (argctx production wiring): extra phrasing docs, base id + questions
preserved so argument-context resolution has the signal without changing the QA gold."""
from __future__ import annotations

import os

from erkgbench.qa_e2e.engineered import generate_engineered


def _gen(**env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        return generate_engineered(seed=7, n_questions=20, ambiguity=0.0)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_cooccur_questions_byte_identical_to_paraphrase_corpus():
    base = _gen(GOLDENGRAPH_BENCH_REL_PARAPHRASE="1", GOLDENGRAPH_BENCH_COOCCUR="0")
    co = _gen(GOLDENGRAPH_BENCH_REL_PARAPHRASE="1", GOLDENGRAPH_BENCH_COOCCUR="1")
    bq = [(q.question, q.gold_answer, tuple(q.gold_supporting_fact_ids)) for q in base.questions]
    cq = [(q.question, q.gold_answer, tuple(q.gold_supporting_fact_ids)) for q in co.questions]
    assert bq == cq


def test_cooccur_doc_set_is_strict_superset_with_unique_ids():
    base = _gen(GOLDENGRAPH_BENCH_REL_PARAPHRASE="1", GOLDENGRAPH_BENCH_COOCCUR="0")
    co = _gen(GOLDENGRAPH_BENCH_REL_PARAPHRASE="1", GOLDENGRAPH_BENCH_COOCCUR="1")
    base_ids = {d.id for d in base.documents}
    co_ids = [d.id for d in co.documents]
    assert len(co_ids) == len(set(co_ids))           # unique
    assert base_ids <= set(co_ids)                   # every base doc-id present -> gold support resolves
    assert len(co.documents) > len(base.documents)   # strictly more docs (the co-occurrence)
