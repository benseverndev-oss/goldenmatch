//! Denial-constraint evidence-set kernels.
//!
//! Fast-path replacement for `goldencheck/denial/evidence.py` (`row_evidence` /
//! `pair_evidence`), which build `{u64 satisfaction-mask -> count}` histograms
//! over slice-encoded columns. The Python module is the correctness ORACLE; this
//! kernel MUST reproduce its bit layout byte-for-byte (a later task asserts
//! parity).
//!
//! Columns are passed pre-encoded, mirroring `keys.rs`: `cols[c][r]` is the
//! interned/rank id of column `c` at row `r` (null -> a sentinel that is never
//! consulted, because `nulls[c][r]` gates it first). The ids are order-preserving
//! ranks (Python does the encoding), so id comparison equals value comparison for
//! ordered predicates -- unlike the equality-only key/FD kernels, the DC kernel
//! does ordered `<`/`<=`/`>`/`>=` comparisons over those rank-encoded ids.
//!
//! **Predicate evaluation matches `predicates.py::predicate_holds` exactly: any
//! null operand on the relevant row makes the predicate NOT satisfied.**

use rustc_hash::FxHashMap;

/// One predicate over the encoded columns. `kind`: 0 = const (`t.A op literal`),
/// 1 = single (`t.A op t.B`, one row), 2 = cross (`tα.A op tβ.B`, two rows).
/// `op`: 0=EQ 1=NE 2=LT 3=LE 4=GT 5=GE. `col_b`/`literal` are used per `kind`
/// (const uses `literal`, single/cross use `col_b`).
#[derive(Clone, Copy, Debug)]
pub struct Pred {
    pub kind: u8,
    pub col_a: usize,
    pub op: u8,
    pub col_b: usize,
    pub literal: u64,
}

/// Compare two ids under the predicate op code. Mirrors `predicates.py::_cmp`.
#[inline]
fn cmp(op: u8, x: u64, y: u64) -> bool {
    match op {
        0 => x == y,
        1 => x != y,
        2 => x < y,
        3 => x <= y,
        4 => x > y,
        _ => x >= y, // 5 = GE
    }
}

/// `t.A op literal` on row `r`. Null col_a -> false.
#[inline]
fn holds_const(p: &Pred, cols: &[&[u64]], nulls: &[&[bool]], r: usize) -> bool {
    if nulls[p.col_a][r] {
        return false;
    }
    cmp(p.op, cols[p.col_a][r], p.literal)
}

/// `t.A op t.B` on row `r`. Null col_a or col_b -> false.
#[inline]
fn holds_single(p: &Pred, cols: &[&[u64]], nulls: &[&[bool]], r: usize) -> bool {
    if nulls[p.col_a][r] || nulls[p.col_b][r] {
        return false;
    }
    cmp(p.op, cols[p.col_a][r], cols[p.col_b][r])
}

/// `tα.A op tβ.B` across rows `a` (alpha) and `b` (beta). Null operand -> false.
#[inline]
fn holds_cross(p: &Pred, cols: &[&[u64]], nulls: &[&[bool]], a: usize, b: usize) -> bool {
    if nulls[p.col_a][a] || nulls[p.col_b][b] {
        return false;
    }
    cmp(p.op, cols[p.col_a][a], cols[p.col_b][b])
}

/// A single-tuple predicate is evaluated on ONE row (kind 0 const or kind 1
/// single); dispatch on kind so the caller can pass a mixed `singles` slice.
#[inline]
fn holds_single_tuple(p: &Pred, cols: &[&[u64]], nulls: &[&[bool]], r: usize) -> bool {
    if p.kind == 0 {
        holds_const(p, cols, nulls, r)
    } else {
        holds_single(p, cols, nulls, r)
    }
}

/// Sort a `mask -> count` map into a deterministic `Vec<(mask, count)>` (by mask).
fn sorted_hist(hist: FxHashMap<u64, u64>) -> Vec<(u64, u64)> {
    let mut out: Vec<(u64, u64)> = hist.into_iter().collect();
    out.sort_unstable_by_key(|&(mask, _)| mask);
    out
}

