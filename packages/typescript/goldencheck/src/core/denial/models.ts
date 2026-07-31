/**
 * Denial-constraint data models — parity port of
 * `packages/python/goldencheck/goldencheck/denial/models.py`.
 *
 * Edge-safe: no Node.js deps.
 */

import type { ColumnValue } from "../data.js";

/** Comparison operators; the enum *value* is the rendered unicode symbol. */
export const Op = {
  EQ: "=",
  NE: "≠",
  LT: "<",
  LE: "≤",
  GT: ">",
  GE: "≥",
} as const;

export type Op = (typeof Op)[keyof typeof Op];

export type PredicateKind = "const" | "single" | "cross";

/**
 * Render a const literal the way Python's `Predicate.render` does: single-quote
 * strings, `repr` everything else. Booleans mirror Python's `True`/`False`.
 * (Float `repr` divergence — Python `repr(2.0)` == "2.0" vs JS `String(2.0)` ==
 * "2" — cannot occur here: const literals only come from low-card columns and the
 * int/float dtype split means an int column never carries a float literal.)
 */
export function renderLiteral(literal: ColumnValue | null): string {
  if (typeof literal === "string") return `'${literal}'`;
  if (typeof literal === "boolean") return literal ? "True" : "False";
  return String(literal);
}

/**
 * One predicate. `kind`:
 *   - "const"  → `t.A op literal`
 *   - "single" → `t.A op t.B` (same tuple)
 *   - "cross"  → `tα.A op tβ.B` (across a pair)
 */
export class Predicate {
  constructor(
    readonly kind: PredicateKind,
    readonly colA: string,
    readonly op: Op,
    readonly colB: string | null,
    readonly literal: ColumnValue | null,
  ) {}

  render(): string {
    if (this.kind === "const") {
      return `${this.colA} ${this.op} ${renderLiteral(this.literal)}`;
    }
    return `${this.colA} ${this.op} ${this.colB}`;
  }
}

/**
 * A discovered denial constraint `¬(p1 ∧ … ∧ pm)`: the predicate conjunction
 * should (almost) never hold. `g1` = fraction of elements (rows for single-tuple
 * scope, pairs for cross) that violate it. `exact` = g1 measured on the full data
 * (single-tuple) vs a sample (cross).
 */
export class DenialConstraint {
  constructor(
    readonly predicates: readonly Predicate[],
    readonly g1: number,
    readonly support: number,
    readonly tupleScope: "single" | "cross",
    readonly exact: boolean,
  ) {}

  columns(): string[] {
    const seen: string[] = [];
    for (const p of this.predicates) {
      for (const c of [p.colA, p.colB]) {
        if (c !== null && !seen.includes(c)) seen.push(c);
      }
    }
    return seen;
  }

  render(): string {
    return "¬(" + this.predicates.map((p) => p.render()).join(" ∧ ") + ")";
  }
}
