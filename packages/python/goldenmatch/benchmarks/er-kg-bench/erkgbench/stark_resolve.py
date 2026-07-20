"""SP-moat resolver: cluster injected alias nodes. `none`/`exact` are pure; `er`
routes through goldenmatch's zero-config dedupe (the suite's real ER engine) over
the alias names -- the same call ``ingest.py::_gm_cluster`` uses. goldenmatch +
polars import lazily so the pure paths need neither.
"""
from __future__ import annotations

from collections import defaultdict


def resolve_aliases(alias_nodes, method: str):
    """`alias_nodes`: [(alias_id, name)] (injected set only). Returns clusters:
    list[list[alias_id]]. method in {none, exact, er}."""
    ids = [a for a, _ in alias_nodes]
    if method == "none":
        return [[i] for i in ids]
    if method == "exact":
        groups: dict[str, list[str]] = defaultdict(list)
        for aid, name in alias_nodes:
            groups[name.lower().strip()].append(aid)
        return list(groups.values())
    if method == "er":
        import goldenmatch as gm
        import pyarrow as pa

        if not alias_nodes:
            return []
        df = pa.table({"name": [name for _, name in alias_nodes]})
        result = gm.dedupe_df(df)
        seen: set[int] = set()
        clusters: list[list[str]] = []
        for info in result.clusters.values():
            members = [int(x) for x in info["members"]]
            seen.update(members)
            clusters.append([ids[m] for m in members])
        # dedupe_df only returns multi-member clusters; singletons are the rest
        for i, aid in enumerate(ids):
            if i not in seen:
                clusters.append([aid])
        return clusters
    raise ValueError(f"unknown method {method!r} (none|exact|er)")
