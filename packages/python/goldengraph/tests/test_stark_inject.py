"""SP-moat: alias injection fragments a gold entity into k variant-named nodes,
splitting its doc + edges. Pure + seeded; no store, no goldenmatch."""
from __future__ import annotations

from goldengraph.stark_inject import _variants, inject_aliases

# 3 nodes; N1 is the injection target. Docs are 3 sentences so k=3 splits cleanly.
_NODES = [("1", "Interleukin 6", "gene"), ("2", "aspirin", "drug"), ("3", "fever", "effect")]
_TEXTS = ["Interleukin 6 is a cytokine. It signals inflammation. It is a drug target.",
          "aspirin doc.", "fever doc."]
_EDGES = [("1", "associated_with", "3"), ("2", "treats", "3"), ("1", "targeted_by", "2")]


def test_variants_returns_k_distinct():
    vs = _variants("Interleukin 6", 3, seed=0)
    assert len(vs) == 3 and len(set(vs)) == 3          # distinct (anti-rig for exact dedup)


def test_target_fragmented_into_k_aliases_original_dropped():
    nodes2, texts2, edges2, canon = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=0)
    ids = {n[0] for n in nodes2}
    assert "1" not in ids                               # original dropped
    aliases = [i for i in ids if i.startswith("1#a")]
    assert len(aliases) == 3                            # k aliases
    assert {"2", "3"} <= ids                            # non-targets pass through


def test_canon_maps_aliases_to_original_identity_elsewhere():
    _, _, _, canon = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=0)
    assert canon["2"] == "2" and canon["3"] == "3"      # identity for non-targets
    assert all(canon[a] == "1" for a in canon if a.startswith("1#a"))


def test_doc_sentences_partitioned_no_alias_has_full():
    nodes2, texts2, _, _ = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=0)
    txt = dict(zip([n[0] for n in nodes2], texts2))
    alias_docs = [txt[i] for i in txt if i.startswith("1#a")]
    joined = " ".join(alias_docs)
    for sent in ["Interleukin 6 is a cytokine", "It signals inflammation", "It is a drug target"]:
        assert sent in joined                           # union preserves every sentence
    assert all(len(d) < len(_TEXTS[0]) for d in alias_docs)   # no alias has the full doc


def test_edges_distributed_across_aliases():
    _, _, edges2, _ = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=0)
    # N1 had 2 incident edges (->3, N1 targeted_by 2); they land on DIFFERENT aliases
    n1_edge_sources = {s for (s, _p, _o) in edges2 if s.startswith("1#a")}
    n1_edge_objs = {o for (_s, _p, o) in edges2 if o.startswith("1#a")}
    assert len(n1_edge_sources | n1_edge_objs) >= 2     # spread across >=2 aliases


def test_both_target_edge_remaps_both_ends():
    # inject BOTH endpoints of edge (1 -targeted_by-> 2): both must become aliases
    _, _, edges2, _ = inject_aliases(_NODES, _TEXTS, _EDGES, {"1", "2"}, k=2, seed=0)
    e = [(s, p, o) for (s, p, o) in edges2 if p == "targeted_by"][0]
    assert e[0].startswith("1#a") and e[2].startswith("2#a")


def test_determinism_same_seed():
    a = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=7)
    b = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=7)
    assert a == b
