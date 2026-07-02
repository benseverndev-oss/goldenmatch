"""Substrate-quality scoring over a BUILT graph (pure; operates on the graph dict + gold mentions)."""
from __future__ import annotations

from collections import defaultdict


def _base_doc_id(ref: str) -> str:
    """A source_ref may carry a `::N` co-occurrence suffix; the base doc id is `src::rel::dst` (3 parts).
    Re-join the first three `::`-separated parts (entity ids use a single `:`, so `::` is unambiguous)."""
    parts = ref.split("::")
    return "::".join(parts[:3]) if len(parts) >= 3 else ref


def _assign_nodes(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> dict[int, int]:
    """Per gold-mention index -> the built node it landed in (via the doc's edge endpoints), or a fresh
    NEGATIVE id when the doc produced no matching edge (extraction miss / dropped self-loop). Shared by
    `align_mentions_to_nodes` (which groups these) and `fragmentation_report` (which counts them)."""
    by_doc: dict[str, tuple[int, int]] = {}
    for e in graph.get("edges", ()):
        for ref in e.get("source_refs", ()):
            by_doc.setdefault(_base_doc_id(ref), (e["subj"], e["obj"]))
    node_of: dict[int, int] = {}
    fresh = -1
    for i, (entity_id, _surface, doc_id) in enumerate(gold_mentions):
        edge = by_doc.get(_base_doc_id(doc_id))
        if edge is None:
            node_of[i] = fresh
            fresh -= 1
            continue
        parts = doc_id.split("::")
        src_id, dst_id = parts[0], parts[2]
        node_of[i] = edge[0] if entity_id == src_id else edge[1] if entity_id == dst_id else fresh
        if node_of[i] == fresh:
            fresh -= 1
    return node_of


def align_mentions_to_nodes(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> list[list[int]]:
    """Cluster gold-mention INDICES by the built node each landed in. Exact, doc-keyed (not surface):
    each engineered doc is ONE edge `src::rel::dst`; the built edge for that doc (matched by base doc id
    in `source_refs`) gives endpoints subj=src-node, obj=dst-node. Assumption: direction-canonicalization
    OFF (subj==src). Unmatched mention (no edge for its doc) -> its own singleton (extraction miss).

    KNOWN LIMIT (documented, not fixed in v1): if the resolver merges a single doc's src+dst (distinct
    entities) into one node, the build drops the self-loop -> no edge -> both mentions become singletons,
    mislabeling a within-doc over-merge as recall misses. Does not affect the ambiguity-driven (cross-doc,
    recall-side) headline."""
    groups: dict[int, list[int]] = {}
    for i, node in _assign_nodes(graph, gold_mentions).items():
        groups.setdefault(node, []).append(i)
    return [sorted(v) for v in groups.values()]


def _assign_real_nodes(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> dict[int, int]:
    """Per gold-mention index -> built node, by SURFACE+DOC match (no engineered `src::rel::dst` doc-id
    oracle -- real prose has none). Candidates = nodes touched by an edge sourced from the mention's doc;
    pick exact surface match (case-folded) over substring, tie-broken by LOWEST node id (deterministic);
    no match -> a UNIQUE decrementing negative (orphan singleton, like `_assign_nodes`). On real articles
    the candidate set is large, so precision rides on exact-before-substring."""
    id2surf: dict[int, set[str]] = {}
    for e in graph.get("entities", ()):
        nid = e.get("entity_id")
        surfs = {str(s).strip().lower() for s in e.get("surface_names", ()) if s}
        cn = str(e.get("canonical_name", "")).strip().lower()
        if cn:
            surfs.add(cn)
        id2surf[nid] = surfs
    by_doc: dict[str, set[int]] = {}
    for e in graph.get("edges", ()):
        for ref in e.get("source_refs", ()):
            by_doc.setdefault(_base_doc_id(ref), set()).update((e.get("subj"), e.get("obj")))
    node_of: dict[int, int] = {}
    fresh = -1
    for i, (_eid, surface, doc) in enumerate(gold_mentions):
        s = str(surface).strip().lower()
        cands = by_doc.get(_base_doc_id(doc), set())
        exact = sorted(n for n in cands if s in id2surf.get(n, ()))
        if exact:
            node_of[i] = exact[0]
            continue
        substr = sorted(n for n in cands if any(s and (s in sn or sn in s) for sn in id2surf.get(n, ())))
        if substr:
            node_of[i] = substr[0]
            continue
        node_of[i] = fresh
        fresh -= 1
    return node_of


def align_real_mentions_to_nodes(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> list[list[int]]:
    """Cluster gold-mention indices by built node via surface+doc match -- the real-prose counterpart to
    `align_mentions_to_nodes` (which needs the engineered doc-id). Same output shape; reproduces the oracle
    on engineered graphs (1 edge/doc, distinct surfaces)."""
    groups: dict[int, list[int]] = {}
    for i, node in _assign_real_nodes(graph, gold_mentions).items():
        groups.setdefault(node, []).append(i)
    return [sorted(v) for v in groups.values()]


def real_alignment_coverage(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> float:
    """Fraction of gold mentions assigned to a real (non-orphan) built node. A low value means the ER score
    is measuring alignment failure, not resolution -- report it alongside R(B)."""
    node_of = _assign_real_nodes(graph, gold_mentions)
    if not node_of:
        return 1.0
    return sum(1 for n in node_of.values() if n >= 0) / len(node_of)


def _alias_match_surface(surf_lc: str, match_set: set[str]) -> bool:
    """True iff a (already lowercased) surface matches a lowercased alias/gold `match_set`:
    exact membership OR substring either way. The single shared predicate behind both the
    aligned-node substring fallback and the GLiNER-probe match, so they cannot drift."""
    if not surf_lc:
        return False
    if surf_lc in match_set:
        return True
    return any(surf_lc in m or m in surf_lc for m in match_set if m)


def _assign_real_nodes_aliased(graph: dict, gold_mentions, qid_aliases) -> dict[int, int]:
    """Like `_assign_real_nodes` but the match target is the mention's QID ALIAS SET (union the literal
    wikilink surface), so a built node is found by ANY of the entity's aliases -- dissolving the
    wikilink-surface-vs-extracted-form mismatch (`Big Blue` gold aligns to the `IBM` node). Node surface set
    = `surface_names` + `canonical_name` (case-folded). Largest alias-intersection wins, lowest node id
    breaks ties; no match -> a UNIQUE decrementing negative (orphan)."""
    id2surf: dict[int, set[str]] = {}
    for e in graph.get("entities", ()):
        surfs = {str(s).strip().lower() for s in e.get("surface_names", ()) if s}
        cn = str(e.get("canonical_name", "")).strip().lower()
        if cn:
            surfs.add(cn)
        id2surf[e.get("entity_id")] = surfs
    by_doc: dict[str, set[int]] = {}
    for e in graph.get("edges", ()):
        for ref in e.get("source_refs", ()):
            by_doc.setdefault(_base_doc_id(ref), set()).update((e.get("subj"), e.get("obj")))
    # Node provenance: nodes whose OWN source_refs name a doc are candidates there too, reaching
    # per-doc-relationless entities (present, but no surviving edge in this doc) the edge-derived
    # by_doc can't. UNION with by_doc -> always a superset (never a coverage regression); empty when
    # no entity carries source_refs (byte-identical to the edge-only path).
    node_by_doc: dict[str, set[int]] = defaultdict(set)
    for e in graph.get("entities", ()):
        for ref in e.get("source_refs", ()):
            node_by_doc[_base_doc_id(ref)].add(e.get("entity_id"))
    node_of: dict[int, int] = {}
    fresh = -1
    for i, (qid, surface, doc) in enumerate(gold_mentions):
        match = set(qid_aliases.get(qid, ())) | {str(surface).strip().lower()}
        d = _base_doc_id(doc)
        cands = sorted(by_doc.get(d, set()) | node_by_doc.get(d, set()))  # sorted -> lowest id on tie
        best, best_ov = None, 0
        for n in cands:                                            # exact set-intersection: largest wins
            ov = len(id2surf.get(n, set()) & match)
            if ov > best_ov:
                best, best_ov = n, ov
        if best is None:                                           # substring fallback via the shared primitive
            for n in cands:                                        # (parity with the probe; behavior-preserving)
                if any(_alias_match_surface(sn, match) for sn in id2surf.get(n, ())):
                    best = n
                    break
        if best is not None:
            node_of[i] = best
        else:
            node_of[i] = fresh
            fresh -= 1
    return node_of


def align_real_mentions_to_nodes_aliased(graph: dict, gold_mentions, qid_aliases) -> list[list[int]]:
    """Cluster gold-mention indices by built node via ALIAS-set + doc match (the clean-absolute L2 aligner)."""
    groups: dict[int, list[int]] = {}
    for i, node in _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases).items():
        groups.setdefault(node, []).append(i)
    return [sorted(v) for v in groups.values()]


def real_alignment_coverage_aliased(graph: dict, gold_mentions, qid_aliases) -> float:
    """Fraction of gold mentions aligned to a real node under alias-anchoring. With surface-mismatch
    removed, this is the true EXTRACTION-RECALL floor (an entity the extractor never produced can't align)."""
    node_of = _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases)
    if not node_of:
        return 1.0
    return sum(1 for n in node_of.values() if n >= 0) / len(node_of)


def fragmentation_report(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> dict:
    """Diagnose WHY cross-doc co-reference is lost: for each GOLD entity, how many distinct built NODES
    its mentions scattered across, and whether those nodes differ in NAME or in TYPE. `mean_nodes_per_entity`
    == 1.0 is perfect unification; higher means the same entity became many nodes. Of the fragmented
    entities, `name_jitter_frac`/`type_jitter_frac` attribute the cause (the extractor rendering one entity
    under varied names/types across docs -> mismatched `record_key` -> no store merge); `identical_frac` is
    fragmented-despite-identical-(name,typ) == a store-merge BUG, not modeling. Ignores fresh (<0) miss
    nodes so this isolates cross-doc NON-MERGE from extraction drop (that is `edge_recall`)."""
    id2nt: dict[int, tuple[str, str]] = {
        e["entity_id"]: (e.get("canonical_name", ""), e.get("typ", "")) for e in graph.get("entities", ())
    }
    node_of = _assign_nodes(graph, gold_mentions)
    by_entity: dict[str, set[int]] = {}
    for i, (eid, _s, _d) in enumerate(gold_mentions):
        node = node_of[i]
        if node < 0:  # extraction-miss singleton -> not a cross-doc non-merge; excluded
            continue
        by_entity.setdefault(eid, set()).add(node)
    multi = {eid: nodes for eid, nodes in by_entity.items() if len(nodes) > 1}
    total = len(by_entity) or 1
    name_j = type_j = ident = 0
    worst: list[tuple[str, int, list[tuple[str, str]]]] = []
    for eid, nodes in multi.items():
        nts = [id2nt.get(n, ("?", "?")) for n in nodes]
        names = {nt[0] for nt in nts}
        types = {nt[1] for nt in nts}
        if len(names) > 1:
            name_j += 1
        if len(types) > 1:
            type_j += 1
        if len(names) == 1 and len(types) == 1:
            ident += 1
        worst.append((eid, len(nodes), sorted(nts)))
    worst.sort(key=lambda t: -t[1])
    nodes_per = [len(nodes) for nodes in by_entity.values()]
    return {
        "mean_nodes_per_entity": sum(nodes_per) / total,
        "max_nodes_per_entity": max(nodes_per, default=0),
        "fragmented_entities": len(multi),
        "total_entities": len(by_entity),
        "name_jitter_frac": name_j / (len(multi) or 1),
        "type_jitter_frac": type_j / (len(multi) or 1),
        "identical_frac": ident / (len(multi) or 1),
        "worst": worst[:12],
    }


def graph_coherence(graph: dict) -> dict:
    """Connected components of the built graph (edges undirected) + largest-component fraction. A
    coherent knowledge base is few components / one dominant; the construction ceiling shows as many
    small components."""
    nodes = {e["entity_id"] for e in graph.get("entities", ())}
    parent: dict[int, int] = {n: n for n in nodes}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in graph.get("edges", ()):
        parent[find(e["subj"])] = find(e["obj"])
    roots = [find(n) for n in parent]
    if not roots:
        return {"components": 0, "largest_fraction": 0.0}
    from collections import Counter
    sizes = Counter(roots)
    return {"components": len(sizes), "largest_fraction": max(sizes.values()) / len(roots)}


def edge_recall(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> float:
    """Fraction of GOLD edge-docs that produced a surviving built edge (base doc id present in some
    edge's `source_refs`). This is extraction COMPLETENESS: a doc whose edge was never extracted -- or
    whose endpoints the resolver collapsed into a self-loop that `build_batch` drops -- contributes no
    edge, so both its gold mentions orphan. Decomposes the R(B) floor: low edge_recall => the edges
    aren't in the graph at all (extraction/over-merge), NOT a cross-doc resolution miss. 1.0 if no gold."""
    gold_docs = {_base_doc_id(doc_id) for (_eid, _surface, doc_id) in gold_mentions}
    if not gold_docs:
        return 1.0
    built_docs: set[str] = set()
    for e in graph.get("edges", ()):
        for ref in e.get("source_refs", ()):
            built_docs.add(_base_doc_id(ref))
    return len(gold_docs & built_docs) / len(gold_docs)


def provenance_coverage(graph: dict) -> float:
    """Fraction of edges carrying a non-empty `source_refs` (every fact traceable to a source). ~1.0 for
    goldengraph alone (it always stamps doc ids); discriminating in the multi-engine bake-off."""
    edges = list(graph.get("edges", ()))
    if not edges:
        return 1.0
    return sum(1 for e in edges if e.get("source_refs")) / len(edges)


def score_substrate(*, gold_mentions, resolver_clusters, graph) -> dict:
    """Assemble the substrate scoreboard. ER-F1(A) = the resolver clustering scored vs gold; ER-F1(B) =
    the built-graph mention->node clustering scored vs gold; A-B gap = extraction-induced fragmentation;
    plus coherence + provenance on the built graph. All over the SAME gold-mention index space."""
    from erkgbench import metrics

    entity_ids = [m[0] for m in gold_mentions]
    a = metrics.score(entity_ids, resolver_clusters)
    b = metrics.score(entity_ids, align_mentions_to_nodes(graph, gold_mentions))
    coh = graph_coherence(graph)
    return {
        "er_f1_a": a.f1, "er_p_a": a.precision, "er_r_a": a.recall,
        "er_f1_b": b.f1, "er_p_b": b.precision, "er_r_b": b.recall,
        "ab_gap": a.f1 - b.f1,
        "components": coh["components"], "largest_fraction": coh["largest_fraction"],
        "provenance": provenance_coverage(graph),
        "edge_recall": edge_recall(graph, gold_mentions),
    }


def gliner_probe_report(graph: dict, gold_mentions, qid_aliases, gliner_by_doc) -> dict:
    """Measure GLiNER's addressable recall against the real-prose substrate residual.

    Splits the unaligned gold (`_assign_real_nodes_aliased` node_of < 0) into NER-miss (no graph node
    matches the alias set anywhere -> GLiNER-addressable) vs edge-miss (a node exists but produced no
    in-doc edge -> GLiNER can't help). The gate number is `ner_recovered_frac`: of the NER-miss gold,
    the share whose entity GLiNER surfaces (in the same doc). Pure; `gliner_by_doc` maps base doc id ->
    set of raw GLiNER surface strings."""
    node_of = _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases)
    # node surfaces (case-folded), exactly as the aligner builds them
    id2surf: dict[int, set[str]] = {}
    for e in graph.get("entities", ()):
        surfs = {str(s).strip().lower() for s in e.get("surface_names", ()) if s}
        cn = str(e.get("canonical_name", "")).strip().lower()
        if cn:
            surfs.add(cn)
        id2surf[e.get("entity_id")] = surfs

    def _match_set(qid, surface):
        return set(qid_aliases.get(qid, ())) | {str(surface).strip().lower()}

    def _entity_exists(match_set):  # any node anywhere whose surfaces match -> NER present
        return any(
            any(_alias_match_surface(s, match_set) for s in surfs) for surfs in id2surf.values()
        )

    def _gliner_hit(doc, match_set):  # any GLiNER surface IN THIS DOC matches
        surfaces = gliner_by_doc.get(_base_doc_id(doc), ())
        return any(_alias_match_surface(str(g).strip().lower(), match_set) for g in surfaces)

    n_gold = len(gold_mentions)
    gliner_matched = ner_miss = edge_miss = ner_recovered = missed_recovered = missed = 0
    for i, (qid, surface, doc) in enumerate(gold_mentions):
        ms = _match_set(qid, surface)
        hit = _gliner_hit(doc, ms)
        gliner_matched += hit
        if node_of.get(i, -1) < 0:
            missed += 1
            if hit:
                missed_recovered += 1
            if _entity_exists(ms):
                edge_miss += 1
            else:
                ner_miss += 1
                if hit:
                    ner_recovered += 1

    # junk proxy: GLiNER surfaces (per doc) matching NO gold of that doc
    gold_by_doc: dict[str, list] = {}
    for (qid, surface, doc) in gold_mentions:
        gold_by_doc.setdefault(_base_doc_id(doc), []).append(_match_set(qid, surface))
    total_surf = junk = 0
    for doc, surfaces in gliner_by_doc.items():
        base = _base_doc_id(doc)
        golds = gold_by_doc.get(base, [])
        for g in surfaces:
            total_surf += 1
            g_lc = str(g).strip().lower()
            if not any(_alias_match_surface(g_lc, ms) for ms in golds):
                junk += 1

    def _frac(num, den):
        return num / den if den else 0.0

    return {
        "n_gold": n_gold,
        "n_missed": missed,
        "n_ner_miss": ner_miss,
        "n_edge_miss": edge_miss,
        "gliner_recall": _frac(gliner_matched, n_gold),
        "llm_coverage": _frac(sum(1 for v in node_of.values() if v >= 0), n_gold),
        "ner_recovered_frac": _frac(ner_recovered, ner_miss),
        "residual_recovered_frac": _frac(missed_recovered, missed),
        "junk_rate": _frac(junk, total_surf),
    }
