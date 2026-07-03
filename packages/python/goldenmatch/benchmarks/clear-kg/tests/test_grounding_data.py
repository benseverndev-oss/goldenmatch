"""Track C dataset invariants -- the three candidate classes are constructed as
claimed, so the metric measures what it says. Pure/offline."""
import re

from grounding_data import RELATIONS, SIBLING, generate_grounding_dataset


def _find(text, surface):
    return re.search(rf"\b{re.escape(surface)}\b", text, flags=re.IGNORECASE)


def _cooccur(cand, docs):
    return [t for t in docs.values()
            if _find(t, cand["subj_surface"]) and _find(t, cand["obj_surface"])]


def test_three_classes_present():
    ds = generate_grounding_dataset(seed=0)
    classes = {c["gold_class"] for c in ds["candidates"]}
    assert classes == {"supported", "distractor", "hallucinated"}


def test_supported_span_states_the_relation():
    ds = generate_grounding_dataset(seed=0)
    for c in ds["candidates"]:
        if c["gold_class"] != "supported":
            continue
        assert c["gold_verdict"] == "supported"
        prov = c["gold_provenance"]
        assert prov is not None
        text = ds["docs"][prov["doc_id"]]
        # the provenance sentence contains both entities AND a trigger of the relation
        assert _find(text, c["subj_surface"]) and _find(text, c["obj_surface"])
        assert any(t in text.lower() for t in RELATIONS[c["rel"]]["triggers"])


def test_distractor_cooccurs_but_states_the_sibling_relation():
    ds = generate_grounding_dataset(seed=0)
    docs = ds["docs"]
    seen = 0
    for c in ds["candidates"]:
        if c["gold_class"] != "distractor":
            continue
        seen += 1
        assert c["gold_verdict"] == "unsupported" and c["gold_provenance"] is None
        co = _cooccur(c, docs)
        assert co, c  # entities DO co-occur (that's what fools presence-grounding)
        t = co[0].lower()
        # ...but the sentence states the sibling relation, not the claimed one
        assert any(trg in t for trg in RELATIONS[SIBLING[c["rel"]]]["triggers"]), c
        assert not any(trg in t for trg in RELATIONS[c["rel"]]["triggers"]), c
    assert seen > 0


def test_hallucinated_never_cooccurs():
    ds = generate_grounding_dataset(seed=0)
    docs = ds["docs"]
    seen = 0
    for c in ds["candidates"]:
        if c["gold_class"] != "hallucinated":
            continue
        seen += 1
        assert c["gold_verdict"] == "unsupported"
        assert not _cooccur(c, docs), c  # the pair appears in NO document
    assert seen > 0
