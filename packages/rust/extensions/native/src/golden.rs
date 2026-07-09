//! Fused Arrow-native golden-record kernel — cluster map + decision columns ->
//! per-(cluster, column) SOURCE-ROW INDICES + confidences, in ONE FFI call.
//!
//! The kernel returns INDICES, never values: for each output column and each
//! cluster it emits `winner_idx` (the global position, in the pre-sorted frame,
//! whose value survives; `-1` = null) and `field_conf` (the field confidence).
//! Python materializes the golden frame with one `.gather(winner_idx)` per column
//! on the original typed data — so the wide `multi_df` never exists and native
//! dtypes / byte-identical values come for free. Byte-parity target:
//! `core/golden.py::build_golden_records_batch` (the exact `merge_field` path).
//!
//! Contract (enforced by the Python caller `run_golden_fused_arrow`): rows are
//! pre-sorted by `(cluster_id, row_id)`, so members of a cluster are a CONTIGUOUS
//! `row_id`-ascending span. Every order-dependent tie-break resolves to "first
//! occurrence," which the ascending order makes match the reference.
//!
//! Design: `docs/superpowers/specs/2026-07-08-fused-golden-record-kernel-design.md`.

use arrow::array::{ArrayData, Float64Array, Int64Array};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::score::StrCol;

// Strategy ids — shared with Python `_GOLDEN_STRATEGY_IDS`.
const STRAT_MOST_COMPLETE: u8 = 0;
const STRAT_MAJORITY_VOTE: u8 = 1;
const STRAT_SOURCE_PRIORITY: u8 = 2;
const STRAT_MOST_RECENT: u8 = 3;
const STRAT_FIRST_NON_NULL: u8 = 4;
const STRAT_LONGEST_VALUE: u8 = 5;
const STRAT_UNANIMOUS_OR_NULL: u8 = 6;
const STRAT_CONFIDENCE_MAJORITY: u8 = 7;

// Group strategy ids — shared with Python `_GROUP_STRATEGY_IDS`. A DISTINCT enum
// from the scalar `STRAT_*` above: group ranking (winner.py::group_winner) is not
// the scalar merge_field dispatch, and `anchor` is group-only.
const GROUP_MOST_COMPLETE: u8 = 0;
const GROUP_SOURCE_PRIORITY: u8 = 1;
const GROUP_MOST_RECENT: u8 = 2;
const GROUP_ANCHOR: u8 = 3;

/// The non-null members of one cluster span as `(local_idx, code)`, in span
/// order. `code == -1` (the Python-side factorization null sentinel) is a null.
fn span_non_null(code: &[i64], off: usize, size: usize) -> Vec<(usize, i64)> {
    let mut v = Vec::with_capacity(size);
    for l in 0..size {
        let c = code[off + l];
        if c != -1 {
            v.push((l, c));
        }
    }
    v
}

/// Universal pre-dispatch decisions (`merge_field:76`/`:82`), on RAW-VALUE
/// (code) equality — NOT text. Returns `Some((local_idx, conf))` when it
/// resolves the field on its own: `(-1, 0.0)` for an all-null column, and
/// `(first_non_null_idx, 1.0)` when every non-null member shares one code
/// (matches the reference `set(v)` short-circuit and, on mixed-type columns,
/// stays byte-identical where a `str(v)` short-circuit would diverge). `None`
/// means "dispatch to the per-strategy branch."
fn universal_short_circuit(non_null: &[(usize, i64)]) -> Option<(i64, f64)> {
    if non_null.is_empty() {
        return Some((-1, 0.0));
    }
    let first = non_null[0].1;
    if non_null.iter().all(|&(_, c)| c == first) {
        return Some((non_null[0].0 as i64, 1.0));
    }
    None
}

/// Char length of the member at `off + local` (`str(v)` was materialized on the
/// Python side, so this is Python `len(str(v))` = Unicode code points). A null
/// text cell should never reach here (callers pass only non-null members), but
/// map it to length 0 defensively.
fn char_len(text: &StrCol, off: usize, local: usize) -> usize {
    text.get(off + local)
        .map(|s| s.chars().count())
        .unwrap_or(0)
}

/// The FIRST index in `0..n` whose `key` is maximal. Strict `>` keeps the
/// earliest maximal element on a tie -- byte-parity with Python `max(...)` (first
/// max), NOT `max_by_key` (which returns the LAST max and would silently flip a
/// weight/count tie to the wrong representative). Single-sources every hand-rolled
/// first-max scan (weighted longest tie-break / majority_vote / first_non_null).
fn first_max_idx(n: usize, key: impl Fn(usize) -> f64) -> usize {
    let mut best = 0usize;
    for i in 1..n {
        if key(i) > key(best) {
            best = i;
        }
    }
    best
}

/// Weighted length-tie confidence rule -- the ONE behavioral divergence between
/// `most_complete` and `longest_value` on the QUALITY-WEIGHTED tie path (they
/// share the pick, NOT the confidence):
/// - `most_complete` (`golden.py:134`) -> `min(1.0, 0.7 * w_winner)` (`ScaledByWeight`).
/// - `longest_value` (`golden.py:233`) -> a FLAT `0.7`, ignoring the weight (`Flat`).
///
/// Do NOT collapse these into one rule -- conflating them ships a silently-wrong
/// confidence on the weighted tie (the plan's Stage 3 warning). The unweighted
/// length-tie confidences ALSO differ (0.7 vs 0.5), carried by `tie_conf`.
#[derive(Clone, Copy)]
enum WeightedTieConf {
    /// most_complete: `min(1.0, 0.7 * w_winner)`.
    ScaledByWeight,
    /// longest_value: flat `0.7`.
    Flat,
}

/// Shared "longest `str(v)` wins" pick for `most_complete` / `longest_value`.
/// Unique longest -> conf 1.0 (weights irrelevant). On a LENGTH tie:
/// - unweighted (`weights` None) -> first-in-order member at `tie_conf`
///   (0.7 most_complete, 0.5 longest_value).
/// - weighted (`weights` Some) -> the highest-`qweight` member among the longest
///   (first-max on a weight tie, preserving span order, matching Python `max`),
///   at the confidence dictated by `wtie` (the ONLY most_complete/longest_value
///   divergence on this path). Missing-index weight falls back to `1.0`
///   (`x[0] < len(quality_weights)`); with full-length per-column weights this
///   never fires, but is kept faithful to the reference.
fn longest_pick(
    text: &StrCol,
    non_null: &[(usize, i64)],
    off: usize,
    tie_conf: f64,
    weights: Option<&[f64]>,
    wtie: WeightedTieConf,
) -> (i64, f64) {
    let max_len = non_null
        .iter()
        .map(|&(l, _)| char_len(text, off, l))
        .max()
        .unwrap();
    let longest: Vec<usize> = non_null
        .iter()
        .filter(|&&(l, _)| char_len(text, off, l) == max_len)
        .map(|&(l, _)| l)
        .collect();
    if longest.len() == 1 {
        return (longest[0] as i64, 1.0);
    }
    match weights {
        None => (longest[0] as i64, tie_conf),
        Some(w) => {
            let wt = |l: usize| w.get(off + l).copied().unwrap_or(1.0);
            // Highest-weight member among the longest; first-max on a weight tie
            // (span order preserved, since `longest` is in span order) matches
            // Python `max(longest, key=...)`.
            let best = longest[first_max_idx(longest.len(), |i| wt(longest[i]))];
            let conf = match wtie {
                WeightedTieConf::ScaledByWeight => (0.7 * wt(best)).min(1.0),
                WeightedTieConf::Flat => 0.7,
            };
            (best as i64, conf)
        }
    }
}

/// `_most_complete` (`golden.py:125`), sans the short-circuit (handled
/// universally): longest `str(v)`, length tie -> first-in-order at conf 0.7
/// (unweighted) or the highest-weight longest at `min(1.0, 0.7*w)` (weighted).
fn most_complete(
    text: &StrCol,
    non_null: &[(usize, i64)],
    off: usize,
    weights: Option<&[f64]>,
) -> (i64, f64) {
    longest_pick(
        text,
        non_null,
        off,
        0.7,
        weights,
        WeightedTieConf::ScaledByWeight,
    )
}

/// `_longest_value` (`golden.py:209`): same pick as `most_complete` but a length
/// tie yields conf 0.5 unweighted, and a FLAT 0.7 on the weighted tie (NOT
/// `min(1.0, 0.7*w)` -- the most_complete/longest_value divergence).
fn longest_value(
    text: &StrCol,
    non_null: &[(usize, i64)],
    off: usize,
    weights: Option<&[f64]>,
) -> (i64, f64) {
    longest_pick(text, non_null, off, 0.5, weights, WeightedTieConf::Flat)
}