/// Pass 1: `mask -> row-count` over rows `0..n`. Bit `i` (`0 <= i < s`) is set
/// iff `singles[i]` holds on that row. Cross predicates do not participate.
/// Mirrors `evidence.py::row_evidence`.
pub fn dc_row_evidence(
    singles: &[Pred],
    cols: &[&[u64]],
    nulls: &[&[bool]],
    n: usize,
) -> Vec<(u64, u64)> {
    debug_assert!(singles.len() <= 64, "single-mask must fit u64");
    let mut hist: FxHashMap<u64, u64> = FxHashMap::default();
    for r in 0..n {
        let mut mask: u64 = 0;
        for (i, p) in singles.iter().enumerate() {
            if holds_single_tuple(p, cols, nulls, r) {
                mask |= 1u64 << i;
            }
        }
        *hist.entry(mask).or_insert(0) += 1;
    }
    sorted_hist(hist)
}

/// Pass 2: `mask -> pair-count` over ALL ordered pairs `(alpha, beta)`,
/// `alpha != beta`, `alpha, beta in sample_idx`. Bit layout (matches
/// `evidence.py::pair_evidence`):
///   * bit `i`      (`0 <= i < s`) = `singles[i]` on alpha
///   * bit `s + i`                 = `singles[i]` on beta
///   * bit `2s + j` (`0 <= j < c`) = `crosses[j]` on `(alpha, beta)`
///
/// Optimization: each alpha's singles-on-alpha bits are constant across every
/// beta, so they are hoisted out of the inner loop -- identical output to the
/// naive double loop.
pub fn dc_pair_evidence(
    singles: &[Pred],
    crosses: &[Pred],
    cols: &[&[u64]],
    nulls: &[&[bool]],
    sample_idx: &[usize],
) -> Vec<(u64, u64)> {
    let s = singles.len();
    let c = crosses.len();
    debug_assert!(s + s + c <= 64, "pass-2 mask (2s + c) must fit u64");

    let mut hist: FxHashMap<u64, u64> = FxHashMap::default();
    for &alpha in sample_idx {
        // singles-on-alpha bits: constant across all beta for this alpha.
        let mut alpha_mask: u64 = 0;
        for (i, p) in singles.iter().enumerate() {
            if holds_single_tuple(p, cols, nulls, alpha) {
                alpha_mask |= 1u64 << i;
            }
        }
        for &beta in sample_idx {
            if alpha == beta {
                continue;
            }
            let mut mask = alpha_mask;
            for (i, p) in singles.iter().enumerate() {
                if holds_single_tuple(p, cols, nulls, beta) {
                    mask |= 1u64 << (s + i);
                }
            }
            for (j, p) in crosses.iter().enumerate() {
                if holds_cross(p, cols, nulls, alpha, beta) {
                    mask |= 1u64 << (2 * s + j);
                }
            }
            *hist.entry(mask).or_insert(0) += 1;
        }
    }
    sorted_hist(hist)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn row_evidence_matches_manual() {
        // 2 single preds over 3 rows. col0 ids, col1 ids; no nulls.
        // singles[0] = const: col0 == 1 ;  singles[1] = single: col0 < col1
        let col0 = [1u64, 2, 1];
        let col1 = [3u64, 1, 1];
        let cols: [&[u64]; 2] = [&col0, &col1];
        let n0 = [false; 3];
        let n1 = [false; 3];
        let nulls: [&[bool]; 2] = [&n0, &n1];
        let singles = [
            Pred {
                kind: 0,
                col_a: 0,
                op: 0,
                col_b: 0,
                literal: 1,
            }, // col0 == 1
            Pred {
                kind: 1,
                col_a: 0,
                op: 2,
                col_b: 1,
                literal: 0,
            }, // col0 < col1
        ];
        // row0: col0==1 true(bit0), 1<3 true(bit1) -> 0b11
        // row1: col0==1 false,      2<1 false      -> 0b00
        // row2: col0==1 true(bit0), 1<1 false      -> 0b01
        let ev = dc_row_evidence(&singles, &cols, &nulls, 3);
        let map: HashMap<u64, u64> = ev.into_iter().collect();
        assert_eq!(map[&0b11], 1);
        assert_eq!(map[&0b00], 1);
        assert_eq!(map[&0b01], 1);
    }

    #[test]
    fn null_operand_makes_predicate_false() {
        // single pred col0 < col1, row where col0 is null -> bit unset
        let col0 = [5u64, 0];
        let col1 = [9u64, 9];
        let cols: [&[u64]; 2] = [&col0, &col1];
        let n0 = [false, true];
        let n1 = [false, false];
        let nulls: [&[bool]; 2] = [&n0, &n1];
        let singles = [Pred {
            kind: 1,
            col_a: 0,
            op: 2,
            col_b: 1,
            literal: 0,
        }];
        let ev = dc_row_evidence(&singles, &cols, &nulls, 2);
        let map: HashMap<u64, u64> = ev.into_iter().collect();
        assert_eq!(map[&0b1], 1); // row0: 5<9 true
        assert_eq!(map[&0b0], 1); // row1: col0 null -> false
    }

    #[test]
    fn pair_evidence_alpha_beta_bits() {
        // 1 single (const col0==1), 1 cross (col0 < col0 across tuples).
        // s=1,c=1 -> bits: [0]=single@α [1]=single@β [2]=cross
        let col0 = [1u64, 2];
        let cols: [&[u64]; 1] = [&col0];
        let n0 = [false; 2];
        let nulls: [&[bool]; 1] = [&n0];
        let singles = [Pred {
            kind: 0,
            col_a: 0,
            op: 0,
            col_b: 0,
            literal: 1,
        }];
        let crosses = [Pred {
            kind: 2,
            col_a: 0,
            op: 2,
            col_b: 0,
            literal: 0,
        }]; // tα.col0 < tβ.col0
        let ev = dc_pair_evidence(&singles, &crosses, &cols, &nulls, &[0, 1]);
        let map: HashMap<u64, u64> = ev.into_iter().collect();
        // pair(0,1): single@α: col0[0]==1 true (bit0); single@β: col0[1]==1 false;
        //            cross: 1<2 true (bit2) -> 0b101
        // pair(1,0): single@α: col0[1]==1 false; single@β: col0[0]==1 true (bit1);
        //            cross: 2<1 false -> 0b010
        assert_eq!(map[&0b101], 1);
        assert_eq!(map[&0b010], 1);
        assert_eq!(map.values().sum::<u64>(), 2);
    }

    #[test]
    fn all_ops_evaluate() {
        // one row, col0=2 col1=2, exercise every op code as a single pred.
        let col0 = [2u64];
        let col1 = [2u64];
        let cols: [&[u64]; 2] = [&col0, &col1];
        let n0 = [false];
        let nulls: [&[bool]; 2] = [&n0, &n0];
        // EQ,LE,GE true; NE,LT,GT false for 2 vs 2.
        for (op, expect) in [
            (0u8, true),
            (1, false),
            (2, false),
            (3, true),
            (4, false),
            (5, true),
        ] {
            let singles = [Pred {
                kind: 1,
                col_a: 0,
                op,
                col_b: 1,
                literal: 0,
            }];
            let ev = dc_row_evidence(&singles, &cols, &nulls, 1);
            let map: HashMap<u64, u64> = ev.into_iter().collect();
            let want = if expect { 0b1 } else { 0b0 };
            assert_eq!(map.get(&want), Some(&1), "op {op} expect {expect}");
        }
    }

    #[test]
    fn hist_is_sorted_by_mask() {
        // distinct masks must come back ascending for determinism.
        let col0 = [1u64, 2, 3];
        let cols: [&[u64]; 1] = [&col0];
        let n0 = [false; 3];
        let nulls: [&[bool]; 1] = [&n0];
        let singles = [Pred {
            kind: 0,
            col_a: 0,
            op: 0,
            col_b: 0,
            literal: 1,
        }];
        let ev = dc_row_evidence(&singles, &cols, &nulls, 3);
        assert!(ev.windows(2).all(|w| w[0].0 < w[1].0));
    }
}
