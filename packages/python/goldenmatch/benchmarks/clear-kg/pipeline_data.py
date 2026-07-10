"""CLEAR-KG Track D: one unified corpus for the end-to-end composite.

Track D is the greenfield: corpus -> a grounded, resolved KG, scored on all three
axes at once. A **system** is a full pipeline = (shared extractor) x (ER engine)
x (grounding engine); the CLEAR score is the harmonic mean of extraction-F1,
ER-F1, and grounded-&-correct rate, so a system cannot win by being strong on one
axis and hollow on the others.

This corpus carries all three aligned ground truths by construction, so the
existing Track A/B/C scorers compose over it:
  * gold_triples + docs      -> extraction-F1 (surface triples from text);
  * mentions (homographs +   -> ER-F1 (B-cubed over the mention clustering); each
    a stable co-mention          entity's mentions share its co-mention signature,
    signature)                   homograph partners' signatures are disjoint;
  * emitted candidate triples-> grounded-&-correct (supported / distractor /
    (supported/distractor/       hallucinated), with distractor docs stating a
    hallucinated)                sibling relation the claim does not.

Relations are the two PERSON->ORG grounding relations (`works_at` / `advises`)
that are same-signature SIBLINGS, so a distractor states one while the candidate
claims the other. Deterministic, offline, no LLM.
"""
from __future__ import annotations

import random

from generate import _abbrev
from grounding_data import RELATIONS

_PERSONS = ["Jane Okafor", "Wei Chen", "Maria Silva", "Ahmed Hassan", "Liam Novak",
            "Sofia Rossi", "Raj Patel", "Elena Ivanova"]
# a wide org pool so every person can hold a DISJOINT gold object set (homograph
# partners' co-mention signatures must not overlap)
_ORGS = ["Acme Labs", "Northwind Bank", "Cedar Clinic", "Vertex Partners",
         "Summit Foundry", "Harbor Institute", "Ridge Analytics", "Delta Foods",
         "Union Press", "Pioneer Motors", "Beacon Systems", "Cobalt Robotics",
         "Meridian Health", "Aster Capital", "Willow Media", "Ironwood Steel",
         "Solace Pharma", "Tidewater Shipping", "Granite Insurance", "Lumen Optics",
         "Verdant Farms", "Sable Security", "Onyx Mining", "Cirrus Airlines"]
_PERSON_RELS = ["works_at", "advises"]  # same (PERSON, ORG) signature -> siblings