/// `_majority_vote` (`golden.py:139`). Unweighted (`weights` None): highest code
/// COUNT wins; a count tie resolves to the code encountered FIRST in span order
/// (the `Counter.most_common` stable-order tie-break); `conf = count / n_non_null`.
/// Weighted (`weights` Some, `golden.py:140`): sum each code's per-member qweight;
/// highest WEIGHT-SUM wins (first-appearance tie-break); `conf = winner_weight /
/// total_weight` (0.0 when total is 0). In both, the winner index is the winning
/// code's first occurrence.
fn majority_vote(non_null: &[(usize, i64)], off: usize, weights: Option<&[f64]>) -> (i64, f64) {
    if let Some(w) = weights {
        let wt = |l: usize| w.get(off + l).copied().unwrap_or(1.0);
        // (code, first_local_idx, weight_sum) in first-appearance order -- mirrors
        // the reference's insertion-ordered `value_weights` dict, so both the
        // winner tie-break (first max) and the per-code summation order match.
        let mut order: Vec<(i64, usize, f64)> = Vec::new();
        for &(l, c) in non_null {
            let wl = wt(l);
            if let Some(e) = order.iter_mut().find(|e| e.0 == c) {
                e.2 += wl;
            } else {
                order.push((c, l, wl));
            }
        }
        // First-appearance tie-break (see `first_max_idx`): keeps the EARLIEST
        // code on a weight-sum tie, matching the reference's insertion-ordered
        // `max(value_weights, key=...)`.
        let best = first_max_idx(order.len(), |i| order[i].2);
        let total: f64 = order.iter().map(|e| e.2).sum();
        let conf = if total > 0.0 {
            order[best].2 / total
        } else {
            0.0
        };
        return (order[best].1 as i64, conf);
    }
    // (code, first_local_idx, count) in first-appearance order.
    let mut order: Vec<(i64, usize, usize)> = Vec::new();
    for &(l, c) in non_null {
        if let Some(e) = order.iter_mut().find(|e| e.0 == c) {
            e.2 += 1;
        } else {
            order.push((c, l, 1));
        }
    }
    // First-appearance tie-break keeps the EARLIEST-appearing code on a count tie
    // (matching `Counter.most_common`'s stable order); `first_max_idx`'s strict
    // `>` encodes it -- NOT `max_by_key` (returns the LAST max, wrong index).
    let best = first_max_idx(order.len(), |i| order[i].2 as f64);
    let conf = order[best].2 as f64 / non_null.len() as f64;
    (order[best].1 as i64, conf)
}

/// `_unanimous_or_null` (`golden.py:237`). Exactly one distinct non-null code
/// -> that value, conf 1.0; any disagreement -> null, conf 0.0. (The unanimous
/// case is already caught by `universal_short_circuit`; kept explicit for a
/// direct call and defensive completeness.)
fn unanimous_or_null(non_null: &[(usize, i64)]) -> (i64, f64) {
    let first = non_null[0].1;
    if non_null.iter().all(|&(_, c)| c == first) {
        (non_null[0].0 as i64, 1.0)
    } else {
        (-1, 0.0)
    }
}

/// `_first_non_null` (`golden.py:198`). Unweighted (`weights` None): first
/// non-null in span order. Weighted (`weights` Some, `golden.py:199`): the
/// highest-`qweight` non-null (first-max on a weight tie == span order, matching
/// Python `max`). Conf is `0.6` either way.
fn first_non_null(non_null: &[(usize, i64)], off: usize, weights: Option<&[f64]>) -> (i64, f64) {
    match weights {
        None => (non_null[0].0 as i64, 0.6),
        Some(w) => {
            let wt = |l: usize| w.get(off + l).copied().unwrap_or(1.0);
            // Highest-weight non-null; first-max on a tie == span order (the
            // reference's `max(non_null, key=...)`).
            let best = non_null[first_max_idx(non_null.len(), |i| wt(non_null[i].0))].0;
            (best as i64, 0.6)
        }
    }
}

/// `_source_priority` (`golden.py:142`). Records the FIRST row per source
/// (regardless of null value), then walks `priority` (a source-code list); the
/// first source whose first-occurrence value is non-null wins.
/// `conf = max(0.1, 1.0 - idx*0.1)`; no match -> `(-1, 0.0)`.
///
/// Precise null handling (matches the reference `source_val[src] = val` /
/// `if val is not None`): the winner value is specifically the FIRST row of the
/// winning source. If that first row's value is null, the source is skipped even
/// if a LATER row of the same source has a non-null value — we only ever look at
/// the first row's `value_code`.
///
/// `source_code[i] < 0` (null `__source__`) rows are never a priority target (a
/// priority list holds strings, never None), and an ABSENT priority source is
/// encoded as a negative code in Python — so both are excluded by the `< 0`
/// guard, which also prevents an absent-priority sentinel from colliding with
/// the null-source group. Winner index is the LOCAL span index.
fn source_priority(
    source_code: &[i64],
    value_code: &[i64],
    priority: &[i64],
    off: usize,
    size: usize,
) -> (i64, f64) {
    // First-occurrence (source_code >= 0) in span order: (source_code, local).
    let mut first: Vec<(i64, usize)> = Vec::new();
    for l in 0..size {
        let sc = source_code[off + l];
        if sc < 0 {
            continue;
        }
        if !first.iter().any(|&(s, _)| s == sc) {
            first.push((sc, l));
        }
    }
    for (idx, &pc) in priority.iter().enumerate() {
        if pc < 0 {
            continue; // absent priority source (or reserved sentinel)
        }
        if let Some(&(_, first_local)) = first.iter().find(|&&(s, _)| s == pc) {
            if value_code[off + first_local] != -1 {
                let conf = (1.0 - idx as f64 * 0.1).max(0.1);
                return (first_local as i64, conf);
            }
        }
    }
    (-1, 0.0)
}

/// `_most_recent` (`golden.py:166`). Eligible rows = value non-null AND date
/// non-null. Python `sort(key=date, reverse=True)` is STABLE, so among rows tied
/// on the top date the FIRST-occurring (lowest local index) wins — replicated
/// here as "first eligible row holding the max date" (NOT a reversed comparator,
/// which would pick the last). `conf = 0.5` when >=2 eligible rows share the top
/// date, else `1.0`; none eligible -> `(-1, 0.0)`.
fn most_recent(
    value_code: &[i64],
    date: &[i64],
    date_null: &[i64],
    off: usize,
    size: usize,
) -> (i64, f64) {
    let eligible = |l: usize| value_code[off + l] != -1 && date_null[off + l] == 0;
    let mut max_date: Option<i64> = None;
    for l in 0..size {
        if !eligible(l) {
            continue;
        }
        let d = date[off + l];
        max_date = Some(match max_date {
            Some(m) if m >= d => m,
            _ => d,
        });
    }
    let md = match max_date {
        Some(m) => m,
        None => return (-1, 0.0),
    };
    let mut winner_local: i64 = -1;
    let mut tie_count = 0usize;
    for l in 0..size {
        if eligible(l) && date[off + l] == md {
            if winner_local < 0 {
                winner_local = l as i64;
            }
            tie_count += 1;
        }
    }
    let conf = if tie_count > 1 { 0.5 } else { 1.0 };
    (winner_local, conf)
}

