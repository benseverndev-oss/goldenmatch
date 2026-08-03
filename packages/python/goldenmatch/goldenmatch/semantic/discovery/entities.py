"""Cross-table entity-type discovery (semantic-model discovery, Phase 2).

Which source tables describe the same real-world THING. `customers`, `app_users`, and
`crm_contacts` are three surfaces of one `Person` entity; `orders` and `line_items` are
transactions, not entities. Grouping tables into entity types is what lets the later
phases conform a shared key and propose one dimension per real entity instead of one per
table.

Two complementary signals decide it (spec Phase 2 — "resolved-entity overlap +
column-semantic signature"):

  * **Column-semantic signature (the cheap, deterministic backbone).** Each table's
    columns canonicalize to semantic tokens (`first_name`/`email`/`zip`/… via the shared
    `schema_match` synonym map, plus the profiled `col_type`). Two tables with a high
    Jaccard overlap of tokens describe the same kind of thing.
  * **Value overlap (the ER-flavored corroboration).** If two tables share an
    identifier/email-shaped column whose *values* substantially overlap, they realize the
    same population — a strong same-entity signal that also merges tables whose column
    names differ.

Tables are unioned into entity types by either signal; each type is named from its
dominant semantic hint (`person` / `organization` / …) or falls back to `entity_N`.

Advisory only — it proposes the entity typing; a human approves. Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenmatch.core.schema_match import _SYNONYM_MAP
from goldenmatch.semantic.discovery.joins import _distinct_nonnull
from goldenmatch.semantic.discovery.keys import KeyCandidate

# Two tables describe the same entity type when their semantic-token signatures overlap
# at least this much (Jaccard).
_DEFAULT_MIN_SIMILARITY = 0.5
# A shared identifier/email column whose values overlap at least this much is a
# same-population (same-entity) signal on its own.
_DEFAULT_MIN_VALUE_OVERLAP = 0.5
# The value-overlap signal only means "same entity" when the shared column is the
# IDENTITY of BOTH tables (each near-unique) — i.e. the same population enumerated
# twice. If it is near-unique on one side and repeats on the other, that's a
# foreign-key REFERENCE (a Phase-3 join), not sameness. This is the cardinality floor
# that separates the two.
_NEAR_UNIQUE_RATIO = 0.95

# Semantic tokens that name an entity type. First match (in order) wins.
_ENTITY_NAME_HINTS: list[tuple[str, frozenset[str]]] = [
    ("person", frozenset({"first_name", "last_name", "dob", "gender"})),
    ("organization", frozenset({"company"})),
]
# Column tokens/col_types whose VALUES carry cross-table identity (used for the
# value-overlap signal — a shared email/id links populations; a shared city does not).
_IDENTITY_TOKENS = frozenset({"email", "phone", "id"})
_IDENTITY_COL_TYPES = frozenset({"identifier", "email", "phone"})


@dataclass
class EntityType:
    """A real-world entity type realized by one or more source tables.

    `tables` are the surfaces of this entity; `signature` is the shared semantic-token
    backbone; `key_by_table` records the best certified key per table (when the caller
    supplied per-table keys) — the conformance anchor the later phases join on.
    """

    name: str
    tables: list[str]
    signature: list[str] = field(default_factory=list)
    key_by_table: dict[str, str] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def is_multi_table(self) -> bool:
        """True when the entity type is realized by more than one table — the case where
        conforming a shared key actually buys something."""
        return len(self.tables) > 1


def _canonical_token(name: str) -> str:
    return _SYNONYM_MAP.get(name.lower(), name.lower())


def _signature(table: Any) -> tuple[set[str], dict[str, str]]:
    """The table's semantic-token signature + a `{token: col_type}` map."""
    from goldenmatch.core.autoconfig import profile_columns
    from goldenmatch.core.frame import to_frame

    tokens: set[str] = set()
    token_types: dict[str, str] = {}
    try:
        col_types = {p.name: p.col_type for p in profile_columns(table)}
    except Exception:  # noqa: BLE001 - profiling is advisory
        col_types = {}
    try:
        cols = [c for c in to_frame(table).columns if not c.startswith("__")]
    except Exception:  # noqa: BLE001
        cols = list(col_types)
    for col in cols:
        tok = _canonical_token(col)
        tokens.add(tok)
        ct = col_types.get(col)
        if ct:
            token_types[tok] = ct
    return tokens, token_types


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _value_overlap(t1: Any, c1: str, t2: Any, c2: str) -> float:
    """Symmetric value overlap of two columns (min-normalized containment)."""
    v1, v2 = _distinct_nonnull(t1, c1), _distinct_nonnull(t2, c2)
    if not v1 or not v2:
        return 0.0
    return len(v1 & v2) / min(len(v1), len(v2))


def _is_near_unique(table: Any, column: str) -> bool:
    """True when a column is the identity of its table (distinct ≈ row count) — so a
    value overlap on it means same population, not a foreign-key reference."""
    from goldenmatch.core.frame import to_frame

    try:
        height = to_frame(table).height
    except Exception:  # noqa: BLE001
        return False
    if height == 0:
        return False
    return len(_distinct_nonnull(table, column)) / height >= _NEAR_UNIQUE_RATIO


