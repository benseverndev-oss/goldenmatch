"""CLEAR-KG Track A: extraction (table stakes) + a measured finding about the
metric itself.

Table stakes: triple-level precision / recall / F1 vs the gold KG, matching the
Text2KGBench / Re-DocRED convention (report BOTH `exact` and `relaxed`/
canonicalized matching). We are NOT trying to beat the LLM-extraction pack here
(Re-DocRED SOTA ~74.6 F1 LLM / ~80.7 BERT). Phase 0 delivers the convention-
matching HARNESS -- ready to score an LLM extractor's output on the real-data
corpus (the next phase) -- and one methodological finding that is ours:

  Canonicalized ("relaxed") triple matching is itself an ENTITY-RESOLUTION
  problem. The field resolves a predicted entity surface to a gold entity by
  STRING match / similarity, which MIS-CREDITS homographs (two gold entities
  sharing a surface). ER-aware canonicalization scores them correctly. So even
  the extraction metric inherits the moat.

Deterministic reference extractors (`extractors.py`) stand in for an LLM pass;
the object of study is the metric's behavior, not a SOTA extraction number.
Relations are treated as schema-closed (normalized to canonical in every mode),
so the ENTITY surface is the sole axis under study.
"""
from __future__ import annotations

import random

from generate import _LAST, _ORG, _PLACE, _abbrev

_FIRST_A = ["Jane", "Wei", "Maria", "Ahmed", "Liam", "Sofia", "Raj", "Elena",
            "David", "Yuki", "Omar", "Anna"]

# canonical relation -> surface paraphrases (schema-closed: normalized in all
# modes, so relation phrasing is not the axis under study -- the subject surface
# is). Keyed by the gold object's type.
REL_SYNONYMS: dict[str, list[str]] = {
    "employed_by": ["works at", "is employed by", "joined", "is with"],
    "based_in": ["is based in", "is located in", "lives in"],
}
_REL_FOR_TYPE = {"ORG": "employed_by", "PLACE": "based_in"}


def _phrase_to_canonical() -> dict[str, str]:
    out = {}
    for canon, phrases in REL_SYNONYMS.items():
        for p in phrases:
            out[p] = canon
    return out


def generate_extraction_corpus(
    *,
    seed: int = 0,
    n_persons: int = 8,
    n_homograph_pairs: int = 2,
    triples_per_person: int = 3,
) -> dict:
    """Return ``{entities, gold, docs, homograph_ids}``.

    entities: [{entity_id, type, canonical, aliases}]
    gold:     [(subj_id, rel_canonical, obj_id)]  -- the gold KG
    docs:     {doc_id: "SUBJ_SURFACE REL_PHRASE OBJ_SURFACE."}  -- one triple each,
              subject rendered with alias/homograph variation, object canonical.
    homograph_ids: person ids sharing an ambiguous surface with a partner.
    """
    rng = random.Random(seed)

    # --- persons (subjects) with unique names + aliases ---
    persons: list[dict] = []
    used = set()
    for i in range(n_persons):
        while True:
            nm = f"{rng.choice(_FIRST_A)} {rng.choice(_LAST)}"
            if nm not in used:
                used.add(nm)
                break
        persons.append({"entity_id": f"P{i:02d}", "type": "PERSON", "canonical": nm,
                        "aliases": [nm, _abbrev(nm), nm.split(" ", 1)[1]]})

    # --- objects (orgs + places), each used by exactly one person so homograph
    #     PARTNERS get DISJOINT object sets (co-mention uniquely disambiguates) ---
    objs: list[dict] = []
    for i, nm in enumerate(_ORG):
        objs.append({"entity_id": f"O{i:02d}", "type": "ORG", "canonical": nm, "aliases": [nm]})
    for i, nm in enumerate(_PLACE):
        objs.append({"entity_id": f"L{i:02d}", "type": "PLACE", "canonical": nm, "aliases": [nm]})
    rng.shuffle(objs)
    need = n_persons * triples_per_person
    if need > len(objs):
        raise ValueError(f"need {need} distinct objects, have {len(objs)}")

    # --- inject homographs: force partner pairs to share an ambiguous surface ---
    homograph_ids: set[str] = set()
    order = list(range(n_persons))
    rng.shuffle(order)
    for k in range(min(n_homograph_pairs, n_persons // 2)):
        a, b = persons[order[2 * k]], persons[order[2 * k + 1]]
        shared = _abbrev(a["canonical"])  # e.g. "J. Smith"
        for e in (a, b):
            if shared not in e["aliases"]:
                e["aliases"].append(shared)
            e["_ambiguous"] = shared
            homograph_ids.add(e["entity_id"])

    # --- gold triples + docs (one sentence each) ---
    gold: list[tuple] = []
    docs: dict[str, str] = {}
    did = 0
    obj_cursor = 0
    for p in persons:
        my_objs = objs[obj_cursor:obj_cursor + triples_per_person]
        obj_cursor += triples_per_person
        for o in my_objs:
            rel = _REL_FOR_TYPE[o["type"]]
            gold.append((p["entity_id"], rel, o["entity_id"]))
            # homograph persons always surface with the ambiguous alias; others
            # surface with their canonical name most of the time (as a real
            # extractor would) but sometimes an abbreviation/surname -- so `exact`
            # is a credible baseline that still pays the alias-canonicalization
            # penalty, and homographs force the ambiguous surface.
            if "_ambiguous" in p:
                subj_surface = p["_ambiguous"]
            elif rng.random() < 0.6:
                subj_surface = p["canonical"]
            else:
                subj_surface = rng.choice([_abbrev(p["canonical"]), p["canonical"].split(" ", 1)[1]])
            phrase = rng.choice(REL_SYNONYMS[rel])
            docs[f"D{did:04d}"] = f"{subj_surface} {phrase} {o['canonical']}."
            did += 1

    for p in persons:
        p.pop("_ambiguous", None)

    return {
        "entities": persons + objs,
        "gold": gold,
        "docs": docs,
        "homograph_ids": sorted(homograph_ids),
    }