/// `_confidence_majority` (`golden.py:252`), sans the universal short-circuit
/// (handled before dispatch). For each edge `(a, b, score)` whose BOTH endpoints
/// hold a non-null code AND those codes AGREE, add `score` to that code's
/// weight-sum; the max weight-sum code wins; `conf = winner_sum / total_sum`
/// (`0.5` when total is 0 — unreachable with agreeing edges, kept for parity).
///
/// Representative index = the FIRST endpoint `a` of the FIRST agreeing edge for
/// the winning code — NOT the min/canonical endpoint, and the edge ITERATION
/// ORDER is load-bearing (Python passes edges in `pair_scores.items()` order; a
/// different order picks a different representative). The insertion-ordered `order`
/// Vec mirrors the reference's `value_weights` / `value_idx` dicts, so both the
/// winner tie-break (first-max over insertion order) and the representative index
/// match.
///
/// Empty `edges` (no pair scores for this cluster) OR no agreeing edge -> fall
/// back to the (quality-weighted) `majority_vote`, exactly as the reference does
/// on `if not pair_scores` / `if not value_weights`.
fn confidence_majority(
    value_code: &[i64],
    non_null: &[(usize, i64)],
    edges: &[(usize, usize, f64)],
    off: usize,
    weights: Option<&[f64]>,
) -> (i64, f64) {
    if edges.is_empty() {
        return majority_vote(non_null, off, weights);
    }
    // (code, first_representative_local_idx, weight_sum), in first-agreeing-edge
    // order (== the reference's insertion-ordered value_weights dict).
    let mut order: Vec<(i64, usize, f64)> = Vec::new();
    for &(a, b, s) in edges {
        // `a in idx_to_value and b in idx_to_value`: both endpoints non-null.
        let va = value_code[off + a];
        let vb = value_code[off + b];
        if va == -1 || vb == -1 {
            continue;
        }
        if va == vb {
            if let Some(e) = order.iter_mut().find(|e| e.0 == va) {
                e.2 += s;
            } else {
                // Representative = first endpoint `a` of this first agreeing edge
                // (`value_idx[va] = a`), NOT min(a, b).
                order.push((va, a, s));
            }
        }
    }
    if order.is_empty() {
        return majority_vote(non_null, off, weights);
    }
    // First-max over insertion order keeps the EARLIEST code on a weight-sum tie,
    // matching `max(value_weights, key=...)` on an insertion-ordered dict.
    let best = first_max_idx(order.len(), |i| order[i].2);
    let total: f64 = order.iter().map(|e| e.2).sum();
    let conf = if total > 0.0 {
        order[best].2 / total
    } else {
        0.5
    };
    (order[best].1 as i64, conf)
}

/// One field_group's owned kernel data, pre-read from Arrow (before `py.detach`).
/// `col_indices` = output-column indices this group spans; `date`/`date_null` are
/// full-length (n_rows) for `most_recent` groups (empty otherwise);
/// `anchor_col_index` is the anchor column's output index for `anchor` groups
/// (`-1` otherwise).
struct GroupPrep {
    col_indices: Vec<usize>,
    strategy: u8,
    priority_codes: Vec<i64>,
    date: Vec<i64>,
    date_null: Vec<i64>,
    anchor_col_index: i64,
    allow_fill: bool,
}

/// `winner.py::group_winner` — correlated / lock-step survivorship for one group,
/// over one cluster's span `[off, off+size)`. Ranks the cluster's rows ONCE by the
/// group strategy, pins ONE winner row across every group column (or, under
/// `allow_fill`, per-column back-fill from the next-best-ranked row holding that
/// column), and returns `(per_column_global_index, group_confidence)`.
///
/// Returns a global winner index for EVERY group column (never `-1`): a
/// null-pinned column points at the winner row whose cell IS null, so the Python
/// `.gather()` yields null naturally (no sentinel needed — the group `n == 0`
/// empty case can't occur here, spans are size >= 1).
///
/// Confidence (`winner.py:74`): `base = (winner_populated + n_filled) / n_cols`,
/// `x0.7` on tie. Ranking / tie semantics per `_ranking` (`winner.py:21`):
/// - `most_complete`: populated-count DESC (stable); tie = >=2 rows share max.
/// - `source_priority`: priority-rank ASC (stable, absent source ranks last);
///   tie always False.
/// - `most_recent`: `(date_present, date)` DESC (stable); tie always False.
/// - `anchor`: `(anchor_present, populated)` DESC (stable); tie = >=2 rows share
///   the top composite. Degrades to most_complete when no row has the anchor.
///
/// **Stability is load-bearing** (as in the scalar strategies): Python's `sorted`
/// is stable and `reverse=True` preserves input order among equal keys, so the
/// first-occurring (lowest local index) tied row is `order[0]`. Rust `sort_by` is
/// stable; a DESC key comparison that returns `Equal` on ties preserves that.
fn resolve_group(
    prep: &GroupPrep,
    off: usize,
    size: usize,
    code_vals: &[Vec<i64>],
    source_vals: &[i64],
) -> (Vec<i64>, f64) {
    let cols = &prep.col_indices;
    let ncg = cols.len();
    let populated = |l: usize| {
        cols.iter()
            .filter(|&&c| code_vals[c][off + l] != -1)
            .count()
    };

    // Default = most_complete ranking (populated-count DESC, stable). The non-
    // most_complete strategies override `order`/`tie` below; GROUP_MOST_COMPLETE
    // (and any unknown code, which degrades to most_complete since Python
    // validates the strategy) keeps this default.
    let mut order: Vec<usize> = (0..size).collect();
    let counts: Vec<usize> = (0..size).map(populated).collect();
    order.sort_by(|&a, &b| counts[b].cmp(&counts[a]));
    let top_count = counts[order[0]];
    let mut tie = counts.iter().filter(|&&c| c == top_count).count() > 1;
    match prep.strategy {
        GROUP_SOURCE_PRIORITY => {
            // rank(l) = index of the row's source in the priority list, or
            // `len(priority)` when absent/null (winner.py:25-26). A null source
            // (`sc < 0`) never matches an absent-priority sentinel (`pc < 0`).
            //
            // KNOWN DIVERGENCE (out-of-contract, no clean fix): for a MALFORMED
            // priority list with DUPLICATE sources, this returns the FIRST index
            // of a match, matching the SCALAR reference `_source_priority`
            // (golden.py, walk-and-return-on-first-match). But the GROUP reference
            // `winner.py::_ranking` builds a dict `{s: i}` that keeps the LAST
            // index. So the two reference paths ALREADY disagree with each other
            // on duplicate priority lists; no single kernel behavior can byte-match
            // both. `GoldenGroupRule` doesn't forbid duplicates, but they're a user
            // error (a priority list is an ordered set). Flagged for a shared
            // priority-rank helper on the next source_priority touch.
            let rank = |l: usize| -> usize {
                let sc = source_vals[off + l];
                if sc >= 0 {
                    for (j, &pc) in prep.priority_codes.iter().enumerate() {
                        if pc >= 0 && pc == sc {
                            return j;
                        }
                    }
                }
                prep.priority_codes.len()
            };
            let ranks: Vec<usize> = (0..size).map(rank).collect();
            order = (0..size).collect();
            order.sort_by(|&a, &b| ranks[a].cmp(&ranks[b])); // ASC, stable
            tie = false; // winner.py: source_priority tie is always False
        }
        GROUP_MOST_RECENT => {
            // key = (present, date) DESC; absent-date rows share a constant date
            // component (0) so they compare EQUAL among themselves (winner.py:29
            // never compares None to None because the present flag differs first).
            let key = |l: usize| -> (i64, i64) {
                if prep.date_null[off + l] == 0 {
                    (1, prep.date[off + l])
                } else {
                    (0, 0)
                }
            };
            let keys: Vec<(i64, i64)> = (0..size).map(key).collect();
            order = (0..size).collect();
            order.sort_by(|&a, &b| keys[b].cmp(&keys[a])); // DESC, stable
            tie = false; // winner.py: most_recent tie is always False
        }
        GROUP_ANCHOR => {
            let ai = prep.anchor_col_index as usize;
            let key = |l: usize| -> (i64, usize) {
                let present = if code_vals[ai][off + l] != -1 { 1 } else { 0 };
                (present, counts[l])
            };
            let keys: Vec<(i64, usize)> = (0..size).map(key).collect();
            order = (0..size).collect();
            order.sort_by(|&a, &b| keys[b].cmp(&keys[a])); // DESC, stable
            let top = keys[order[0]];
            tie = keys.iter().filter(|&&k| k == top).count() > 1;
        }
        GROUP_MOST_COMPLETE => {} // keep the default most_complete ranking above
        _ => {}                   // unknown code -> degrade to most_complete
    }

    let best = order[0];
    let mut per_col_idx = Vec::with_capacity(ncg);
    let mut n_filled = 0usize;
    for &c in cols {
        if code_vals[c][off + best] != -1 || !prep.allow_fill {
            // Non-null winner cell, OR no back-fill: pin the winner row (a null
            // cell here gathers to null on the Python side).
            per_col_idx.push((off + best) as i64);
        } else {
            // allow_fill: winner cell is null -> the FIRST next-best-ranked row
            // holding a non-null value for this column (winner.py:66-72). If none
            // is found the value stays the winner's null (not counted as filled).
            let mut gi = (off + best) as i64;
            for &j in &order[1..] {
                if code_vals[c][off + j] != -1 {
                    gi = (off + j) as i64;
                    n_filled += 1;
                    break;
                }
            }
            per_col_idx.push(gi);
        }
    }

    let winner_populated = populated(best);
    let base = (winner_populated + n_filled) as f64 / ncg as f64;
    let conf = if tie { base * 0.7 } else { base };
    (per_col_idx, conf)
}

