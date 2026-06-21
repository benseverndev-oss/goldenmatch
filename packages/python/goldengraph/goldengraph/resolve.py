"""Entity resolution over extracted mentions, reusing goldenmatch.

Mirrors goldenmatch-kg's `resolve_entities` (~40 lines): build a Polars frame of
(name, type), call goldenmatch's zero-config `dedupe_df` (the auto-config
controller — the moat), extract groups + singletons. Each group's members get a
`record_key` (goldenmatch's `:h1:` `record_fingerprint`) so the durable store can
reconcile the same surface form across documents.

goldenmatch + polars are imported LAZILY (inside `resolve`) so the package
imports without them — tests that inject a resolver need neither.
"""

from __future__ import annotations

from dataclasses import dataclass

from .extract import Mention


@dataclass
class ResolvedEntity:
    local_id: int
    canonical_name: str
    typ: str
    surface_names: list[str]
    record_keys: list[str]
    member_idx: list[int]  # indices into the extraction's mentions list


def _record_key(name: str, typ: str) -> str:
    import goldenmatch as gm

    # dict arg, fixed keys — the fingerprint must be constructed identically on
    # every call or cross-document reconciliation drifts.
    return gm.record_fingerprint({"name": name, "typ": typ})


def resolve(mentions: list[Mention]) -> list[ResolvedEntity]:
    """Resolve mentions into entities via goldenmatch's zero-config dedupe."""
    if not mentions:
        return []

    import goldenmatch as gm
    import polars as pl

    # Resolve on name+type, and on `context` (the per-mention description) when
    # any mention carries one -- the extra evidence sharpens resolution (the field
    # that takes goldenmatch from name-only to its best on ER-KG-Bench). Falls back
    # to name+type when extraction produced no descriptions (backward compatible).
    cols = {"name": [m.name for m in mentions], "type": [m.typ for m in mentions]}
    if any(m.context for m in mentions):
        cols["context"] = [m.context for m in mentions]
    df = pl.DataFrame(cols)
    result = gm.dedupe_df(df)

    n = len(mentions)
    groups_idx: list[list[int]] = []
    seen: set[int] = set()
    for info in result.clusters.values():
        members = [int(x) for x in info["members"]]
        if info.get("size", len(members)) > 1:
            groups_idx.append(members)
            seen.update(members)
    groups_idx.extend([i] for i in range(n) if i not in seen)  # singletons

    out: list[ResolvedEntity] = []
    for local_id, grp in enumerate(sorted(groups_idx, key=min)):
        rep = min(grp, key=lambda i: (-len(mentions[i].name), i))  # longest name
        out.append(
            ResolvedEntity(
                local_id=local_id,
                canonical_name=mentions[rep].name,
                typ=mentions[rep].typ,
                surface_names=sorted({mentions[i].name for i in grp}),
                record_keys=sorted({_record_key(mentions[i].name, mentions[i].typ) for i in grp}),
                member_idx=sorted(grp),
            )
        )
    return out
