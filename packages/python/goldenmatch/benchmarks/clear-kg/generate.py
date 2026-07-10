"""Synthetic corpus + exact ground-truth generator for CLEAR-KG Phase 0.

Reverses the usual direction: fabricate a small known KG (entities + triples) with
CONTROLLED HOMOGRAPHS, then emit a multi-document corpus expressing it -- so we
know, by construction, every mention's gold entity id, every triple, and every
provenance span. Deterministic (seeded), offline, no LLM (Phase 0 uses templated
prose; LLM-generated prose + real Wikidata content is Phase 1).

The load-bearing knob is the HOMOGRAPH: two DISTINCT entities that share a surface
string ("J. Smith") but have DISJOINT co-mention neighborhoods. Exact-surface /
cosine ER merges them; neighborhood-aware ER keeps them apart. Phase 0 measures
exactly that gap.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

_FIRST = ["John", "Jane", "Julia", "James", "Wei", "Maria", "Ahmed", "Sofia",
          "Liam", "Chen", "Omar", "Elena", "Raj", "Anna", "David", "Yuki"]
_LAST = ["Smith", "Chen", "Garcia", "Khan", "Muller", "Rossi", "Tanaka", "Novak",
         "Silva", "Kim", "Ivanov", "Dubois"]
_ORG = ["Mercy Hospital", "Baker Legal", "Acme Labs", "Northwind Bank",
        "Cedar Clinic", "Vertex Partners", "Summit Foundry", "Harbor Institute",
        "Ridge Analytics", "Delta Foods", "Union Press", "Pioneer Motors"]
_PLACE = ["Portland", "Lyon", "Osaka", "Cairo", "Bergen", "Turin", "Pune",
          "Cordoba", "Aarhus", "Leeds", "Kobe", "Ghent"]
_REL = ["works at", "is affiliated with", "collaborated with",
        "is based in", "advised", "joined"]


@dataclass
class Entity:
    entity_id: str
    typ: str
    canonical: str
    aliases: list[str] = field(default_factory=list)
    neighbors: list[str] = field(default_factory=list)  # neighbor entity_ids


def _abbrev(person_name: str) -> str:
    first, last = person_name.split(" ", 1)
    return f"{first[0]}. {last}"


def generate_corpus(
    *,
    seed: int = 0,
    n_entities: int = 20,
    n_homograph_pairs: int = 5,
    docs_per_entity: int = 3,
    neighbors_per_entity: int = 4,
    comentions_per_doc: int = 3,
) -> dict:
    """Return {'entities', 'mentions', 'triples', 'docs', 'homograph_surfaces'}.

    mentions: [{mention_id, doc_id, span:[s,e], surface, gold_entity_id}]
    entities: [{entity_id, typ, canonical, aliases}]
    triples:  [{subj, rel, obj, provenance:[{doc_id, span}]}]
    docs:     {doc_id: text}
    """
    rng = random.Random(seed)
    ents: list[Entity] = []

    # --- build distinct entities across three types ---
    used_person, used_org, used_place = set(), set(), set()
    for i in range(n_entities):
        t = ["PERSON", "ORG", "PLACE"][i % 3]
        if t == "PERSON":
            while True:
                nm = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
                if nm not in used_person:
                    used_person.add(nm)
                    break
            aliases = [nm, _abbrev(nm), nm.split(" ", 1)[1]]  # full, "J. Smith", surname
        elif t == "ORG":
            nm = _ORG[len(used_org) % len(_ORG)]
            used_org.add(nm)
            aliases = [nm, nm.split(" ", 1)[0]]
        else:
            nm = _PLACE[len(used_place) % len(_PLACE)]
            used_place.add(nm)
            aliases = [nm]
        ents.append(Entity(entity_id=f"E{i:03d}", typ=t, canonical=nm, aliases=aliases))

    by_id = {e.entity_id: e for e in ents}

    # --- assign neighbor sets (the disambiguating co-mention signal) ---
    ids = [e.entity_id for e in ents]
    for e in ents:
        pool = [x for x in ids if x != e.entity_id]
        e.neighbors = rng.sample(pool, min(neighbors_per_entity, len(pool)))

    # --- inject HOMOGRAPHS: pick person pairs, force a shared ambiguous surface,
    #     and make their neighbor sets DISJOINT so neighborhood can separate them ---
    persons = [e for e in ents if e.typ == "PERSON"]
    rng.shuffle(persons)
    homograph_surfaces: set[str] = set()
    for k in range(min(n_homograph_pairs, len(persons) // 2)):
        a, b = persons[2 * k], persons[2 * k + 1]
        shared = _abbrev(a.canonical)  # e.g. "J. Smith"
        for e in (a, b):
            if shared not in e.aliases:
                e.aliases.append(shared)
        homograph_surfaces.add(shared)
        # force disjoint neighborhoods so the pair is separable by co-mention
        b_pool = [x for x in ids if x not in set(a.neighbors) | {a.entity_id, b.entity_id}]
        b.neighbors = rng.sample(b_pool, min(len(a.neighbors), len(b_pool)))

    # --- generate docs, mentions (with spans), triples (with provenance) ---
    mentions: list[dict] = []
    triples: list[dict] = []
    docs: dict[str, str] = {}
    mid = 0
    did = 0

    for e in ents:
        # A CONSISTENT signature: the same neighbors, by their DISTINCTIVE canonical
        # names, co-occur in every one of this entity's docs -> same-entity subject
        # mentions share a neighborhood; homograph entities (disjoint neighbors) do
        # not. Co-mentions are CONTEXT FEATURES, not themselves resolved mentions;
        # Track B resolves the ambiguous SUBJECT mention (one per doc).
        signature = [by_id[nb].canonical for nb in e.neighbors[:comentions_per_doc]]
        amb = [a for a in e.aliases if a in homograph_surfaces]
        for _ in range(docs_per_entity):
            doc_id = f"D{did:04d}"
            did += 1
            surface = rng.choice(amb) if (amb and rng.random() < 0.7) else rng.choice(e.aliases)
            parts: list[str] = [surface]
            span = [0, len(surface)]
            for nb in e.neighbors[:comentions_per_doc]:
                rel = rng.choice(_REL)
                parts.append(f" {rel} ")
                sub_start = sum(len(p) for p in parts)
                nb_surface = by_id[nb].canonical
                parts.append(nb_surface)
                parts.append(".")
                triples.append({"subj": e.entity_id, "rel": rel, "obj": nb,
                                "provenance": [{"doc_id": doc_id,
                                                "span": [sub_start, sub_start + len(nb_surface)]}]})
            docs[doc_id] = "".join(parts)
            mentions.append({"mention_id": f"M{mid:05d}", "doc_id": doc_id, "span": span,
                             "surface": surface, "gold_entity_id": e.entity_id,
                             "neighbor_surfaces": signature})
            mid += 1

    return {
        "entities": [{"entity_id": e.entity_id, "typ": e.typ,
                      "canonical": e.canonical, "aliases": e.aliases} for e in ents],
        "mentions": mentions,
        "triples": triples,
        "docs": docs,
        "homograph_surfaces": sorted(homograph_surfaces),
    }
