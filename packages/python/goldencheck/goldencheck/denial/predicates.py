"""Column encoding + the bounded predicate space for denial-constraint discovery.

The evidence/discovery engine (later tasks) works on integer *ids*, never raw
values. This module turns a Polars frame into per-column :class:`EncodedColumn`
objects and enumerates the Stage-1 predicate space (const / single-tuple /
cross-tuple), honouring two load-bearing invariants:

* **Order preservation.** ``<`` / ``>`` on numeric/temporal columns must agree
  with the real values, so those columns get an order-preserving dense rank.
  Crucially the rank domain is *shared across all same-kind columns* -- a
  cross-column predicate ``t.A < t.B`` compares two columns, so their ranks must
  live in one order space (a per-column rank would make ``1`` and ``2`` both
  rank-1 and break the comparison). Categorical columns likewise share one
  first-seen id map so equal strings in different columns get equal ids
  (equality only; order is meaningless).
* **Null handling.** ``0`` is the null sentinel id. It is NOT "the smallest real
  value": any predicate whose operand is null on the relevant row is treated as
  NOT satisfied. :func:`predicate_holds` checks the null mask before it ever
  compares ids, so the sentinel is never used as an operand.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from goldencheck._polars_lazy import pl
from goldencheck.denial.constants import MAX_LITERAL_CARD, MAX_PREDICATES, MIN_SUPPORT
from goldencheck.denial.models import Op, Predicate

__all__ = [
    "EncodedColumn",
    "PredicateSpace",
    "encode_columns",
    "predicate_holds",
    "build_predicate_space",
]

_CAT_OPS: tuple[Op, ...] = (Op.EQ, Op.NE)
_ORD_OPS: tuple[Op, ...] = (Op.EQ, Op.NE, Op.LT, Op.LE, Op.GT, Op.GE)


@dataclass
class EncodedColumn:
    """One column encoded to dense integer ids (null -> 0).

    ``kind`` is one of ``"categorical"`` / ``"numeric"`` / ``"temporal"``.
    ``dtype`` is the concrete Polars dtype key (e.g. ``"Date"`` vs ``"Datetime"``):
    ids and pairing are shared only within one exact dtype, never merely within a
    ``kind`` bucket. ``ids`` are per-row ids; for numeric/temporal they are
    order-preserving ranks shared across all columns of the SAME dtype, for
    categorical they are shared first-seen ids (per dtype). ``card`` is this
    column's distinct non-null count. ``id_of_value`` maps each of this column's
    values to its id (for literals).
    """

    name: str
    kind: str
    dtype: str
    ids: list[int]
    nulls: list[bool]
    card: int
    id_of_value: dict


@dataclass
class PredicateSpace:
    predicates: list[Predicate]
    n_single: int
    n_cross: int
    pass2_effective: int
    capped: bool
    enc: dict[str, EncodedColumn] = field(default_factory=dict)


def _classify(dtype: pl.DataType) -> str | None:
    if dtype == pl.Boolean:
        return "categorical"
    if dtype.is_numeric():
        return "numeric"
    if dtype.is_temporal():
        return "temporal"
    if dtype in (pl.Utf8, pl.Categorical):
        return "categorical"
    return None


def encode_columns(df: pl.DataFrame) -> dict[str, EncodedColumn]:
    """Encode every supported column to dense ids; unsupported dtypes are omitted."""
    kinds: dict[str, str] = {}
    dtypes: dict[str, str] = {}
    values: dict[str, list] = {}
    for name in df.columns:
        dtype = df[name].dtype
        kind = _classify(dtype)
        if kind is None:
            continue
        kinds[name] = kind
        dtypes[name] = str(dtype)
        values[name] = df[name].to_list()

    # One shared id domain PER CONCRETE DTYPE (not per coarse kind): ordered ranks
    # for numeric/temporal so same-dtype cross-column comparisons are meaningful,
    # first-seen ids for categorical so equal values collapse. Keying on the exact
    # dtype keeps Date/Datetime (and Utf8/Boolean) in separate, incomparable
    # domains -- pooling them would crash sorted() or compare meaningless ranks.
    id_maps: dict[str, dict] = {}
    for name, kind in kinds.items():
        key = dtypes[name]
        if key in id_maps:
            continue
        members = [n for n, dk in dtypes.items() if dk == key]
        id_maps[key] = _build_domain(members, values, ordered=(kind != "categorical"))

    out: dict[str, EncodedColumn] = {}
    for name, kind in kinds.items():
        idmap = id_maps[dtypes[name]]
        vals = values[name]
        ids = [0 if v is None else idmap[v] for v in vals]
        nulls = [v is None for v in vals]
        distinct = {v for v in vals if v is not None}
        out[name] = EncodedColumn(
            name=name,
            kind=kind,
            dtype=dtypes[name],
            ids=ids,
            nulls=nulls,
            card=len(distinct),
            id_of_value={v: idmap[v] for v in distinct},
        )
    return out


def _build_domain(members: list[str], values: dict[str, list], ordered: bool) -> dict:
    """Shared value->id map for one concrete dtype's columns.

    ``ordered`` -> order-preserving dense rank over the sorted distinct values;
    otherwise first-seen ids in (column, row) order. null is never a member.
    """
    if ordered:
        pool: set = set()
        for name in members:
            pool.update(v for v in values[name] if v is not None)
        return {v: i + 1 for i, v in enumerate(sorted(pool))}
    mapping: dict = {}
    nxt = 1
    for name in members:
        for v in values[name]:
            if v is None:
                continue
            if v not in mapping:
                mapping[v] = nxt
                nxt += 1
    return mapping


def _cmp(a: int, op: Op, b: int) -> bool:
    if op is Op.EQ:
        return a == b
    if op is Op.NE:
        return a != b
    if op is Op.LT:
        return a < b
    if op is Op.LE:
        return a <= b
    if op is Op.GT:
        return a > b
    return a >= b  # Op.GE


def predicate_holds(
    p: Predicate, enc: dict[str, EncodedColumn], row_a: int, row_b: int | None
) -> bool:
    """Evaluate one predicate. A null operand on the relevant row -> NOT satisfied."""
    ea = enc[p.col_a]
    if ea.nulls[row_a]:
        return False

    if p.kind == "const":
        lit_id = ea.id_of_value.get(p.literal)
        if lit_id is None:  # literal value never appears -> nothing equals it
            return False
        return _cmp(ea.ids[row_a], p.op, lit_id)

    eb = enc[p.col_b]  # type: ignore[index]
    if p.kind == "single":
        if eb.nulls[row_a]:
            return False
        return _cmp(ea.ids[row_a], p.op, eb.ids[row_a])

    # cross: tα.A op tβ.B
    rb = row_a if row_b is None else row_b
    if eb.nulls[rb]:
        return False
    return _cmp(ea.ids[row_a], p.op, eb.ids[rb])


def _ops_for(kind: str) -> tuple[Op, ...]:
    return _CAT_OPS if kind == "categorical" else _ORD_OPS


def _comparison_support(
    ea: EncodedColumn, eb: EncodedColumn, op: Op, n_rows: int
) -> float:
    if n_rows == 0:
        return 0.0
    hits = 0
    a_ids, a_nulls = ea.ids, ea.nulls
    b_ids, b_nulls = eb.ids, eb.nulls
    for i in range(n_rows):
        if a_nulls[i] or b_nulls[i]:
            continue
        if _cmp(a_ids[i], op, b_ids[i]):
            hits += 1
    return hits / n_rows


def build_predicate_space(df: pl.DataFrame) -> PredicateSpace:
    """Enumerate the bounded Stage-1 predicate space over ``df``.

    Predicates: const ``t.A = c`` (low-card columns, frequent values only),
    single-tuple ``t.A op t.B`` and cross-tuple ``tα.A op tβ.B`` over
    type-compatible column pairs. Trims by descending support until both the
    Pass-1 (``n_single``) and Pass-2 (``2*n_single + n_cross``) mask budgets fit
    in :data:`MAX_PREDICATES`, flagging ``capped``.
    """
    enc = encode_columns(df)
    names = [n for n in df.columns if n in enc]
    n_rows = df.height

    const: list[Predicate] = []
    const_support: dict[Predicate, float] = {}
    for name in names:
        ec = enc[name]
        if ec.card == 0 or ec.card > MAX_LITERAL_CARD:
            continue
        n_nonnull = sum(1 for v in ec.nulls if not v)
        if not n_nonnull:
            continue
        counts: dict = {}
        for i, is_null in enumerate(ec.nulls):
            if is_null:
                continue
            counts[ec.ids[i]] = counts.get(ec.ids[i], 0) + 1
        id_to_value = {i: v for v, i in ec.id_of_value.items()}
        for vid, cnt in counts.items():
            support = cnt / n_nonnull
            if support < MIN_SUPPORT:
                continue
            p = Predicate(
                kind="const", col_a=name, op=Op.EQ, col_b=None, literal=id_to_value[vid]
            )
            const.append(p)
            const_support[p] = support

    single: list[Predicate] = []
    cross: list[Predicate] = []
    for i, a in enumerate(names):
        # A == B cross-column (same column, two tuples)
        for op in _ops_for(enc[a].kind):
            cross.append(
                Predicate(kind="cross", col_a=a, op=op, col_b=a, literal=None)
            )
        for b in names[i + 1:]:
            if enc[a].dtype != enc[b].dtype:
                continue  # type-incompatible: only EXACT same dtype may pair
            for op in _ops_for(enc[a].kind):
                single.append(
                    Predicate(kind="single", col_a=a, op=op, col_b=b, literal=None)
                )
                cross.append(
                    Predicate(kind="cross", col_a=a, op=op, col_b=b, literal=None)
                )

    predicates = const + single + cross
    n_single = len(const) + len(single)
    n_cross = len(cross)
    pass2 = 2 * n_single + n_cross

    if n_single <= MAX_PREDICATES and pass2 <= MAX_PREDICATES:
        return PredicateSpace(
            predicates=predicates,
            n_single=n_single,
            n_cross=n_cross,
            pass2_effective=pass2,
            capped=False,
            enc=enc,
        )

    # Over budget: keep highest-support predicates first until BOTH passes fit.
    def _score(p: Predicate) -> float:
        if p.kind == "const":
            return const_support[p]
        return _comparison_support(enc[p.col_a], enc[p.col_b], p.op, n_rows)

    ranked = sorted(predicates, key=_score, reverse=True)  # stable on ties
    kept: list[Predicate] = []
    ns = nc = 0
    for p in ranked:
        is_single = p.kind in ("const", "single")
        cand_ns = ns + (1 if is_single else 0)
        cand_nc = nc + (0 if is_single else 1)
        if cand_ns <= MAX_PREDICATES and 2 * cand_ns + cand_nc <= MAX_PREDICATES:
            kept.append(p)
            ns, nc = cand_ns, cand_nc

    return PredicateSpace(
        predicates=kept,
        n_single=ns,
        n_cross=nc,
        pass2_effective=2 * ns + nc,
        capped=True,
        enc=enc,
    )
