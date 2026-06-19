import demo.narrative as nv  # pyright: ignore[reportMissingImports]  # namespace pkg, resolves at runtime from bench dir

# index -> (mention, entity_id) for a tiny IBM-only fixture
RECS = {
    0: ("IBM", "Q37156"),
    1: ("International Business Machines", "Q37156"),
    2: ("Big Blue", "Q37156"),
}
MENTIONS = {i: m for i, (m, _) in RECS.items()}
EIDS = {i: e for i, (_, e) in RECS.items()}

def test_complete_partition_adds_singletons():
    parts = nv.complete_partition([[0, 1]], [0, 1, 2])
    assert sorted(map(sorted, parts)) == [[0, 1], [2]]

def test_under_merge_answer_fragmented():
    before = [[0], [1], [2]]
    ans = nv.under_merge_answer(before, MENTIONS, EIDS, "Q37156", query="IBM")
    assert ans["distinct_nodes"] == 3
    assert ans["names_reachable"] == ["IBM"]
    assert ans["complete"] is False

def test_under_merge_answer_resolved():
    after = [[0, 1, 2]]
    ans = nv.under_merge_answer(after, MENTIONS, EIDS, "Q37156", query="IBM")
    assert ans["distinct_nodes"] == 1
    assert set(ans["names_reachable"]) == {"IBM", "International Business Machines", "Big Blue"}
    assert ans["complete"] is True

def test_pair_merged_detects_over_merge():
    clusters = [[10, 11]]
    eids = {10: "Q230", 11: "Q1428"}
    assert nv.pair_merged(clusters, eids, "Q230", "Q1428") is True
    assert nv.pair_merged([[10], [11]], eids, "Q230", "Q1428") is False

def test_render_demo_md_is_deterministic():
    before = [[0], [1], [2]]
    after = [[0, 1, 2]]
    md1 = nv.render_demo_md(MENTIONS, EIDS, "Q37156", "IBM", before, after)
    md2 = nv.render_demo_md(MENTIONS, EIDS, "Q37156", "IBM", before, after)
    assert md1 == md2
    assert "International Business Machines" in md1
    assert "F1 0.066" in md1   # cites the harness exact-family number (scaled corpus)


import demo.kg as kg  # pyright: ignore[reportMissingImports]  # namespace pkg, resolves from bench dir

# index -> (mention, entity_id, type, context)
_KGRECS = {
    3: ("NATO", "Q7184", "org", "military alliance"),
    4: ("NATO Alliance", "Q7184", "org", "military alliance"),
    5: ("North Atlantic Treaty Organisation", "Q7184", "org", "military alliance"),
    9: ("WHO", "Q7817", "org", "UN health agency"),
}
_MEN = {i: m for i, (m, *_ ) in _KGRECS.items()}
_TYP = {i: t for i, (_, _, t, _) in _KGRECS.items()}
_CTX = {i: c for i, (_, _, _, c) in _KGRECS.items()}


def test_build_kg_fragmented_one_node_per_form():
    part = [[3], [4], [5], [9]]
    g = kg.build_kg(part, _MEN, _TYP, _CTX)
    assert len(g.nodes) == 4
    # each NATO form is its own node, single name
    nato_nodes = [n for n in g.nodes if set(n.record_indices) & {3, 4, 5}]
    assert len(nato_nodes) == 3
    assert all(len(n.names) == 1 for n in nato_nodes)


def test_build_kg_resolved_one_node_all_names():
    part = [[3, 4, 5], [9]]
    g = kg.build_kg(part, _MEN, _TYP, _CTX)
    nato = next(n for n in g.nodes if set(n.record_indices) & {3, 4, 5})
    assert set(nato.names) == {"NATO", "NATO Alliance", "North Atlantic Treaty Organisation"}
    assert nato.type == "org"
    assert nato.context == "military alliance"


def test_retrieve_lands_on_query_node_and_bounds_distractors():
    part = [[3, 4, 5], [9]]
    g = kg.build_kg(part, _MEN, _TYP, _CTX)
    sub = kg.retrieve(g, "NATO", type_filter="org", max_distractors=1)
    # the matched node is present
    assert any(set(n.record_indices) & {3, 4, 5} for n in sub.nodes)
    # bounded: matched + at most 1 distractor
    assert len(sub.nodes) <= 2
    assert sub.query == "NATO"


import demo.render_html as rh  # pyright: ignore[reportMissingImports]

_SNAPSHOT = {
    "scaffolding": {
        "protagonist": {"entity_id": "Q7184", "query": "NATO", "type": "org"},
        "question": "Are 'NATO', 'NATO Alliance' the same org, and how many distinct orgs?",
        "before": {"nodes": [
            {"node_id": 3, "names": ["NATO"], "type": "org", "context": "alliance", "record_indices": [3]},
            {"node_id": 4, "names": ["NATO Alliance"], "type": "org", "context": "alliance", "record_indices": [4]},
        ], "retrieved_node_ids": [3, 4]},
        "after": {"nodes": [
            {"node_id": 3, "names": ["NATO", "NATO Alliance"], "type": "org", "context": "alliance", "record_indices": [3, 4]},
        ], "retrieved_node_ids": [3]},
        "numbers": {"exact_family_f1": "F1 0.066"},
    },
    "recorded_llm": {
        "model": "gpt-4o-mini", "recorded_at": "2026-06-19",
        "before_answer": "There are two separate organizations.",
        "after_answer": "One organization with two names.",
        "cost": {"llm_calls": 2, "llm_tokens": 100, "llm_usd": 0.0001},
    },
}


def test_render_is_deterministic_and_self_contained():
    h1 = rh.render(_SNAPSHOT)
    h2 = rh.render(_SNAPSHOT)
    assert h1 == h2                              # pure
    assert h1.lstrip().startswith("<!DOCTYPE html>")
    assert "<script" not in h1.lower()           # no JS required to read
    assert "http://" not in h1 and "https://" not in h1.replace("http-equiv", "")  # no external assets
    # surfaces the real content
    assert "There are two separate organizations." in h1
    assert "One organization with two names." in h1
    assert "F1 0.066" in h1
    assert "gpt-4o-mini" in h1 and "2026-06-19" in h1
    assert "NATO Alliance" in h1


import demo.agent as ag  # pyright: ignore[reportMissingImports]
import demo.kg as kg  # noqa: F811


def test_answer_uses_subgraph_and_records_model():
    g = kg.build_kg([[3, 4, 5]], _MEN, _TYP, _CTX)
    sub = kg.retrieve(g, "NATO", type_filter="org")
    seen = {}
    def stub(prompt: str) -> ag.LLMResponse:
        seen["prompt"] = prompt
        return ag.LLMResponse(text="one org", model="stub-model", input_tokens=5, output_tokens=2)
    ans = ag.answer("how many orgs?", sub, stub)
    assert ans.text == "one org"
    assert ans.model == "stub-model"
    # the serialized subgraph is in the prompt (closed-book grounding)
    assert "NATO" in seen["prompt"] and "how many orgs?" in seen["prompt"]
    assert ans.n_nodes_seen == len(sub.nodes)
