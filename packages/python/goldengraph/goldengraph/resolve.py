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


def _key_payload(name: str, typ: str) -> dict:
    """The dict fed to `record_fingerprint` for cross-document reconciliation. Default is (name, typ) --
    but an open-vocab extractor assigns a DIFFERENT `typ` to one entity per document (measured: 97.6% of
    cross-doc fragmentation is type jitter -- 'schema matching' typed Process/Algorithm/method/... across
    docs), so its record_keys never overlap and the store never unifies it. `GOLDENGRAPH_XDOC_KEY` relaxes
    the key: `name` = name only (type-agnostic); `name_ci` = name, case-folded (also absorbs the ~case-only
    name jitter). Pure + goldenmatch-free so the normalization is unit-tested without the fingerprint."""
    import os

    mode = os.environ.get("GOLDENGRAPH_XDOC_KEY", "").strip().lower()
    if mode == "name":
        return {"name": name}
    if mode == "name_ci":
        return {"name": name.strip().lower()}
    return {"name": name, "typ": typ}


def _record_key(name: str, typ: str) -> str:
    import goldenmatch as gm

    # The fingerprint must be constructed identically on every call or cross-document reconciliation
    # drifts; `_key_payload` is the single chokepoint (all call sites route through it).
    return gm.record_fingerprint(_key_payload(name, typ))


def _build_entities(mentions: list[Mention], groups_idx: list[list[int]]) -> list[ResolvedEntity]:
    """Shared construction: groups of mention-indices -> ResolvedEntity list (longest-name rep,
    sorted-distinct surfaces + record_keys)."""
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


def _fuzzy_resolve(mentions: list[Mention], *, use_context: bool) -> list[ResolvedEntity]:
    """Resolve mentions into entities via goldenmatch's zero-config dedupe (the moat).

    Resolves on name+type, and on `context` (the per-mention description) when `use_context` AND any
    mention carries one -- the extra evidence sharpens resolution (the field that takes goldenmatch
    from name-only to its best on ER-KG-Bench). Falls back to name+type otherwise. At
    `use_context=True` this is byte-identical to the historical `resolve` behavior.
    """
    if not mentions:
        return []

    import goldenmatch as gm
    import polars as pl

    cols = {"name": [m.name for m in mentions], "type": [m.typ for m in mentions]}
    if use_context and any(m.context for m in mentions):
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
    return _build_entities(mentions, groups_idx)


def _exact_resolve(mentions: list[Mention]) -> list[ResolvedEntity]:
    """Group mentions by EXACT (name, typ) -- distinct surfaces never merge. Deterministic (no
    dedupe_df). record_keys via the SAME `_record_key` as the fuzzy path, so EXACT- and FUZZY-built
    stores reconcile across documents with identical fingerprints.
    """
    if not mentions:
        return []
    groups: dict[tuple[str, str], list[int]] = {}
    for i, m in enumerate(mentions):
        groups.setdefault((m.name, m.typ), []).append(i)
    return _build_entities(mentions, list(groups.values()))


def resolve(mentions: list[Mention]) -> list[ResolvedEntity]:
    """Backward-compatible default: fuzzy name+type+context (FUZZY_CONTEXT)."""
    return _fuzzy_resolve(mentions, use_context=True)