def _identity_columns(table: Any, token_types: dict[str, str]) -> list[str]:
    """Columns whose VALUES carry cross-table identity (email/phone/id-shaped)."""
    from goldenmatch.core.frame import to_frame

    try:
        cols = [c for c in to_frame(table).columns if not c.startswith("__")]
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for col in cols:
        tok = _canonical_token(col)
        if tok in _IDENTITY_TOKENS or token_types.get(tok) in _IDENTITY_COL_TYPES:
            out.append(col)
    return out


def _name_for(signature: set[str]) -> str | None:
    for label, hints in _ENTITY_NAME_HINTS:
        if signature & hints:
            return label
    return None


def _best_key(entry: Any) -> str | None:
    """The best (trustworthy) single-column key from a per-table entry — the
    conformance anchor. Prefers a candidate carrying the `identifier` signal (a real
    id column) over an incidental unique field, then falls back to the top rank."""
    if entry is None:
        return None
    if isinstance(entry, KeyCandidate):
        entry = [entry]
    if isinstance(entry, str):
        return entry
    trustworthy = [
        item for item in entry
        if isinstance(item, KeyCandidate) and len(item.columns) == 1 and item.is_trustworthy
    ]
    for item in trustworthy:
        if "identifier" in item.signals:
            return item.columns[0]
    if trustworthy:
        return trustworthy[0].columns[0]
    for item in entry:
        if isinstance(item, str):
            return item
    return None


def discover_entity_types(
    tables: dict[str, Any],
    *,
    keys: dict[str, Any] | None = None,
    min_similarity: float = _DEFAULT_MIN_SIMILARITY,
    min_value_overlap: float = _DEFAULT_MIN_VALUE_OVERLAP,
) -> list[EntityType]:
    """Group source tables into the real-world entity types they realize.

    Args:
        tables: `{table_name: table}` — the source tables.
        keys: optional `{table_name: keys}` (the `discover_keys` output, a
            `KeyCandidate`, or a column name) — used to record the conformance key per
            table on each `EntityType`.
        min_similarity: Jaccard threshold on semantic-token signatures for two tables
            to be the same entity type.
        min_value_overlap: value-overlap threshold on a shared identity column for two
            tables to be the same entity type (the ER-flavored signal).

    Returns:
        `EntityType`s ranked multi-table-first (the ones where conforming a shared key
        matters), then by confidence, then name. A single isolated table still yields
        its own single-table entity type.
    """
    keys = keys or {}
    names = list(tables)
    sigs: dict[str, set[str]] = {}
    sig_types: dict[str, dict[str, str]] = {}
    for name in names:
        s, st = _signature(tables[name])
        sigs[name] = s
        sig_types[name] = st

    # Union-find over the two same-entity signals.
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    edge_signals: dict[frozenset[str], set[str]] = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            linked: set[str] = set()
            if _jaccard(sigs[a], sigs[b]) >= min_similarity:
                linked.add("signature")
            # value overlap on a shared identity column
            a_ids = _identity_columns(tables[a], sig_types[a])
            b_ids = _identity_columns(tables[b], sig_types[b])
            for ca in a_ids:
                if not _is_near_unique(tables[a], ca):
                    continue  # an FK, not this table's identity → a join, not sameness
                for cb in b_ids:
                    if not _is_near_unique(tables[b], cb):
                        continue
                    if _value_overlap(tables[a], ca, tables[b], cb) >= min_value_overlap:
                        linked.add("value_overlap")
                        break
                if "value_overlap" in linked:
                    break
            if linked:
                union(a, b)
                edge_signals[frozenset({a, b})] = linked

    # Assemble components into entity types.
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)

    entity_types: list[EntityType] = []
    used_names: set[str] = set()
    for idx, members in enumerate(sorted(groups.values(), key=lambda g: (-len(g), g))):
        members = sorted(members)
        shared = set.intersection(*(sigs[m] for m in members)) if members else set()
        signals: set[str] = set()
        for e in edge_signals:
            if e <= set(members):
                signals |= edge_signals[e]
        # confidence: mean pairwise signature Jaccard within the group (1.0 for a lone
        # table, which is trivially self-consistent), lifted when value-overlap corroborates.
        if len(members) == 1:
            conf = 1.0
        else:
            pair_scores = [
                _jaccard(sigs[a], sigs[b])
                for i, a in enumerate(members) for b in members[i + 1:]
            ]
            conf = sum(pair_scores) / len(pair_scores) if pair_scores else 0.0
            if "value_overlap" in signals:
                conf = min(1.0, conf + 0.2)
        label = _name_for(shared) or _name_for(set().union(*(sigs[m] for m in members)))
        if label is None or label in used_names:
            label = label or "entity"
            label = f"{label}_{idx + 1}" if label in used_names else label
        used_names.add(label)
        key_by_table = {}
        for m in members:
            k = _best_key(keys.get(m))
            if k is not None:
                key_by_table[m] = k
        entity_types.append(
            EntityType(
                name=label,
                tables=members,
                signature=sorted(shared),
                key_by_table=key_by_table,
                signals=sorted(signals),
                confidence=round(conf, 4),
            )
        )

    entity_types.sort(key=lambda e: (not e.is_multi_table, -e.confidence, e.name))
    return entity_types
