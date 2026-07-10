"""CLEAR-KG Track C dataset: span-grounded faithfulness.

Track B asks "is each entity one node?" Track C asks the other question the
market skips: **when a KG emits a triple, is it verifiably supported by a
specific source span -- with a confidence -- or invented?**

The discriminator (the faithfulness analogue of Track B's homograph) is the
**distractor**: a sentence where the two entities co-occur but the claimed
relation is NOT stated (a *different* same-type relation is). Every documented
faithfulness mechanism the field ships -- "the entities appear in a sentence"
(within-sentence presence) or "the relation type conforms to the schema"
(ontology conformance) -- says SUPPORTED on a distractor, because neither reads
whether the span expresses *that relation*. A relation-aware grounder refuses it.

Three candidate-triple classes, by construction (deterministic, offline, no LLM):
  * supported     -- a sentence states exactly this relation (gold span exists);
  * distractor    -- a sentence states a DIFFERENT same-signature relation between
                     the same two entities (co-occur, but the claim is false);
  * hallucinated  -- the two entities NEVER co-occur in the corpus (invented).

`generate_grounding_dataset` is pure. Every relation has explicit type
signatures (so ontology-conformance can be measured) and trigger lexicons (so
relation-aware grounding is a real, if simple, NLU proxy -- not reading a hidden
label). Prose realism (LLM-generated, multi-sentence docs) is a later phase, as
in Track B; Phase 0 proves the mechanism and the metric.
"""
from __future__ import annotations

import random

# relation -> (type signature, trigger lexicon, surface template).
# Same-signature SIBLINGS are the distractor source: a claim of one, phrased as
# the other, so the entity types still conform but the span does not support it.
RELATIONS: dict[str, dict] = {
    "founded_by": {
        "sig": ("ORG", "PERSON"),
        "triggers": ["founded", "co-founded", "established", "started"],
        "template": "{obj} founded {subj}.",
    },
    "works_at": {
        "sig": ("PERSON", "ORG"),
        "triggers": ["works at", "joined", "is employed by", "was hired by"],
        "template": "{subj} works at {obj}.",
    },
    "advises": {
        "sig": ("PERSON", "ORG"),
        "triggers": ["advises", "is an advisor to", "consults for", "mentors"],
        "template": "{subj} advises {obj}.",
    },
    "acquired": {
        "sig": ("ORG", "ORG"),
        "triggers": ["acquired", "bought", "purchased", "took over"],
        "template": "{subj} acquired {obj}.",
    },
    "partnered_with": {
        "sig": ("ORG", "ORG"),
        "triggers": ["partnered with", "allied with", "teamed up with", "joint venture with"],
        "template": "{subj} partnered with {obj}.",
    },
    "headquartered_in": {
        "sig": ("ORG", "PLACE"),
        "triggers": ["is headquartered in", "is based in", "has its head office in"],
        "template": "{subj} is headquartered in {obj}.",
    },
}
# same-type-signature relation pairs -> the distractor swaps one for the other
SIBLING: dict[str, str] = {
    "works_at": "advises", "advises": "works_at",
    "acquired": "partnered_with", "partnered_with": "acquired",
}

_PERSONS = ["Jane Okafor", "Wei Chen", "Maria Silva", "Ahmed Hassan", "Liam Novak",
            "Sofia Rossi", "Raj Patel", "Elena Ivanova", "David Kim", "Yuki Tanaka"]
_ORGS = ["Acme Labs", "Northwind Bank", "Cedar Clinic", "Vertex Partners",
         "Summit Foundry", "Harbor Institute", "Ridge Analytics", "Delta Foods",
         "Union Press", "Pioneer Motors"]
_PLACES = ["Portland", "Lyon", "Osaka", "Cairo", "Bergen", "Turin", "Pune",
           "Cordoba", "Aarhus", "Leeds"]


