"""CLEAR-KG Track B: corpus-level entity resolution over mentions.

Input: the gold mention set (surfaces + which document each is in). Task: cluster
mentions so each real entity is exactly one cluster -- including keeping HOMOGRAPHS
(same surface, different entity) apart.

Two engines:
  exact_surface  -- merge mentions with identical normalized surface (Neo4j's
                    default `if same name: merge`). Over-merges homographs AND
                    under-merges alias variants. The incumbent baseline.
  goldenmatch    -- principled ER: block on the surface's last token, then a
                    weighted matchkey of surface similarity + CO-MENTION set
                    overlap. Co-mention overlap (the neighborhood signal) splits
                    homographs and merges alias variants -- the moat.
"""
from __future__ import annotations

import os
from collections import defaultdict

import er_utils
import polars as pl
from er_utils import encode_set, norm

BLOCK_COL = "__block__"


def build_mention_frame(mentions: list[dict]) -> pl.DataFrame:
    """One row per (subject) mention. `neighbors` = the mention's co-mention
    context (distinctive canonical surfaces of the entities named alongside it) --
    the disambiguating signal. `__block__` = last token of the normalized surface
    (brings alias variants AND homographs into one block so scoring must separate
    them). Falls back to same-document co-mentions if `neighbor_surfaces` is absent."""
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for m in mentions:
        by_doc[m["doc_id"]].append(m)

    rows = []
    for rid, m in enumerate(mentions):
        if "neighbor_surfaces" in m:
            nbr = m["neighbor_surfaces"]
        else:
            nbr = [o["surface"] for o in by_doc[m["doc_id"]]
                   if o["mention_id"] != m["mention_id"]]
        nsurf = norm(m["surface"])
        block = nsurf.split()[-1] if nsurf else ""
        rows.append({
            "__row_id__": rid,
            "mention_id": m["mention_id"],
            BLOCK_COL: block,
            "surface": nsurf,
            "neighbors": encode_set(nbr),
        })
    return pl.DataFrame(rows, schema={
        "__row_id__": pl.Int64, "mention_id": pl.Utf8, BLOCK_COL: pl.Utf8,
        "surface": pl.Utf8, "neighbors": pl.Utf8,
    })


def _clusters_to_mentions(clusters: dict, df: pl.DataFrame) -> list[list[str]]:
    row_to_mid = dict(zip(df["__row_id__"].to_list(), df["mention_id"].to_list()))
    covered: set[int] = set()
    out: list[list[str]] = []
    for info in clusters.values():
        members = info.get("members", [])
        if len(members) < 2:
            continue
        mids = [row_to_mid[m] for m in members if m in row_to_mid]
        if mids:
            out.append(mids)
            covered.update(members)
    for rid, mid in row_to_mid.items():
        if rid not in covered:
            out.append([mid])
    return out


def predict_exact_surface(mentions: list[dict]) -> list[list[str]]:
    """Incumbent baseline: cluster by identical normalized surface string."""
    groups: dict[str, list[str]] = defaultdict(list)
    for m in mentions:
        groups[norm(m["surface"])].append(m["mention_id"])
    return list(groups.values())


def _goldenmatch_config(threshold: float):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    # Co-mention overlap is THE discriminator: same entity -> overlap > 0; homograph
    # -> overlap exactly 0 (disjoint neighborhoods by construction). Surface is only
    # a light tie-breaker (candidates already share a surname via blocking); letting
    # surface drive the merge is exactly the exact-surface bug that merges homographs.
    surf_w = float(os.environ.get("CLEARKG_SURFACE_WEIGHT", "0.0"))
    fields = [MatchkeyField(field="neighbors", scorer="comention_jaccard", weight=1.0)]
    if surf_w > 0:
        # surface as a light tie-breaker; a positive weight gives homographs
        # (neighbors jaccard 0) a nonzero floor, so keep it 0 to guarantee splits.
        fields.append(MatchkeyField(field="surface", scorer="jaro_winkler", weight=surf_w))
    mk = MatchkeyConfig(
        name="mention", type="weighted", threshold=threshold, rerank=False, fields=fields,
    )
    return GoldenMatchConfig(
        matchkeys=[mk],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=[BLOCK_COL], transforms=[])]),
    )


def predict_goldenmatch(mentions: list[dict], *, threshold: float | None = None) -> list[list[str]]:
    """Principled ER: surface similarity + co-mention overlap, blocked on last
    token. Co-mention overlap keeps homographs apart and merges alias variants."""
    import goldenmatch as gm

    er_utils.register()
    if threshold is None:
        threshold = float(os.environ.get("CLEARKG_ER_THRESHOLD", "0.5"))
    df = build_mention_frame(mentions)
    result = gm.dedupe_df(df, config=_goldenmatch_config(threshold), confidence_required=False)
    return _clusters_to_mentions(result.clusters, df)


ENGINES = {
    "exact_surface": predict_exact_surface,
    "goldenmatch": predict_goldenmatch,
}