/// Kernel result: `(winner_idx, field_conf, group_conf, cluster_ids_out)`.
/// `winner_idx[col][k]` is the GLOBAL pre-sorted-frame row index whose value
/// survives for column `col` in cluster `k` (`-1` = null); `field_conf[col][k]`
/// its confidence (a placeholder `0.0` for group-owned columns — the group's
/// single confidence lives in `group_conf[g][k]`, one entry per field_group). The
/// Python caller folds `group_conf` into the cluster mean ONCE per group (spec §8).
type GoldenFusedResult = (Vec<Vec<i64>>, Vec<Vec<f64>>, Vec<Vec<f64>>, Vec<i64>);

/// Per-column strategy SIDE CHANNELS, extracted from a Python
/// `_GoldenFusedSideChannels` dataclass (attribute-named to match these fields).
/// Consolidating the side channels into ONE carrier keeps `golden_fused`'s
/// positional arity flat as later stages add channels: each new channel is ONE
/// struct field + ONE Python assignment, not a new positional arg threaded
/// through both the marshal site and this destructure.
///
/// Stage 2 fields:
/// - `source_code`: factorized `__source__` (Int64, len n_rows) — present only
///   when some column uses source_priority (else an empty array).
/// - `priority_codes[col]`: that column's source_priority list mapped into
///   source-code space (empty for non source_priority columns; absent sources
///   encoded `< 0`).
/// - `date_cols[col]` / `date_null_masks[col]`: Int64 arrays (len n_rows) for
///   most_recent columns (empty arrays otherwise); the mask is 1 = null-date,
///   0 = present.
///
/// Stage 3 field:
/// - `qweights[col]`: per-column Float64 quality weights (len n_rows), aligned
///   to the sorted frame — present (all columns, even all-`1.0`) ONLY when the
///   caller passed a non-None `quality_scores`; an EMPTY array signals the
///   unweighted branch (byte-identical to Stages 0-2). Mirrors `merge_field`'s
///   `quality_weights`: only most_complete/majority_vote/first_non_null/
///   longest_value consult it; source_priority/most_recent/unanimous ignore it.
///
/// Stage 4 field:
/// - `pair_edges`: confidence_majority pair-score edges, flattened GLOBALLY to
///   `(cluster_id, a_local, b_local, score)` tuples. Positions are LOCAL to the
///   cluster's sorted span; the kernel buckets them by `cluster_id` (so Python
///   never predicts span order) and preserves per-cluster INSERTION ORDER, which
///   is the incoming `pair_scores.items()` order — load-bearing for the
///   representative index (spec 6.4). Empty when no confidence_majority column is
///   present. Edges are shared across all confidence_majority columns of a cluster
///   (pair scores are per-cluster, not per-column).
///
/// Stage 5 field:
/// - `group_specs`: one `GroupSpec` per `rules.field_groups`. A group resolves as
///   a UNIT (winner.py::group_winner): the kernel ranks the cluster's rows once,
///   pins one winner row across all the group's columns (or per-column back-fill
///   under `allow_fill`), and emits ONE confidence folded into the cluster mean.
///   Empty when no field_groups are configured.
///
/// Future stages append fields here (predicate IR, cluster-override codes) — same
/// one-field-per-stage rule.
#[derive(FromPyObject)]
pub struct GoldenFusedSideChannels {
    source_code: PyArrowType<ArrayData>,
    priority_codes: Vec<Vec<i64>>,
    date_cols: Vec<PyArrowType<ArrayData>>,
    date_null_masks: Vec<PyArrowType<ArrayData>>,
    qweights: Vec<PyArrowType<ArrayData>>,
    pair_edges: Vec<(i64, i64, i64, f64)>,
    group_specs: Vec<GroupSpec>,
}

/// One field_group's kernel spec, extracted from a Python `_GoldenFusedGroupSpec`
/// dataclass. `col_indices` are output-column indices; `strategy` is a `GROUP_*`
/// code; `priority_codes` maps the group's source_priority list into source-code
/// space (source_priority); `date_col`/`date_null_mask` are len-n_rows Int64 for
/// most_recent (empty otherwise); `anchor_col_index` is the anchor column's output
/// index for anchor (`-1` otherwise); `allow_fill` toggles per-column back-fill.
#[derive(FromPyObject)]
pub struct GroupSpec {
    col_indices: Vec<i64>,
    strategy: u8,
    priority_codes: Vec<i64>,
    date_col: PyArrowType<ArrayData>,
    date_null_mask: PyArrowType<ArrayData>,
    anchor_col_index: i64,
    allow_fill: bool,
}

