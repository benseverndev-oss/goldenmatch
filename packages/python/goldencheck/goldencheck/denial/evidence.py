"""Pure-Python evidence-set builder for denial-constraint discovery.

This is the correctness / parity ORACLE. A Rust kernel replaces it for speed in
a later task and MUST reproduce these masks byte-for-byte, so the exact bit
layout below is load-bearing.

Given a :class:`~goldencheck.denial.predicates.PredicateSpace`, split its
predicates (in list order) into ``singles`` (kind in ``{"const", "single"}``,
count ``s``) and ``crosses`` (kind ``"cross"``, count ``c``). Two passes build
integer *satisfaction masks* (one u64 each) counted into ``mask -> count``
histograms:

* **Pass 1 -- row-level mask** (one u64 per row): bit ``i`` (``0 <= i < s``) is
  set iff ``singles[i]`` holds on that row (``predicate_holds(singles[i], enc,
  row, None)``). Cross predicates do NOT participate in Pass 1.
* **Pass 2 -- pairwise mask** (one u64 per ORDERED pair ``(alpha, beta)``,
  ``alpha != beta``):

  * bit ``i`` (``0 <= i < s``)      = ``singles[i]`` holds on **alpha**
    (``predicate_holds(singles[i], enc, alpha, None)``)
  * bit ``s + i``                   = ``singles[i]`` holds on **beta**
    (``predicate_holds(singles[i], enc, beta, None)``)
  * bit ``2s + j`` (``0 <= j < c``) = ``crosses[j]`` holds on the pair
    (``predicate_holds(crosses[j], enc, alpha, beta)``)

* **Both orderings:** Pass 2 iterates ALL ordered pairs ``(alpha, beta)`` with
  ``alpha != beta`` over the sample index set -- this covers both
  ``(alpha, beta)`` and ``(beta, alpha)``, which is required so reversed
  cross-column predicates are reachable. For a sample of size ``m`` that is
  ``m * (m - 1)`` ordered pairs.

Intentionally O(n) / O(m^2) pure Python: correctness over speed.
"""
from __future__ import annotations

from goldencheck.core import kernels
from goldencheck.denial.models import Op
from goldencheck.denial.predicates import PredicateSpace, predicate_holds

__all__ = ["row_evidence", "pair_evidence", "space_to_kernel_args"]

# Predicate ``kind`` string -> kernel kind code (const/single -> singles, cross).
_KIND_CODE: dict[str, int] = {"const": 0, "single": 1, "cross": 2}
# ``Op`` -> kernel op code (must match dc.rs::cmp and _cmp below).
_OP_CODE: dict[Op, int] = {
    Op.EQ: 0,
    Op.NE: 1,
    Op.LT: 2,
    Op.LE: 3,
    Op.GT: 4,
    Op.GE: 5,
}
# Literal-absent sentinel: real value ids start at 1 (0 is the null sentinel),
# so a const predicate against id 0 can never be EQ-true on a non-null row --
# exactly the "literal value never appears -> nothing equals it" behaviour of
# ``predicate_holds`` (which returns False when ``id_of_value.get(literal)`` is
# None). Using 0 makes the kernel and the pure-Python fallback agree on absent
# literals without a separate presence flag.
_ABSENT: int = 0


def _split(space: PredicateSpace):
    singles = [p for p in space.predicates if p.kind in ("const", "single")]
    crosses = [p for p in space.predicates if p.kind == "cross"]
    assert len(singles) == space.n_single
    assert len(crosses) == space.n_cross
    return singles, crosses


def space_to_kernel_args(space: PredicateSpace):
    """Flatten a :class:`PredicateSpace` into the native kernel's plain-list form.

    Returns ``(cols, nulls, pred_spec, col_index)`` where:
      * ``col_index`` maps each referenced column NAME -> a dense index,
      * ``cols[k]`` / ``nulls[k]`` are the id / null-mask vectors for the column
        at index ``k`` (``EncodedColumn.ids`` / ``.nulls``),
      * ``pred_spec`` is one ``(kind_code, col_a, op_code, col_b, literal_id)``
        tuple per predicate, in the SAME order as ``space.predicates`` (so the
        kernel's kind 0/1 -> singles, kind 2 -> crosses split matches ``_split``).

    ``literal_id`` for a const predicate is the interned id of its literal
    (``_ABSENT`` == 0 if the literal never appears in the column); for
    single/cross predicates it is 0 (unused).
    """
    enc = space.enc
    order = list(enc.keys())
    col_index = {name: i for i, name in enumerate(order)}
    cols = [enc[name].ids for name in order]
    nulls = [enc[name].nulls for name in order]

    pred_spec: list[tuple[int, int, int, int, int]] = []
    for p in space.predicates:
        kind_code = _KIND_CODE[p.kind]
        op_code = _OP_CODE[p.op]
        col_a = col_index[p.col_a]
        if p.kind == "const":
            literal_id = enc[p.col_a].id_of_value.get(p.literal, _ABSENT)
            pred_spec.append((kind_code, col_a, op_code, 0, literal_id))
        else:
            col_b = col_index[p.col_b]
            pred_spec.append((kind_code, col_a, op_code, col_b, 0))
    return cols, nulls, pred_spec, col_index


def _cmp(op: int, x: int, y: int) -> bool:
    if op == 0:
        return x == y
    if op == 1:
        return x != y
    if op == 2:
        return x < y
    if op == 3:
        return x <= y
    if op == 4:
        return x > y
    return x >= y  # 5 = GE