def _entities() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"PERSON": [], "ORG": [], "PLACE": []}
    for i, s in enumerate(_PERSONS):
        out["PERSON"].append({"entity_id": f"P{i:02d}", "type": "PERSON", "surface": s})
    for i, s in enumerate(_ORGS):
        out["ORG"].append({"entity_id": f"O{i:02d}", "type": "ORG", "surface": s})
    for i, s in enumerate(_PLACES):
        out["PLACE"].append({"entity_id": f"L{i:02d}", "type": "PLACE", "surface": s})
    return out


def generate_grounding_dataset(
    *,
    seed: int = 0,
    n_supported: int = 24,
    n_distractor: int = 16,
    n_hallucinated: int = 12,
) -> dict:
    """Return ``{docs, candidates, relations}``.

    docs:       {doc_id: sentence}
    candidates: [{triple_id, subj, subj_surface, subj_type, rel, obj, obj_surface,
                  obj_type, gold_verdict ('supported'|'unsupported'),
                  gold_class ('supported'|'distractor'|'hallucinated'),
                  gold_provenance ({doc_id, span}|None)}]
    """
    rng = random.Random(seed)
    ents = _entities()
    docs: dict[str, str] = {}
    cands: list[dict] = []
    used_pairs: set[frozenset] = set()
    used_ent_ids: set[str] = set()
    did = tid = 0

    def pick(typ: str) -> dict:
        return rng.choice(ents[typ])

    def emit(text: str) -> str:
        nonlocal did
        doc_id = f"C{did:04d}"
        did += 1
        docs[doc_id] = text
        return doc_id

    def new_pair(rel: str):
        st, ot = RELATIONS[rel]["sig"]
        for _ in range(200):
            s, o = pick(st), pick(ot)
            if s["entity_id"] == o["entity_id"]:
                continue
            key = frozenset((s["entity_id"], o["entity_id"]))
            if key in used_pairs:
                continue
            return s, o, key
        return None

    def record(rel, s, o, gold_class, gold_verdict, prov):
        nonlocal tid
        cands.append({
            "triple_id": f"T{tid:04d}",
            "subj": s["entity_id"], "subj_surface": s["surface"], "subj_type": s["type"],
            "rel": rel,
            "obj": o["entity_id"], "obj_surface": o["surface"], "obj_type": o["type"],
            "gold_verdict": gold_verdict, "gold_class": gold_class,
            "gold_provenance": prov,
        })
        tid += 1
        used_ent_ids.update((s["entity_id"], o["entity_id"]))

    rels = list(RELATIONS)
    sib_rels = list(SIBLING)

    # --- supported: a sentence states exactly this relation ---
    for _ in range(n_supported):
        rel = rng.choice(rels)
        got = new_pair(rel)
        if not got:
            continue
        s, o, key = got
        used_pairs.add(key)
        sent = RELATIONS[rel]["template"].format(subj=s["surface"], obj=o["surface"])
        doc_id = emit(sent)
        record(rel, s, o, "supported", "supported", {"doc_id": doc_id, "span": [0, len(sent)]})

    # --- distractor: entities co-occur, but the sentence states the SIBLING
    #     relation, not the claimed one (same type signature) ---
    for _ in range(n_distractor):
        rel = rng.choice(sib_rels)
        got = new_pair(rel)
        if not got:
            continue
        s, o, key = got
        used_pairs.add(key)
        sib = SIBLING[rel]
        sent = RELATIONS[sib]["template"].format(subj=s["surface"], obj=o["surface"])
        emit(sent)  # the corpus states the sibling relation between s and o
        record(rel, s, o, "distractor", "unsupported", None)

    # --- hallucinated: the pair NEVER co-occurs in any doc (invented triple) ---
    for _ in range(n_hallucinated):
        rel = rng.choice(rels)
        got = new_pair(rel)  # new_pair excludes used_pairs -> no co-occurrence doc exists
        if not got:
            continue
        s, o, key = got
        used_pairs.add(key)  # keep later candidates from co-locating this pair
        record(rel, s, o, "hallucinated", "unsupported", None)

    return {"docs": docs, "candidates": cands, "relations": RELATIONS}