#[pyfunction]
#[pyo3(signature = (
    row_ids, cluster_ids, n_output_cols, strategy_ids, text_cols, code_cols, side,
))]
// 8/7 with `py`: the six core args + the single `side` carrier. Folding the
// side channels into `side` is exactly what keeps this from ballooning as later
// stages add channels; the remaining args are the irreducible per-column core.
#[allow(clippy::too_many_arguments)]
pub fn golden_fused(
    py: Python<'_>,
    row_ids: PyArrowType<ArrayData>,
    cluster_ids: PyArrowType<ArrayData>,
    n_output_cols: usize,
    strategy_ids: Vec<u8>,
    text_cols: Vec<PyArrowType<ArrayData>>,
    code_cols: Vec<PyArrowType<ArrayData>>,
    side: GoldenFusedSideChannels,
) -> PyResult<GoldenFusedResult> {
    let GoldenFusedSideChannels {
        source_code,
        priority_codes,
        date_cols,
        date_null_masks,
        qweights,
        pair_edges,
        group_specs,
    } = side;
    // `row_ids` is validated (int64, right length) to enforce the caller's
    // (cluster_id, row_id) pre-sort contract, but Stage 0 does NOT read its
    // values: spans are formed from `cluster_ids` alone, and winner indices are
    // GLOBAL positions in the pre-sorted frame. Stage 8 (provenance) reads it to
    // map winner index -> source __row_id__.
    let row_data = row_ids.0;
    if row_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "golden_fused: row_ids must be int64, got {:?}",
            row_data.data_type()
        )));
    }
    let n_rows = Int64Array::from(row_data).len();

    let cl_data = cluster_ids.0;
    if cl_data.data_type() != &DataType::Int64 {
        return Err(PyValueError::new_err(format!(
            "golden_fused: cluster_ids must be int64, got {:?}",
            cl_data.data_type()
        )));
    }
    let cluster_ids = Int64Array::from(cl_data);
    if cluster_ids.len() != n_rows {
        return Err(PyValueError::new_err(format!(
            "golden_fused: cluster_ids length {} != row count {n_rows}",
            cluster_ids.len()
        )));
    }
    if strategy_ids.len() != n_output_cols {
        return Err(PyValueError::new_err(format!(
            "golden_fused: strategy_ids length {} != n_output_cols {n_output_cols}",
            strategy_ids.len()
        )));
    }
    if text_cols.len() != n_output_cols {
        return Err(PyValueError::new_err(format!(
            "golden_fused: text_cols length {} != n_output_cols {n_output_cols}",
            text_cols.len()
        )));
    }
    let text: Vec<StrCol> = text_cols
        .into_iter()
        .map(|p| StrCol::from_data(p.0))
        .collect::<PyResult<_>>()?;
    for (c, col) in text.iter().enumerate() {
        if col.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: text col {c} length {} != row count {n_rows}",
                col.len()
            )));
        }
    }
    // code_cols carries the per-column value-factorization (`_factorize_codes`):
    // one int64 code per row, `-1` = null. It is REQUIRED (one per output col) --
    // the universal short-circuit + majority/unanimous run on raw-value equality
    // (codes), never text. Copy each column's values into an owned `Vec<i64>` to
    // move into the detached closure (as with `cluster_vals`).
    if code_cols.len() != n_output_cols {
        return Err(PyValueError::new_err(format!(
            "golden_fused: code_cols length {} != n_output_cols {n_output_cols}",
            code_cols.len()
        )));
    }
    let mut code_vals: Vec<Vec<i64>> = Vec::with_capacity(n_output_cols);
    for (c, p) in code_cols.into_iter().enumerate() {
        let d = p.0;
        if d.data_type() != &DataType::Int64 {
            return Err(PyValueError::new_err(format!(
                "golden_fused: code col {c} must be int64, got {:?}",
                d.data_type()
            )));
        }
        let arr = Int64Array::from(d);
        if arr.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: code col {c} length {} != row count {n_rows}",
                arr.len()
            )));
        }
        code_vals.push(arr.values().to_vec());
    }

    // ── Stage 2 keys: source_priority + most_recent ─────────────────────────
    let any_source_priority = strategy_ids.contains(&STRAT_SOURCE_PRIORITY);

    // Read an Int64 arrow array into an owned Vec. A length-0 array yields an
    // empty Vec (the "column doesn't use this key" placeholder).
    fn read_i64(d: ArrayData, what: &str) -> PyResult<Vec<i64>> {
        if d.data_type() != &DataType::Int64 {
            return Err(PyValueError::new_err(format!(
                "golden_fused: {what} must be int64, got {:?}",
                d.data_type()
            )));
        }
        Ok(Int64Array::from(d).values().to_vec())
    }

    // Per-column side-channel Vecs are ALWAYS n_output_cols long (Python fills a
    // placeholder for columns that don't use a channel) -- validate all three
    // unconditionally so the per-column indexing below can't panic.
    for (name, len) in [
        ("priority_codes", priority_codes.len()),
        ("date_cols", date_cols.len()),
        ("date_null_masks", date_null_masks.len()),
        ("qweights", qweights.len()),
    ] {
        if len != n_output_cols {
            return Err(PyValueError::new_err(format!(
                "golden_fused: {name} length {len} != n_output_cols {n_output_cols}"
            )));
        }
    }

    // ── Stage 5: pre-read each field_group's spec into an owned GroupPrep
    // (Arrow reads must precede py.detach). Validate col indices in range, the
    // most_recent date arrays (len n_rows), and the anchor column index.
    let mut group_preps: Vec<GroupPrep> = Vec::with_capacity(group_specs.len());
    for (g, gs) in group_specs.into_iter().enumerate() {
        let col_indices: Vec<usize> = gs.col_indices.iter().map(|&c| c as usize).collect();
        for &c in &col_indices {
            if c >= n_output_cols {
                return Err(PyValueError::new_err(format!(
                    "golden_fused: group {g} col index {c} >= n_output_cols {n_output_cols}"
                )));
            }
        }
        let date = read_i64(gs.date_col.0, "group date col")?;
        let date_null = read_i64(gs.date_null_mask.0, "group date null mask")?;
        if gs.strategy == GROUP_MOST_RECENT && (date.len() != n_rows || date_null.len() != n_rows) {
            return Err(PyValueError::new_err(format!(
                "golden_fused: group {g} most_recent date arrays must be length {n_rows}"
            )));
        }
        if gs.strategy == GROUP_ANCHOR
            && (gs.anchor_col_index < 0 || gs.anchor_col_index as usize >= n_output_cols)
        {
            return Err(PyValueError::new_err(format!(
                "golden_fused: group {g} anchor col index {} out of range",
                gs.anchor_col_index
            )));
        }
        group_preps.push(GroupPrep {
            col_indices,
            strategy: gs.strategy,
            priority_codes: gs.priority_codes,
            date,
            date_null,
            anchor_col_index: gs.anchor_col_index,
            allow_fill: gs.allow_fill,
        });
    }
    let any_group_source = group_preps
        .iter()
        .any(|p| p.strategy == GROUP_SOURCE_PRIORITY);

    // source_code is a single shared column: required (len n_rows) when a scalar
    // source_priority column OR a source_priority GROUP exists (else an empty
    // placeholder array).
    let source_vals = read_i64(source_code.0, "source_code")?;
    if (any_source_priority || any_group_source) && source_vals.len() != n_rows {
        return Err(PyValueError::new_err(format!(
            "golden_fused: source_code length {} != row count {n_rows}",
            source_vals.len()
        )));
    }

    let mut date_vals: Vec<Vec<i64>> = Vec::with_capacity(n_output_cols);
    for (c, p) in date_cols.into_iter().enumerate() {
        let v = read_i64(p.0, "date col")?;
        if strategy_ids[c] == STRAT_MOST_RECENT && v.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: date col {c} length {} != row count {n_rows}",
                v.len()
            )));
        }
        date_vals.push(v);
    }
    let mut date_null_vals: Vec<Vec<i64>> = Vec::with_capacity(n_output_cols);
    for (c, p) in date_null_masks.into_iter().enumerate() {
        let v = read_i64(p.0, "date null mask")?;
        if strategy_ids[c] == STRAT_MOST_RECENT && v.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: date null mask {c} length {} != row count {n_rows}",
                v.len()
            )));
        }
        date_null_vals.push(v);
    }

    // qweights: per-column Float64 (len n_rows) when quality_scores was passed,
    // else an EMPTY array = "unweighted this column". An empty inner Vec below
    // maps to `weights: None` at the dispatch site, so quality_scores=None stays
    // byte-identical to the unweighted Stages 0-2.
    let mut qweight_vals: Vec<Vec<f64>> = Vec::with_capacity(n_output_cols);
    for (c, p) in qweights.into_iter().enumerate() {
        let d = p.0;
        if d.data_type() != &DataType::Float64 {
            return Err(PyValueError::new_err(format!(
                "golden_fused: qweight col {c} must be float64, got {:?}",
                d.data_type()
            )));
        }
        let v = Float64Array::from(d).values().to_vec();
        if !v.is_empty() && v.len() != n_rows {
            return Err(PyValueError::new_err(format!(
                "golden_fused: qweight col {c} length {} != row count {n_rows}",
                v.len()
            )));
        }
        qweight_vals.push(v);
    }

    let cluster_vals: Vec<i64> = cluster_ids.values().to_vec();

    Ok(py.detach(|| {
        // Group pre-sorted rows into contiguous per-cluster spans.
        let mut spans: Vec<(usize, usize, i64)> = Vec::new(); // (offset, size, cluster_id)
        let mut i = 0usize;
        while i < n_rows {
            let cid = cluster_vals[i];
            let start = i;
            while i < n_rows && cluster_vals[i] == cid {
                i += 1;
            }
            spans.push((start, i - start, cid));
        }

        let n_clusters = spans.len();
        let mut winner_idx: Vec<Vec<i64>> = (0..n_output_cols)
            .map(|_| Vec::with_capacity(n_clusters))
            .collect();
        let mut field_conf: Vec<Vec<f64>> = (0..n_output_cols)
            .map(|_| Vec::with_capacity(n_clusters))
            .collect();
        // One confidence per field_group per cluster (folded into the mean ONCE
        // by the Python caller; spec §8).
        let n_groups = group_preps.len();
        let mut group_conf: Vec<Vec<f64>> = (0..n_groups)
            .map(|_| Vec::with_capacity(n_clusters))
            .collect();
        let cluster_out: Vec<i64> = spans.iter().map(|&(_, _, c)| c).collect();

        // Bucket confidence_majority edges by cluster id, preserving per-cluster
        // insertion order (== pair_scores.items() order). Positions stay LOCAL.
        // Empty when no confidence_majority column exists.
        let mut edges_by_cluster: std::collections::HashMap<i64, Vec<(usize, usize, f64)>> =
            std::collections::HashMap::new();
        for (cid, a, b, s) in pair_edges {
            edges_by_cluster
                .entry(cid)
                .or_default()
                .push((a as usize, b as usize, s));
        }
        let no_edges: Vec<(usize, usize, f64)> = Vec::new();

        for &(off, size, cid) in &spans {
            // Resolve field_groups first: pin each group's lock-step winner index
            // per group column and record the group's single confidence. A group
            // column's winner index overrides the per-column scalar dispatch below.
            let mut group_col_idx: Vec<Option<i64>> = vec![None; n_output_cols];
            for (g, prep) in group_preps.iter().enumerate() {
                let (per_col, conf) = resolve_group(prep, off, size, &code_vals, &source_vals);
                for (k, &c) in prep.col_indices.iter().enumerate() {
                    group_col_idx[c] = Some(per_col[k]);
                }
                group_conf[g].push(conf);
            }

            for col in 0..n_output_cols {
                // Group-owned column: winner index came from the group pass; the
                // group's confidence is in group_conf, so push a placeholder here
                // (Python excludes group columns from the scalar-confidence sum).
                if let Some(gi) = group_col_idx[col] {
                    winner_idx[col].push(gi);
                    field_conf[col].push(0.0);
                    continue;
                }
                let non_null = span_non_null(&code_vals[col], off, size);
                // Per-column quality weights: an empty channel (quality_scores
                // was None) -> None -> the unweighted branch.
                let wcol: Option<&[f64]> = if qweight_vals[col].is_empty() {
                    None
                } else {
                    Some(&qweight_vals[col])
                };
                // Universal decisions first (all-null / all-agree), on codes.
                let (li, conf) = if let Some(sc) = universal_short_circuit(&non_null) {
                    sc
                } else {
                    match strategy_ids[col] {
                        STRAT_MOST_COMPLETE => most_complete(&text[col], &non_null, off, wcol),
                        STRAT_MAJORITY_VOTE => majority_vote(&non_null, off, wcol),
                        STRAT_SOURCE_PRIORITY => source_priority(
                            &source_vals,
                            &code_vals[col],
                            &priority_codes[col],
                            off,
                            size,
                        ),
                        STRAT_MOST_RECENT => most_recent(
                            &code_vals[col],
                            &date_vals[col],
                            &date_null_vals[col],
                            off,
                            size,
                        ),
                        STRAT_FIRST_NON_NULL => first_non_null(&non_null, off, wcol),
                        STRAT_LONGEST_VALUE => longest_value(&text[col], &non_null, off, wcol),
                        STRAT_UNANIMOUS_OR_NULL => unanimous_or_null(&non_null),
                        STRAT_CONFIDENCE_MAJORITY => {
                            // Per-cluster edges (shared across this cluster's
                            // confidence_majority columns); absent -> majority
                            // fallback inside confidence_majority.
                            let edges = edges_by_cluster.get(&cid).unwrap_or(&no_edges);
                            confidence_majority(&code_vals[col], &non_null, edges, off, wcol)
                        }
                        // Unknown strategy id: Python declines before reaching
                        // here; null sentinel defensively.
                        _ => (-1i64, 0.0),
                    }
                };
                let global = if li < 0 { -1 } else { off as i64 + li };
                winner_idx[col].push(global);
                field_conf[col].push(conf);
            }
        }
        (winner_idx, field_conf, group_conf, cluster_out)
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Array, StringArray};

    fn strcol(vals: &[Option<&str>]) -> StrCol {
        StrCol::from_data(StringArray::from(vals.to_vec()).into_data()).unwrap()
    }

    /// (local_idx, code) for the non-null members of `[off, off+size)`.
    fn nn(code: &[i64], off: usize, size: usize) -> Vec<(usize, i64)> {
        span_non_null(code, off, size)
    }

    // ── universal short-circuit ──────────────────────────────────────────────

    #[test]
    fn short_circuit_all_null_is_sentinel() {
        assert_eq!(
            universal_short_circuit(&nn(&[-1, -1], 0, 2)),
            Some((-1, 0.0))
        );
    }

    #[test]
    fn short_circuit_all_agree_is_first_conf_1() {
        // codes [7,7] (raw-equal) -> first non-null local idx 0, conf 1.0.
        assert_eq!(universal_short_circuit(&nn(&[7, 7], 0, 2)), Some((0, 1.0)));
    }

    #[test]
    fn short_circuit_disagree_declines_to_dispatch() {
        assert_eq!(universal_short_circuit(&nn(&[0, 1], 0, 2)), None);
    }

    // ── most_complete ────────────────────────────────────────────────────────

    #[test]
    fn most_complete_unique_longest_conf_1() {
        // ["Bob","Robert","Bob"] -> "Robert" unique longest -> local idx 1, conf 1.0
        let col = strcol(&[Some("Bob"), Some("Robert"), Some("Bob")]);
        assert_eq!(
            most_complete(&col, &nn(&[0, 1, 0], 0, 3), 0, None),
            (1, 1.0)
        );
    }

    #[test]
    fn most_complete_length_tie_first_in_order_conf_07() {
        // "aa","bb" tie at length 2 -> first, conf 0.7
        let col = strcol(&[Some("aa"), Some("bb")]);
        assert_eq!(most_complete(&col, &nn(&[0, 1], 0, 2), 0, None), (0, 0.7));
    }

    #[test]
    fn most_complete_respects_span_offset() {
        // span [2,4): "z","zzz" -> local idx 1, conf 1.0
        let col = strcol(&[Some("a"), Some("a"), Some("z"), Some("zzz")]);
        assert_eq!(
            most_complete(&col, &nn(&[0, 0, 1, 2], 2, 2), 2, None),
            (1, 1.0)
        );
    }

    #[test]
    fn most_complete_skips_null_members() {
        // "a", null, "bbb" -> "bbb" unique longest at local idx 2, conf 1.0.
        let col = strcol(&[Some("a"), None, Some("bbb")]);
        assert_eq!(
            most_complete(&col, &nn(&[0, -1, 1], 0, 3), 0, None),
            (2, 1.0)
        );
    }

    // ── most_complete weighted tie-break (Stage 3) ───────────────────────────

    #[test]
    fn most_complete_weighted_tie_highest_weight_conf_scaled() {
        // "aa","bb" length tie; weights [0.5, 0.9] -> pick local 1 (higher weight),
        // conf = min(1.0, 0.7*0.9) = 0.63.
        let col = strcol(&[Some("aa"), Some("bb")]);
        let w = [0.5f64, 0.9];
        assert_eq!(
            most_complete(&col, &nn(&[0, 1], 0, 2), 0, Some(&w)),
            (1, 0.7 * 0.9)
        );
    }

    #[test]
    fn most_complete_weighted_tie_conf_clamped_to_1() {
        // weight 2.0 -> 0.7*2.0 = 1.4 clamps to 1.0. Tie -> higher-weight local 1.
        let col = strcol(&[Some("aa"), Some("bb")]);
        let w = [1.0f64, 2.0];
        assert_eq!(
            most_complete(&col, &nn(&[0, 1], 0, 2), 0, Some(&w)),
            (1, 1.0)
        );
    }

    #[test]
    fn most_complete_weighted_unique_longest_ignores_weight() {
        // Unique longest wins at conf 1.0 regardless of a lower weight.
        let col = strcol(&[Some("Bob"), Some("Robert")]);
        let w = [0.9f64, 0.1];
        assert_eq!(
            most_complete(&col, &nn(&[0, 1], 0, 2), 0, Some(&w)),
            (1, 1.0)
        );
    }

    #[test]
    fn most_complete_weighted_tie_first_max_on_weight_tie() {
        // Equal weights on a length tie -> first-in-order (local 0) via first-max.
        let col = strcol(&[Some("aa"), Some("bb")]);
        let w = [0.8f64, 0.8];
        assert_eq!(
            most_complete(&col, &nn(&[0, 1], 0, 2), 0, Some(&w)),
            (0, 0.7 * 0.8)
        );
    }

    // ── longest_value (tie conf 0.5, else 1.0) ───────────────────────────────

    #[test]
    fn longest_value_unique_longest_conf_1() {
        let col = strcol(&[Some("z"), Some("zzz")]);
        assert_eq!(longest_value(&col, &nn(&[0, 1], 0, 2), 0, None), (1, 1.0));
    }

    #[test]
    fn longest_value_length_tie_first_conf_05() {
        let col = strcol(&[Some("aa"), Some("bb")]);
        assert_eq!(longest_value(&col, &nn(&[0, 1], 0, 2), 0, None), (0, 0.5));
    }

    #[test]
    fn longest_value_weighted_tie_conf_flat_07() {
        // The DIVERGENCE: longest_value's weighted tie is a FLAT 0.7, NOT
        // min(1.0, 0.7*w) like most_complete. weights [0.5, 0.9] -> local 1, 0.7.
        let col = strcol(&[Some("aa"), Some("bb")]);
        let w = [0.5f64, 0.9];
        assert_eq!(
            longest_value(&col, &nn(&[0, 1], 0, 2), 0, Some(&w)),
            (1, 0.7)
        );
    }

    // ── majority_vote ────────────────────────────────────────────────────────

    #[test]
    fn majority_vote_clear_winner() {
        // codes [x,x,y] -> x wins 2/3 at first-occurrence idx 0.
        assert_eq!(
            majority_vote(&nn(&[5, 5, 9], 0, 3), 0, None),
            (0, 2.0 / 3.0)
        );
    }

    #[test]
    fn majority_vote_count_tie_first_appearance() {
        // codes [a,b,a,b] tie 2/2 -> first-appearance code `a` at idx 0, conf 0.5.
        assert_eq!(majority_vote(&nn(&[3, 8, 3, 8], 0, 4), 0, None), (0, 0.5));
    }

    #[test]
    fn majority_vote_weighted_beats_count_majority() {
        // codes [a,a,b]: count-majority = a (2/3). Weights [0.1,0.1,0.9] flip it:
        // weight-sums a=0.2, b=0.9 -> winner b at local 2, conf 0.9/1.1.
        let w = [0.1f64, 0.1, 0.9];
        let (idx, conf) = majority_vote(&nn(&[0, 0, 1], 0, 3), 0, Some(&w));
        assert_eq!(idx, 2);
        assert!((conf - 0.9 / 1.1).abs() < 1e-12);
    }

    #[test]
    fn majority_vote_weighted_tie_first_appearance() {
        // codes [a,b]; equal weights -> weight-sum tie -> first-appearance a at
        // local 0, conf 0.5.
        let w = [0.5f64, 0.5];
        let (idx, conf) = majority_vote(&nn(&[0, 1], 0, 2), 0, Some(&w));
        assert_eq!(idx, 0);
        assert!((conf - 0.5).abs() < 1e-12);
    }

    // ── unanimous_or_null ────────────────────────────────────────────────────

    #[test]
    fn unanimous_or_null_disagree_is_sentinel() {
        assert_eq!(unanimous_or_null(&nn(&[0, 1], 0, 2)), (-1, 0.0));
    }

    #[test]
    fn unanimous_or_null_agree_conf_1() {
        assert_eq!(unanimous_or_null(&nn(&[4, 4], 0, 2)), (0, 1.0));
    }

    // ── first_non_null ───────────────────────────────────────────────────────

    #[test]
    fn first_non_null_leading_null_picks_first_present() {
        // null, "b", "c" -> first present at local idx 1, conf 0.6.
        assert_eq!(first_non_null(&nn(&[-1, 1, 2], 0, 3), 0, None), (1, 0.6));
    }

    #[test]
    fn first_non_null_weighted_picks_highest_weight() {
        // non-null locals 1,2 with weights [_, 0.3, 0.8] -> highest-weight local 2,
        // conf 0.6 (weights don't change the confidence).
        let w = [0.0f64, 0.3, 0.8];
        assert_eq!(
            first_non_null(&nn(&[-1, 1, 2], 0, 3), 0, Some(&w)),
            (2, 0.6)
        );
    }

    #[test]
    fn first_non_null_weighted_tie_first_max() {
        // Equal weights -> first-max keeps the first non-null (local 0).
        let w = [0.5f64, 0.5];
        assert_eq!(first_non_null(&nn(&[0, 1], 0, 2), 0, Some(&w)), (0, 0.6));
    }

    // ── source_priority ──────────────────────────────────────────────────────

    #[test]
    fn source_priority_top_priority_wins() {
        // sources [A=0, B=1], values [x, y]; priority [B, A] -> B (code 1) first
        // occurrence at local 1, idx 0 in priority -> conf 1.0.
        let src = [0i64, 1];
        let val = [10i64, 20];
        assert_eq!(source_priority(&src, &val, &[1, 0], 0, 2), (1, 1.0));
    }

    #[test]
    fn source_priority_null_top_source_falls_through() {
        // First row of the TOP-priority source has a null value -> skip it, next
        // priority wins. sources [A=0, B=1]; values [null, 20]; priority [A, B]:
        // A's first value is null -> B wins at local 1, idx 1 -> conf 0.9.
        let src = [0i64, 1];
        let val = [-1i64, 20];
        assert_eq!(source_priority(&src, &val, &[0, 1], 0, 2), (1, 0.9));
    }

    #[test]
    fn source_priority_absent_source_skipped() {
        // priority [absent(-1), A(0)] -> absent skipped, A wins at idx 1 conf 0.9.
        let src = [0i64, 1];
        let val = [10i64, 20];
        assert_eq!(source_priority(&src, &val, &[-1, 0], 0, 2), (0, 0.9));
    }

    #[test]
    fn source_priority_first_occurrence_per_source() {
        // Two rows of source A (code 0): only the FIRST (local 0) is recorded.
        // sources [A, A, B]; values [10, 99, 20]; priority [A] -> local 0.
        let src = [0i64, 0, 1];
        let val = [10i64, 99, 20];
        assert_eq!(source_priority(&src, &val, &[0], 0, 3), (0, 1.0));
    }

    #[test]
    fn source_priority_no_match_is_sentinel() {
        // priority holds only an absent source -> no winner.
        let src = [0i64, 1];
        let val = [10i64, 20];
        assert_eq!(source_priority(&src, &val, &[-1], 0, 2), (-1, 0.0));
    }

    #[test]
    fn source_priority_conf_floor_01() {
        // 11th priority position -> 1.0 - 10*0.1 = 0.0, floored to 0.1.
        let src = [0i64];
        let val = [10i64];
        let prio: Vec<i64> = (0..10).map(|_| -1).chain(std::iter::once(0)).collect();
        assert_eq!(source_priority(&src, &val, &prio, 0, 1), (0, 0.1));
    }

    // ── most_recent ──────────────────────────────────────────────────────────

    #[test]
    fn most_recent_picks_latest() {
        // dates [1, 3, 2], all values present, no nulls -> local 1 (date 3), conf 1.0.
        let val = [10i64, 20, 30];
        let date = [1i64, 3, 2];
        let mask = [0i64, 0, 0];
        assert_eq!(most_recent(&val, &date, &mask, 0, 3), (1, 1.0));
    }

    #[test]
    fn most_recent_top_date_tie_first_occurrence_conf_05() {
        // dates [3, 3, 1]; two rows share the top date 3 -> first (local 0), conf 0.5.
        let val = [10i64, 20, 30];
        let date = [3i64, 3, 1];
        let mask = [0i64, 0, 0];
        assert_eq!(most_recent(&val, &date, &mask, 0, 3), (0, 0.5));
    }

    #[test]
    fn most_recent_drops_null_date_and_null_value() {
        // local 0: null date (dropped). local 1: null value (dropped). local 2:
        // eligible date 5. -> local 2, conf 1.0.
        let val = [10i64, -1, 30];
        let date = [9i64, 8, 5];
        let mask = [1i64, 0, 0];
        assert_eq!(most_recent(&val, &date, &mask, 0, 3), (2, 1.0));
    }

    #[test]
    fn most_recent_none_eligible_is_sentinel() {
        // all rows have null date -> no eligible row.
        let val = [10i64, 20];
        let date = [1i64, 2];
        let mask = [1i64, 1];
        assert_eq!(most_recent(&val, &date, &mask, 0, 2), (-1, 0.0));
    }

    #[test]
    fn most_recent_negative_epoch_ordering() {
        // Negative epoch values order correctly (the reason for an explicit mask,
        // not a sentinel): dates [-10, -3, -20] -> latest is -3 at local 1.
        let val = [10i64, 20, 30];
        let date = [-10i64, -3, -20];
        let mask = [0i64, 0, 0];
        assert_eq!(most_recent(&val, &date, &mask, 0, 3), (1, 1.0));
    }

    // ── confidence_majority ──────────────────────────────────────────────────

    #[test]
    fn confidence_majority_strong_minority_beats_weak_majority() {
        // codes: X=0 (locals 0,1,2), Y=1 (locals 3,4). X edges sum 0.3, Y edge
        // 0.91 -> Y (the 2-member minority) wins. rep = first agreeing Y edge's
        // first endpoint (local 3). conf = 0.91 / (0.3 + 0.91).
        let code = [0i64, 0, 0, 1, 1];
        let edges = [(0, 1, 0.1), (1, 2, 0.1), (0, 2, 0.1), (3, 4, 0.91)];
        let (idx, conf) = confidence_majority(&code, &nn(&code, 0, 5), &edges, 0, None);
        assert_eq!(idx, 3);
        assert!((conf - 0.91 / 1.21).abs() < 1e-12);
    }

    #[test]
    fn confidence_majority_empty_edges_falls_back_to_majority() {
        // No edges -> count-majority. codes [A,B,A] -> A wins 2/3 at local 0.
        let code = [0i64, 1, 0];
        let (idx, conf) = confidence_majority(&code, &nn(&code, 0, 3), &[], 0, None);
        assert_eq!(idx, 0);
        assert!((conf - 2.0 / 3.0).abs() < 1e-12);
    }

    #[test]
    fn confidence_majority_no_agreeing_edges_falls_back_to_majority() {
        // Both edges span DISAGREEING codes (A-B, B-A) -> no value accrues weight
        // -> count-majority fallback: A wins 2/3 at local 0.
        let code = [0i64, 1, 0];
        let edges = [(0, 1, 0.9), (1, 2, 0.8)];
        let (idx, conf) = confidence_majority(&code, &nn(&code, 0, 3), &edges, 0, None);
        assert_eq!(idx, 0);
        assert!((conf - 2.0 / 3.0).abs() < 1e-12);
    }

    #[test]
    fn confidence_majority_representative_is_first_endpoint_of_first_agreeing_edge() {
        // Winning code X=0 (locals 0,1,2). First agreeing X edge in order is
        // (2,1) -> rep = first endpoint local 2 (NOT min(2,1)=1). Y=1 edge weaker.
        let code = [0i64, 0, 0, 1, 1];
        let edges = [(2, 1, 0.5), (0, 2, 0.4), (3, 4, 0.2)];
        let (idx, conf) = confidence_majority(&code, &nn(&code, 0, 5), &edges, 0, None);
        assert_eq!(idx, 2);
        assert!((conf - 0.9 / 1.1).abs() < 1e-12);
    }

    #[test]
    fn confidence_majority_skips_null_endpoint_edges() {
        // Edge (0,1) touches a null endpoint (local 1 code -1) -> skipped. Edge
        // (0,2) agrees on code 0 -> winner local 0, sole weight 0.7 -> conf 1.0.
        let code = [0i64, -1, 0];
        let edges = [(0, 1, 0.9), (0, 2, 0.7)];
        let (idx, conf) = confidence_majority(&code, &nn(&code, 0, 3), &edges, 0, None);
        assert_eq!(idx, 0);
        assert!((conf - 1.0).abs() < 1e-12);
    }

    #[test]
    fn confidence_majority_respects_span_offset() {
        // Span [2,7): codes X=0 (locals 0,1,2), Y=1 (locals 3,4) at global off 2.
        // Y edge (3,4) strong -> rep local 3. Positions/edges are LOCAL; off maps
        // code lookups to the right global rows.
        let code = [9i64, 9, 0, 0, 0, 1, 1];
        let edges = [(0, 1, 0.1), (3, 4, 0.9)];
        let (idx, conf) = confidence_majority(&code, &nn(&code, 2, 5), &edges, 2, None);
        assert_eq!(idx, 3);
        assert!((conf - 0.9 / 1.0).abs() < 1e-12);
    }

    // ── field_groups / resolve_group (Stage 5) ───────────────────────────────

    fn gprep(
        col_indices: &[usize],
        strategy: u8,
        priority_codes: &[i64],
        date: &[i64],
        date_null: &[i64],
        anchor_col_index: i64,
        allow_fill: bool,
    ) -> GroupPrep {
        GroupPrep {
            col_indices: col_indices.to_vec(),
            strategy,
            priority_codes: priority_codes.to_vec(),
            date: date.to_vec(),
            date_null: date_null.to_vec(),
            anchor_col_index,
            allow_fill,
        }
    }

    #[test]
    fn group_most_complete_lockstep_winner_and_conf() {
        // 2 cols (a=code_vals[0], b=code_vals[1]), 2 rows. row0 populated 2,
        // row1 populated 1 -> winner row0. Both columns pin to global off+0.
        // base = 2/2 = 1.0, no tie.
        let a = vec![10i64, 20];
        let b = vec![30i64, -1]; // row1 has null b
        let code_vals = vec![a, b];
        let prep = gprep(&[0, 1], GROUP_MOST_COMPLETE, &[], &[], &[], -1, false);
        let (idx, conf) = resolve_group(&prep, 0, 2, &code_vals, &[]);
        assert_eq!(idx, vec![0, 0]); // both columns -> winner row0
        assert!((conf - 1.0).abs() < 1e-12);
    }

    #[test]
    fn group_most_complete_tie_conf_scaled_070() {
        // Both rows fully populated -> tie, winner = row0 (first). base 2/2, x0.7.
        let code_vals = vec![vec![1i64, 2], vec![3i64, 4]];
        let prep = gprep(&[0, 1], GROUP_MOST_COMPLETE, &[], &[], &[], -1, false);
        let (idx, conf) = resolve_group(&prep, 0, 2, &code_vals, &[]);
        assert_eq!(idx, vec![0, 0]);
        assert!((conf - 0.7).abs() < 1e-12);
    }

    #[test]
    fn group_allow_fill_backfills_from_next_best_row() {
        // 3 rows, all populated=1 (tie) -> winner row0. col a non-null on row0;
        // col b null on row0 -> back-fill from the first next-best row holding b
        // (row2). winner_populated 1 + n_filled 1 = 2/2 = 1.0, x0.7 (tie) -> 0.7.
        let a = vec![10i64, 11, -1];
        let b = vec![-1i64, -1, 22];
        let code_vals = vec![a, b];
        let prep = gprep(&[0, 1], GROUP_MOST_COMPLETE, &[], &[], &[], -1, true);
        let (idx, conf) = resolve_group(&prep, 0, 3, &code_vals, &[]);
        assert_eq!(idx, vec![0, 2]); // a from winner row0, b filled from row2
        assert!((conf - 0.7).abs() < 1e-12);
    }

    #[test]
    fn group_allow_fill_no_donor_keeps_winner_null_not_counted() {
        // col b null everywhere -> no donor; b stays winner row0 (null), NOT
        // counted as filled. winner_populated (a only) = 1, n_filled 0 -> 1/2,
        // x0.7 on the populated tie -> 0.35.
        let a = vec![10i64, 11];
        let b = vec![-1i64, -1];
        let code_vals = vec![a, b];
        let prep = gprep(&[0, 1], GROUP_MOST_COMPLETE, &[], &[], &[], -1, true);
        let (idx, conf) = resolve_group(&prep, 0, 2, &code_vals, &[]);
        assert_eq!(idx, vec![0, 0]);
        assert!((conf - (0.5 * 0.7)).abs() < 1e-12);
    }

    #[test]
    fn group_source_priority_ranks_and_pins() {
        // sources row0=crm(0), row1=web(1). priority [web, crm] -> codes [1, 0].
        // web ranks first -> winner row1. Both cols pin to row1.
        let code_vals = vec![vec![10i64, 20], vec![30i64, 40]];
        let src = [0i64, 1];
        let prep = gprep(&[0, 1], GROUP_SOURCE_PRIORITY, &[1, 0], &[], &[], -1, false);
        let (idx, conf) = resolve_group(&prep, 0, 2, &code_vals, &src);
        assert_eq!(idx, vec![1, 1]);
        assert!((conf - 1.0).abs() < 1e-12); // both cols populated on winner
    }

    #[test]
    fn group_most_recent_ranks_by_date() {
        // dates [100, 305], no nulls -> winner = row1 (latest). Both cols pin row1.
        let code_vals = vec![vec![10i64, 20], vec![30i64, 40]];
        let date = [100i64, 305];
        let mask = [0i64, 0];
        let prep = gprep(&[0, 1], GROUP_MOST_RECENT, &[], &date, &mask, -1, false);
        let (idx, conf) = resolve_group(&prep, 0, 2, &code_vals, &[]);
        assert_eq!(idx, vec![1, 1]);
        assert!((conf - 1.0).abs() < 1e-12);
    }

    #[test]
    fn group_anchor_present_row_wins() {
        // anchor = col 0 (a). row0 a null (anchor absent), row1 a present ->
        // winner row1 (anchor-present ranks first). tie False. base 2/2.
        let a = vec![-1i64, 20];
        let b = vec![30i64, 40];
        let code_vals = vec![a, b];
        let prep = gprep(&[0, 1], GROUP_ANCHOR, &[], &[], &[], 0, false);
        let (idx, conf) = resolve_group(&prep, 0, 2, &code_vals, &[]);
        assert_eq!(idx, vec![1, 1]);
        assert!((conf - 1.0).abs() < 1e-12);
    }

    #[test]
    fn group_respects_span_offset() {
        // Span [2,4): 2 rows at global off 2. row0(local) populated 2, row1
        // populated 1 -> winner local 0 -> global 2. Both cols pin global 2.
        let a = vec![9i64, 9, 10, 20];
        let b = vec![9i64, 9, 30, -1];
        let code_vals = vec![a, b];
        let prep = gprep(&[0, 1], GROUP_MOST_COMPLETE, &[], &[], &[], -1, false);
        let (idx, conf) = resolve_group(&prep, 2, 2, &code_vals, &[]);
        assert_eq!(idx, vec![2, 2]); // global = off(2) + local(0)
        assert!((conf - 1.0).abs() < 1e-12);
    }
}