def _holds_single_tuple(spec, cols, nulls, r: int) -> bool:
    kind, col_a, op, col_b, literal = spec
    if nulls[col_a][r]:
        return False
    if kind == 0:  # const: t.A op literal
        return _cmp(op, cols[col_a][r], literal)
    # single: t.A op t.B (same row)
    if nulls[col_b][r]:
        return False
    return _cmp(op, cols[col_a][r], cols[col_b][r])


def _holds_cross(spec, cols, nulls, a: int, b: int) -> bool:
    _kind, col_a, op, col_b, _literal = spec
    if nulls[col_a][a] or nulls[col_b][b]:
        return False
    return _cmp(op, cols[col_a][a], cols[col_b][b])


def _evidence_python(cols, nulls, pred_spec, which_pass, n, sample_idx) -> dict[int, int]:
    """Cols-based pure-Python evidence map -- the kernel's byte-exact fallback.

    Direct port of ``dc.rs`` over interned id vectors: split ``pred_spec`` into
    singles (kind 0/1) + crosses (kind 2) preserving order, then build the same
    bit-layout masks as :func:`row_evidence` / :func:`pair_evidence`. This is
    what ``kernels.denial_constraint_evidence`` calls when native is off.
    """
    singles = [spec for spec in pred_spec if spec[0] != 2]
    crosses = [spec for spec in pred_spec if spec[0] == 2]
    hist: dict[int, int] = {}
    if which_pass == 1:
        for r in range(n):
            mask = 0
            for i, spec in enumerate(singles):
                if _holds_single_tuple(spec, cols, nulls, r):
                    mask |= 1 << i
            hist[mask] = hist.get(mask, 0) + 1
        return hist
    s = len(singles)
    for alpha in sample_idx:
        alpha_mask = 0
        for i, spec in enumerate(singles):
            if _holds_single_tuple(spec, cols, nulls, alpha):
                alpha_mask |= 1 << i
        for beta in sample_idx:
            if alpha == beta:
                continue
            mask = alpha_mask
            for i, spec in enumerate(singles):
                if _holds_single_tuple(spec, cols, nulls, beta):
                    mask |= 1 << (s + i)
            for j, spec in enumerate(crosses):
                if _holds_cross(spec, cols, nulls, alpha, beta):
                    mask |= 1 << (2 * s + j)
            hist[mask] = hist.get(mask, 0) + 1
    return hist


def row_evidence(space: PredicateSpace, n: int) -> dict[int, int]:
    """Pass 1: mask -> row-count over the ``n`` rows (native-gated).

    Bit ``i`` = ``singles[i]`` holds on the row. Routes through
    ``kernels.denial_constraint_evidence`` (native kernel or the cols-based
    :func:`_evidence_python` fallback); :func:`_row_evidence_oracle` is the
    independent ``predicate_holds`` reference the parity tests compare against.
    """
    cols, nulls, pred_spec, _ = space_to_kernel_args(space)
    return kernels.denial_constraint_evidence(cols, nulls, pred_spec, 1, n, [])


def pair_evidence(space: PredicateSpace, sample_idx: list[int]) -> dict[int, int]:
    """Pass 2: mask -> pair-count over all ordered pairs ``(alpha, beta)``,
    ``alpha != beta``, ``alpha, beta in sample_idx`` (native-gated).

    Bit layout: ``[0..s)`` singles-on-alpha, ``[s..2s)`` singles-on-beta,
    ``[2s..2s+c)`` crosses on ``(alpha, beta)``. Routes through
    ``kernels.denial_constraint_evidence``; :func:`_pair_evidence_oracle` is the
    independent reference.
    """
    cols, nulls, pred_spec, _ = space_to_kernel_args(space)
    return kernels.denial_constraint_evidence(cols, nulls, pred_spec, 2, 0, list(sample_idx))


def _row_evidence_oracle(space: PredicateSpace, n: int) -> dict[int, int]:
    """Independent ``predicate_holds``-based Pass-1 oracle (parity reference)."""
    singles, _ = _split(space)
    enc = space.enc
    hist: dict[int, int] = {}
    for row in range(n):
        mask = 0
        for i, p in enumerate(singles):
            if predicate_holds(p, enc, row, None):
                mask |= 1 << i
        hist[mask] = hist.get(mask, 0) + 1
    return hist


def _pair_evidence_oracle(space: PredicateSpace, sample_idx: list[int]) -> dict[int, int]:
    """Independent ``predicate_holds``-based Pass-2 oracle (parity reference)."""
    singles, crosses = _split(space)
    enc = space.enc
    s = len(singles)
    hist: dict[int, int] = {}
    for alpha in sample_idx:
        # singles-on-alpha bits are constant across all beta for this alpha.
        alpha_mask = 0
        for i, p in enumerate(singles):
            if predicate_holds(p, enc, alpha, None):
                alpha_mask |= 1 << i
        for beta in sample_idx:
            if alpha == beta:
                continue
            mask = alpha_mask
            for i, p in enumerate(singles):
                if predicate_holds(p, enc, beta, None):
                    mask |= 1 << (s + i)
            for j, p in enumerate(crosses):
                if predicate_holds(p, enc, alpha, beta):
                    mask |= 1 << (2 * s + j)
            hist[mask] = hist.get(mask, 0) + 1
    return hist
