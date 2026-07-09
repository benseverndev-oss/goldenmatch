"""End-to-end denial-constraint discovery orchestrator + Finding-emitting profiler.

Ties the Stage-1 pieces (``predicates`` -> ``evidence`` -> ``discover`` ->
``validate``) into a single ``discover_denial_constraints(df)`` call and a
``DenialConstraintProfiler`` that turns the discovered DCs into
:class:`~goldencheck.models.finding.Finding` objects.

Two discovery passes, mirroring the two evidence passes:

* **Pass 1 (single-tuple).** Row-level evidence -> minimal single-tuple DCs
  ``¬(p1 ∧ … ∧ pm)`` where every predicate references one row. Each candidate is
  re-validated EXACTLY on the full frame (O(n)); the exact violating row indices
  are kept for the eventual Finding.
* **Pass 2 (cross-tuple).** Pairwise evidence over a seeded row sample -> DCs that
  need a cross predicate ``tα.A op tβ.B``. Re-validated on a bounded, seeded
  sample (O(m^2)); the g1 is an ESTIMATE and a few representative violating pairs
  are kept.

**Bit layout / the β-slot projection (load-bearing).** ``evidence.pair_evidence``
lays each pairwise mask out as ``[0..s)`` = singles on α, ``[s..2s)`` = singles on
β, ``[2s..2s+c)`` = crosses on ``(α, β)`` (``s = space.n_single``,
``c = space.n_cross``). Before Pass-2 discovery we PROJECT OUT the β-slot bits
``[s..2s)`` and pack the cross bits down into ``[s..s+c)``::

    reduced = (mask & ((1 << s) - 1)) | (((mask >> (2 * s)) & ((1 << c) - 1)) << s)

so the reduced predicate space is ``singles (on α) ++ crosses`` with
``n_predicates = s + c``. Dropping the β-slots is a *canonicalization*, not a
loss: a DC that would need a predicate on tβ is captured as its α-mirror under
the swapped pair ordering, and ``pair_evidence`` already iterates BOTH orderings
``(α, β)`` and ``(β, α)``. Full β-slot DCs (and cross-table DCs) are a Stage 2+
concern.

**Stage-1 reportability gates (all configurable via keyword args).**

* **Order comparison (default on).** With ``require_order_comparison=True``
  (the default) only DCs containing >= 1 order predicate (``<``, ``≤``, ``>``,
  ``≥``) are reported. Pure all-equality DCs (``¬(A = x ∧ B = y)``, ``¬(A = B)``)
  are suppressed by default -- they are the accepted-values / uniqueness / FD
  family, better served by goldencheck's existing profilers, and mining them
  per-literal is noisy. Set ``require_order_comparison=False`` to include them
  (expect more, noisier output).
* **Arity (default 2).** ``arity_bound`` (default
  :data:`~goldencheck.denial.constants.MAX_REPORT_ARITY` = 2) caps predicates per
  DC. Binary DCs carry the signal; wider ones over-fit on independent data and
  are costly to enumerate. A power user can opt into 3+.
* **Self-column cross predicates dropped.** Cross predicates of the form
  ``tα.A op tβ.A`` (same column on both tuples, incl. uniqueness ``¬(tα.A = tβ.A)``)
  are dropped in Stage 1 -- already covered by the ``uniqueness`` /
  ``composite_key`` profilers; full cross-tuple uniqueness is Stage 2+. Genuine
  relational cross DCs relate DISTINCT columns.
* **Tautologies dropped.** Antecedents that can never hold (contradictory sign
  sets on one column pair, or ``A = x ∧ A = y``) are not surfaced as invariants.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from goldencheck._polars_lazy import pl
from goldencheck.denial.constants import (
    DEFAULT_EPS,
    DEFAULT_SAMPLE,
    MAX_CONSTRAINTS,
    MAX_REPORT_ARITY,
    MIN_ROWS,
    VALIDATION_SAMPLE,
)
from goldencheck.denial.discover import discover
from goldencheck.denial.evidence import pair_evidence, row_evidence
from goldencheck.denial.models import DenialConstraint, Predicate
from goldencheck.denial.predicates import build_predicate_space
from goldencheck.denial.validate import validate_cross_tuple, validate_single_tuple
from goldencheck.models.finding import Finding, Severity

__all__ = ["discover_denial_constraints", "DenialConstraintProfiler"]

# A predicate whose op is an order comparison (not pure equality). DCs built
# ONLY from equality predicates over low-card columns are overwhelmingly
# coincidental rare co-occurrences on independent data (the FP-guard case); the
# high-value invariants ("order is never after ship") always carry an order
# comparison. We require at least one ordered predicate to report a DC.
_ORDER_OPS = {"<", "≤", ">", "≥"}

# The sign of ``a - b`` each operator admits (-1: a<b, 0: a=b, +1: a>b). Used to
# reject tautological antecedents: two predicates on the same column pair whose
# sign sets don't intersect can never both hold, so ¬(…) trivially "always holds"
# and is noise, not a discovered invariant.
_SIGN: dict[str, frozenset[int]] = {
    "<": frozenset({-1}),
    "≤": frozenset({-1, 0}),
    ">": frozenset({1}),
    "≥": frozenset({1, 0}),
    "=": frozenset({0}),
    "≠": frozenset({-1, 1}),
}


@dataclass
class _Record:
    """A discovered DC plus the concrete evidence needed to render a Finding."""

    dc: DenialConstraint
    examples: list  # violating row indices (single) or (α, β) pairs (cross)
    n_violations: int  # exact for single, estimated for cross


def _split(space) -> tuple[list[Predicate], list[Predicate]]:
    singles = [p for p in space.predicates if p.kind in ("const", "single")]
    crosses = [p for p in space.predicates if p.kind == "cross"]
    return singles, crosses


def _preds_from_mask(mask: int, preds: list[Predicate]) -> list[Predicate]:
    return [p for i, p in enumerate(preds) if (mask >> i) & 1]


def _has_order_pred(preds: list[Predicate]) -> bool:
    return any(p.op.value in _ORDER_OPS for p in preds)


def _n_distinct_columns(preds: list[Predicate]) -> int:
    cols: set[str] = set()
    for p in preds:
        cols.add(p.col_a)
        if p.col_b is not None:
            cols.add(p.col_b)
    return len(cols)


def _is_tautological(preds: list[Predicate]) -> bool:
    """True iff the antecedent ``p1 ∧ … ∧ pm`` is logically unsatisfiable.

    Groups comparison predicates by their (orientation-normalised) column pair and
    intersects the ``a - b`` sign sets; an empty intersection means the conjunction
    can never hold. Also catches ``A = x ∧ A = y`` (distinct literals). Such DCs
    "always hold" for a trivial reason and must not be surfaced as invariants.
    """
    sign_groups: dict[tuple[str, str], frozenset[int]] = {}
    const_lits: dict[str, set] = {}
    for p in preds:
        if p.kind == "const":
            const_lits.setdefault(p.col_a, set()).add(p.literal)
            continue
        a, b, sign = p.col_a, p.col_b, _SIGN[p.op.value]
        if a > b:  # normalise orientation so (A,B) and (B,A) share a group
            a, b = b, a
            sign = frozenset(-x for x in sign)
        prev = sign_groups.get((a, b))
        sign_groups[(a, b)] = sign if prev is None else (prev & sign)
    if any(not s for s in sign_groups.values()):
        return True
    return any(len(lits) > 1 for lits in const_lits.values())


def _is_reportable(preds: list[Predicate], *, require_order_comparison: bool) -> bool:
    """Stage-1 interestingness gate shared by both passes: at least two distinct
    columns, a satisfiable antecedent, and (when ``require_order_comparison``) at
    least one order comparison."""
    if _n_distinct_columns(preds) < 2 or _is_tautological(preds):
        return False
    return _has_order_pred(preds) if require_order_comparison else True


# Negated operator, for rendering the "then" clause of an if-then message: a DC
# ¬(A ∧ B) reads "if A then ¬B", so the consequent predicate is shown negated.
_NEG_OP = {"=": "≠", "≠": "=", "<": "≥", "≤": ">", ">": "≤", "≥": "<"}


def _render_negated(p: Predicate) -> str:
    """Render ``p`` with its operator negated (the ``then`` clause of ¬(A ∧ p))."""
    op = _NEG_OP[p.op.value]
    if p.kind == "const":
        lit = f"'{p.literal}'" if isinstance(p.literal, str) else repr(p.literal)
        return f"{p.col_a} {op} {lit}"
    return f"{p.col_a} {op} {p.col_b}"


def _friendly_if_then(dc: DenialConstraint) -> str | None:
    """Return an "if A then not-B" rendering for the clean 2-predicate single-tuple
    shape (exactly one equality antecedent + one consequent), else ``None``.

    ``¬(status = 'shipped' ∧ order ≥ ship)`` -> "If status = 'shipped' then
    order < ship". Message-only sugar; ``DenialConstraint.render()`` stays
    canonical ``¬(…)``.
    """
    if dc.tuple_scope != "single" or len(dc.predicates) != 2:
        return None
    eqs = [p for p in dc.predicates if p.op.value == "="]
    if len(eqs) != 1:
        return None
    ante = eqs[0]
    cons = dc.predicates[1] if dc.predicates[0] is ante else dc.predicates[0]
    return f"If {ante.render()} then {_render_negated(cons)}"


def _rank_key(rec: _Record):
    """Interestingness order: strict/low-g1 first, then fewer predicates, then a
    deterministic textual tie-break so equal-quality DCs sort stably."""
    dc = rec.dc
    return (dc.g1, len(dc.predicates), dc.render())


def _discover_records(
    df: pl.DataFrame,
    *,
    eps: float,
    sample_size: int,
    max_constraints: int,
    seed: int,
    arity_bound: int,
    require_order_comparison: bool,
) -> list[_Record]:
    """Run both passes and return ranked ``_Record`` s (DC + violating evidence)."""
    if df.height < MIN_ROWS:
        return []
    space = build_predicate_space(df)
    if not space.predicates:
        return []

    singles, crosses = _split(space)
    s = space.n_single
    c = space.n_cross
    n = df.height
    records: list[_Record] = []
    seen: set[tuple[str, ...]] = set()  # dedupe identical predicate conjunctions

    # -- Pass 1: single-tuple DCs ------------------------------------------------
    ev1 = row_evidence(space, n)
    for mask in discover(ev1, s, n, eps, arity_bound=arity_bound):
        preds = _preds_from_mask(mask, singles)
        if not preds or not _is_reportable(
            preds, require_order_comparison=require_order_comparison
        ):
            continue
        g1, rows = validate_single_tuple(preds, df)
        if g1 > eps:
            continue
        key = tuple(sorted(p.render() for p in preds))
        if key in seen:
            continue
        seen.add(key)
        dc = DenialConstraint(
            predicates=tuple(preds),
            g1=g1,
            support=n,
            tuple_scope="single",
            exact=True,
        )
        records.append(_Record(dc=dc, examples=rows, n_violations=len(rows)))

    # -- Pass 2: cross-tuple DCs -------------------------------------------------
    if c:
        m = min(n, sample_size)
        rng = random.Random(seed)
        sample_idx = sorted(rng.sample(range(n), m)) if n > m else list(range(n))
        ev2 = pair_evidence(space, sample_idx)

        # Project out the β-slots [s..2s); pack crosses down into [s..s+c).
        low = (1 << s) - 1
        cross_mask = (1 << c) - 1
        reduced_ev: dict[int, int] = {}
        for mask, cnt in ev2.items():
            reduced = (mask & low) | (((mask >> (2 * s)) & cross_mask) << s)
            reduced_ev[reduced] = reduced_ev.get(reduced, 0) + cnt

        total_pairs = m * (m - 1)
        for mask in discover(reduced_ev, s + c, total_pairs, eps, arity_bound=arity_bound):
            preds: list[Predicate] = []
            for bit in range(s + c):
                if not (mask >> bit) & 1:
                    continue
                preds.append(singles[bit] if bit < s else crosses[bit - s])
            cross_preds = [p for p in preds if p.kind == "cross"]
            if not cross_preds:
                continue  # a pure single-tuple DC belongs to Pass 1
            # Self-column cross predicates (tα.A op tβ.A) encode intra-column
            # ordering/uniqueness and spawn degenerate multi-predicate DCs;
            # genuine relational cross DCs relate DISTINCT columns. Uniqueness
            # (¬(tα.A = tβ.A)) is a Stage-2+ concern.
            if any(p.col_a == p.col_b for p in cross_preds):
                continue
            if not _is_reportable(
                preds, require_order_comparison=require_order_comparison
            ):
                continue
            key = tuple(sorted(p.render() for p in preds))
            if key in seen:
                continue
            g1_est, pairs = validate_cross_tuple(
                preds, df, sample=VALIDATION_SAMPLE, seed=seed
            )
            if g1_est > eps:
                continue
            seen.add(key)
            m_val = min(n, VALIDATION_SAMPLE)
            denom = m_val * (m_val - 1)
            dc = DenialConstraint(
                predicates=tuple(preds),
                g1=g1_est,
                support=m,
                tuple_scope="cross",
                exact=False,
            )
            records.append(
                _Record(
                    dc=dc,
                    examples=pairs,
                    n_violations=int(round(g1_est * denom)),
                )
            )

    records.sort(key=_rank_key)
    return records[:max_constraints]


def discover_denial_constraints(
    df: pl.DataFrame,
    *,
    min_confidence: float | None = None,
    sample_size: int | None = None,
    max_constraints: int | None = None,
    seed: int = 0,
    arity_bound: int = MAX_REPORT_ARITY,
    require_order_comparison: bool = True,
) -> list[DenialConstraint]:
    """Discover denial constraints ``¬(p1 ∧ … ∧ pm)`` holding on ``df``.

    ``min_confidence`` is the fraction of elements a DC must hold for; the g1
    threshold is ``eps = 1 - min_confidence`` (default ``DEFAULT_EPS``).
    ``sample_size`` bounds the pairwise (cross-tuple) pass; ``max_constraints``
    caps the ranked output. ``arity_bound`` caps predicates per DC (default
    :data:`~goldencheck.denial.constants.MAX_REPORT_ARITY` = 2; raise to opt into
    wider DCs). ``require_order_comparison`` (default True) suppresses pure
    all-equality DCs -- see the module docstring. Returns the DCs only -- use the
    profiler for the per-DC violating rows/pairs.
    """
    eps = DEFAULT_EPS if min_confidence is None else 1.0 - min_confidence
    sample = DEFAULT_SAMPLE if sample_size is None else sample_size
    cap = MAX_CONSTRAINTS if max_constraints is None else max_constraints
    records = _discover_records(
        df,
        eps=eps,
        sample_size=sample,
        max_constraints=cap,
        seed=seed,
        arity_bound=arity_bound,
        require_order_comparison=require_order_comparison,
    )
    return [r.dc for r in records]


class DenialConstraintProfiler:
    """Dataset-level profiler: emits a :class:`Finding` per discovered DC."""

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        records = _discover_records(
            df,
            eps=DEFAULT_EPS,
            sample_size=DEFAULT_SAMPLE,
            max_constraints=MAX_CONSTRAINTS,
            seed=0,
            arity_bound=MAX_REPORT_ARITY,
            require_order_comparison=True,
        )
        findings: list[Finding] = []
        for rec in records:
            findings.append(self._finding(rec, df))
        return findings

    @staticmethod
    def _finding(rec: _Record, df: pl.DataFrame) -> Finding:
        dc = rec.dc
        single = dc.tuple_scope == "single"
        unit = "row" if single else "pair"
        columns = ",".join(dc.columns())
        metadata = {
            "technique": "denial_constraint",
            "predicates": [p.render() for p in dc.predicates],
            "g1": round(dc.g1, 6),
            "exact": dc.exact,
            "tuple_scope": dc.tuple_scope,
            "support": dc.support,
        }

        friendly = _friendly_if_then(dc)

        if dc.g1 == 0:
            if friendly is not None:
                message = f"{friendly} always holds (a discovered invariant)."
            else:
                message = f"{dc.render()} always holds (a discovered invariant)."
            return Finding(
                severity=Severity.INFO,
                column=columns,
                check="denial_constraint",
                message=message,
                affected_rows=0,
                sample_values=_render_examples(rec, df, single),
                confidence=0.7,
                metadata=metadata,
            )

        n_viol = rec.n_violations
        if friendly is not None:
            message = (
                f"{friendly} holds {(1 - dc.g1):.1%} of the time; "
                f"{n_viol} {unit}(s) violate it."
            )
        else:
            message = (
                f"{dc.render()} holds {(1 - dc.g1):.1%} of the time; "
                f"{n_viol} {unit}(s) break it."
            )
        return Finding(
            severity=Severity.WARNING,
            column=columns,
            check="denial_constraint",
            message=message,
            affected_rows=n_viol,
            sample_values=_render_examples(rec, df, single),
            suggestion=(
                f"Review the {n_viol} {unit}(s) violating {dc.render()}; "
                "correct or confirm them."
            ),
            confidence=0.7,
            metadata=metadata,
        )


def _render_examples(rec: _Record, df: pl.DataFrame, single: bool) -> list[str]:
    """A few human-readable violating examples for the Finding."""
    cols = rec.dc.columns()
    if single:
        out: list[str] = []
        for r in rec.examples[:5]:
            vals = ", ".join(f"{col}={df[col][r]!r}" for col in cols)
            out.append(f"row {r}: {vals}")
        return out
    # cross: α/β are indices into the seeded validation sample, not df -- report
    # them symbolically (exact values would need the sample frame threaded here).
    return [f"sample pair (α={a}, β={b})" for a, b in rec.examples[:5]]