def generate_pipeline_corpus(
    *,
    seed: int = 0,
    n_persons: int = 6,
    n_homograph_pairs: int = 2,
    triples_per_person: int = 3,
    n_distractor: int = 6,
    n_hallucinated: int = 4,
) -> dict:
    """Return a unified Track-D corpus dict (see module docstring)."""
    rng = random.Random(seed)
    if n_persons * triples_per_person > len(_ORGS):
        raise ValueError("not enough orgs for globally-disjoint object sets")

    persons: list[dict] = []
    names = rng.sample(_PERSONS, n_persons)
    for i, nm in enumerate(names):
        persons.append({"entity_id": f"P{i:02d}", "type": "PERSON", "canonical": nm,
                        "aliases": [nm]})
    orgs = [{"entity_id": f"O{i:02d}", "type": "ORG", "canonical": nm}
            for i, nm in enumerate(_ORGS)]

    # homographs: partner pairs share an ambiguous surface; globally-disjoint org
    # blocks (below) guarantee their co-mention signatures don't overlap.
    order = list(range(n_persons))
    rng.shuffle(order)
    homograph_ids: set[str] = set()
    partner: dict[str, str] = {}
    for k in range(min(n_homograph_pairs, n_persons // 2)):
        a, b = persons[order[2 * k]], persons[order[2 * k + 1]]
        shared = _abbrev(a["canonical"])
        for e in (a, b):
            if shared not in e["aliases"]:
                e["aliases"].append(shared)
            e["_ambiguous"] = shared
            homograph_ids.add(e["entity_id"])
        partner[a["entity_id"]] = b["entity_id"]
        partner[b["entity_id"]] = a["entity_id"]

    # each person gets a DISJOINT block of orgs (its co-mention signature)
    person_orgs: dict[str, list[dict]] = {}
    cur = 0
    for p in persons:
        person_orgs[p["entity_id"]] = orgs[cur:cur + triples_per_person]
        cur += triples_per_person

    gold_triples: list[tuple] = []
    docs: dict[str, str] = {}
    mentions: list[dict] = []
    emitted: list[dict] = []
    did = mid = tid = 0

    def surface_of(p: dict) -> str:
        return p.get("_ambiguous", p["canonical"])

    # --- real docs / gold triples / subject mentions ---
    for p in persons:
        my_orgs = person_orgs[p["entity_id"]]
        signature = sorted(o["canonical"] for o in my_orgs)  # stable co-mention set
        for o in my_orgs:
            rel = rng.choice(_PERSON_RELS)
            gold_triples.append((p["entity_id"], rel, o["entity_id"]))
            subj_s = surface_of(p)
            doc_id = f"D{did:04d}"
            did += 1
            docs[doc_id] = RELATIONS[rel]["template"].format(subj=subj_s, obj=o["canonical"])
            mentions.append({"mention_id": f"M{mid:04d}", "doc_id": doc_id,
                             "surface": subj_s, "gold_entity_id": p["entity_id"],
                             "neighbor_surfaces": signature})
            mid += 1
            emitted.append({
                "triple_id": f"T{tid:04d}", "subj": p["entity_id"], "subj_surface": subj_s,
                "subj_type": "PERSON", "rel": rel, "obj": o["entity_id"],
                "obj_surface": o["canonical"], "obj_type": "ORG",
                "gold_verdict": "supported", "gold_class": "supported",
            })
            tid += 1

    gold_pairs = {(s, o) for s, _r, o in gold_triples}

    # every emitted spurious triple gets a FRESH (person, org) pair -- so a
    # hallucination never coincides with a distractor's doc, and a distractor
    # never overlaps a gold pair or another distractor
    used_pairs: set[tuple] = set(gold_pairs)

    def fresh_pair() -> tuple[dict, dict] | None:
        for _ in range(500):
            p = rng.choice(persons)
            # exclude p's orgs AND its homograph partner's orgs -- otherwise a
            # spurious triple with p's ambiguous surface would co-occur (by
            # surface) in the partner's real doc, leaking the homograph mechanism
            # into the grounding axis
            excl = {o["entity_id"] for o in person_orgs[p["entity_id"]]}
            if p["entity_id"] in partner:
                excl |= {o["entity_id"] for o in person_orgs[partner[p["entity_id"]]]}
            pool = [o for o in orgs if o["entity_id"] not in excl]
            o = rng.choice(pool)
            if (p["entity_id"], o["entity_id"]) not in used_pairs:
                used_pairs.add((p["entity_id"], o["entity_id"]))
                return p, o
        return None

    # --- distractor emitted triples: entities co-occur in a doc that states the
    #     SIBLING relation, but the candidate claims the other one ---
    sib = {"works_at": "advises", "advises": "works_at"}
    for _ in range(n_distractor):
        got = fresh_pair()
        if not got:
            continue
        p, o = got
        rel = rng.choice(_PERSON_RELS)
        subj_s = surface_of(p)
        docs[f"D{did:04d}"] = RELATIONS[sib[rel]]["template"].format(
            subj=subj_s, obj=o["canonical"])
        did += 1
        emitted.append({
            "triple_id": f"T{tid:04d}", "subj": p["entity_id"], "subj_surface": subj_s,
            "subj_type": "PERSON", "rel": rel, "obj": o["entity_id"],
            "obj_surface": o["canonical"], "obj_type": "ORG",
            "gold_verdict": "unsupported", "gold_class": "distractor",
        })
        tid += 1

    # --- hallucinated emitted triples: pair never co-occurs in any doc ---
    for _ in range(n_hallucinated):
        got = fresh_pair()
        if not got:
            continue
        p, o = got
        emitted.append({
            "triple_id": f"T{tid:04d}", "subj": p["entity_id"], "subj_surface": surface_of(p),
            "subj_type": "PERSON", "rel": rng.choice(_PERSON_RELS), "obj": o["entity_id"],
            "obj_surface": o["canonical"], "obj_type": "ORG",
            "gold_verdict": "unsupported", "gold_class": "hallucinated",
        })
        tid += 1

    for p in persons:
        p.pop("_ambiguous", None)

    # gold surface triples (for extraction-F1) -- what a faithful extractor should
    # recover from the REAL docs
    by_id = {e["entity_id"]: e for e in persons + orgs}
    gold_surfaces = [(surface_of(by_id[s]) if by_id[s]["type"] == "PERSON" else by_id[s]["canonical"],
                      r, by_id[o]["canonical"]) for s, r, o in gold_triples]

    return {
        "entities": persons + orgs,
        "gold_triples": gold_triples,
        "gold_surfaces": gold_surfaces,
        "docs": docs,
        "mentions": mentions,
        "emitted": emitted,
        "homograph_ids": sorted(homograph_ids),
    }
