"""ER-quality dial key-policies.

The dial controls cross-document identity by choosing the `record_key` each
`(entity_id, surface)` mention emits; the store merges two mentions across
documents iff they share a record_key. Four levels:

  oracle      -> key = canonical id   (all surfaces of an entity merge: perfect ER)
  goldengraph -> key = dedupe_df fuzzy cluster (merges string-close variants)
  name_only   -> key = exact surface  (only identical surfaces merge)
  none        -> unique key per pair  (nothing merges: maximal under-merge)

`dedupe_df` runs on name+type only (two fields) so it stays under the 3-field
cross-encoder rerank trigger -- no HuggingFace download, fully offline.
"""
from __future__ import annotations

from pathlib import Path

from .corpora import QACorpus
from .gold import GoldGraph


def _entity_surfaces(g: GoldGraph) -> list[tuple[str, str, str]]:
    """[(entity_id, surface, typ), ...] over the concept universe (canonical +
    variants). The keyspace the build looks up: every rendered surface is one of
    these, so `km[(entity_id, rendered_surface)]` always hits."""
    from dataset.concepts_loader import load_concepts  # type: ignore

    bench_root = Path(__file__).resolve().parents[2]
    out: list[tuple[str, str, str]] = []
    for c in load_concepts(bench_root / "dataset" / "concepts.jsonl"):
        surfaces = [c.concept] + [v.surface for v in c.variants]
        for s in dict.fromkeys(surfaces):
            out.append((c.canonical_id, s, c.entity_type))
    return out


def surface_to_canon(g: GoldGraph) -> dict[str, set[str]]:
    """Read-side side map: surface string -> set of canonical ids that use it.
    A set because a surface can collide across entities (the name_only case)."""
    m: dict[str, set[str]] = {}
    for eid, surface, _typ in _entity_surfaces(g):
        m.setdefault(surface, set()).add(eid)
    return m


def oracle_keys(corpus: QACorpus, g: GoldGraph) -> dict[tuple[str, str], str]:
    return {(eid, s): eid for (eid, s, _t) in _entity_surfaces(g)}


def none_keys(corpus: QACorpus, g: GoldGraph) -> dict[tuple[str, str], str]:
    return {(eid, s): f"{eid}::{s}::{i}" for i, (eid, s, _t) in enumerate(_entity_surfaces(g))}


def name_only_keys(corpus: QACorpus, g: GoldGraph) -> dict[tuple[str, str], str]:
    return {(eid, s): s for (eid, s, _t) in _entity_surfaces(g)}


def goldengraph_keys(corpus: QACorpus, g: GoldGraph) -> dict[tuple[str, str], str]:
    rows = _entity_surfaces(g)
    import goldenmatch as gm
    import polars as pl

    df = pl.DataFrame(
        {"name": [s for (_e, s, _t) in rows], "type": [t for (_e, _s, t) in rows]}
    )
    # rerank OFF: name+type is two fields, under the 3-field cross-encoder trigger.
    result = gm.dedupe_df(df)
    # DedupeResult.clusters: {cluster_id: {"members":[row_idx,...], "size":n}}. It may
    # only surface multi-member clusters -> default every row to its own singleton.
    cluster_of: dict[int, str] = {i: f"s{i}" for i in range(len(rows))}
    for cid, info in result.clusters.items():
        for ri in info["members"]:
            cluster_of[int(ri)] = f"c{cid}"
    return {(rows[i][0], rows[i][1]): cluster_of[i] for i in range(len(rows))}
