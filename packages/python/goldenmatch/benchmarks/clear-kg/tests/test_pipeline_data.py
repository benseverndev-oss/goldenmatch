"""Track D unified-corpus invariants: mentions carry a stable per-entity
co-mention signature (same-entity identical, homograph partners disjoint), and
the emitted triples split cleanly into supported / distractor / hallucinated."""
import re
from collections import Counter, defaultdict

from pipeline_data import generate_pipeline_corpus


def _find(text, surface):
    return re.search(rf"\b{re.escape(surface)}\b", text, flags=re.IGNORECASE)


def test_emitted_triple_classes():
    c = generate_pipeline_corpus(seed=0)
    counts = Counter(e["gold_class"] for e in c["emitted"])
    assert counts["supported"] > 0 and counts["distractor"] > 0 and counts["hallucinated"] > 0
    # supported are gold-supported; the rest are unsupported
    for e in c["emitted"]:
        assert e["gold_verdict"] == ("supported" if e["gold_class"] == "supported"
                                     else "unsupported")


def test_mentions_share_stable_signature_within_entity():
    c = generate_pipeline_corpus(seed=0)
    sig: dict[str, set] = defaultdict(set)
    for m in c["mentions"]:
        sig[m["gold_entity_id"]].add(tuple(m["neighbor_surfaces"]))
    # every mention of an entity carries the SAME neighbor signature (so ER merges)
    for eid, sigs in sig.items():
        assert len(sigs) == 1, eid


def test_homograph_partners_have_disjoint_signatures():
    c = generate_pipeline_corpus(seed=0)
    by_ent = {m["gold_entity_id"]: set(m["neighbor_surfaces"]) for m in c["mentions"]}
    by_id = {e["entity_id"]: e for e in c["entities"]}
    hg = c["homograph_ids"]
    # two homographs sharing a surface must have disjoint co-mention signatures
    for a in hg:
        for b in hg:
            if a >= b:
                continue
            shared = set(by_id[a]["aliases"]) & set(by_id[b]["aliases"])
            if shared:
                assert not (by_ent[a] & by_ent[b]), (a, b)


def test_distractor_cooccurs_hallucinated_does_not():
    c = generate_pipeline_corpus(seed=0)
    docs = c["docs"]
    for e in c["emitted"]:
        co = [t for t in docs.values()
              if _find(t, e["subj_surface"]) and _find(t, e["obj_surface"])]
        if e["gold_class"] == "distractor":
            assert co, e            # entities co-occur (fools presence grounding)
        elif e["gold_class"] == "hallucinated":
            assert not co, e        # pair appears in NO document
