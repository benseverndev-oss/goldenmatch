//! Fellegi–Sunter EM training — the pure-numeric heart, pyo3-free / Arrow-free.
//!
//! This is PR-C / C1 of the "FS Rust+Arrow-only" epic
//! (`docs/superpowers/plans/2026-07-18-fs-rust-arrow-only.md`,
//! `docs/superpowers/specs/2026-07-18-fs-em-rust-arrow-design.md`). It is a
//! byte-parity port of the discrete E/M loop in
//! `packages/python/goldenmatch/goldenmatch/core/probabilistic.py::train_em`
//! (the numpy trainer). It takes ALREADY-BINNED comparison vectors (integer
//! levels; `-1` = unobserved) — sampling + binning stay in Python this phase —
//! and returns the trained m/u probabilities and `log2(m/u)` match weights.
//!
//! Reproduced exactly (same constants + accumulation order as the Python):
//!   * u estimated from random pairs, `(count + 1e-6) / (observed + n_levels*1e-6)`;
//!   * `always_conditioned` = configured blocking field (#1836) OR a field that is
//!     conditioned out of EVERY blocked training pair — such fields take the
//!     bounded fixed prior (neutral u, linear `-3..+3` weights) and are skipped
//!     by the EM update;
//!   * m initialised with the exponential prior `2^k`, EM updates m only (u fixed),
//!     E-step posterior via a stabilised softmax, M-step re-estimate with `1e-6`
//!     smoothing, convergence on the max m-delta over learnable fields;
//!   * weights `log2(max(m,1e-10)/max(u,1e-10))` for learnable fields.
//!
//! NOT yet ported (later C1/C2 slices, tracked in the design doc): negative-evidence
//! dims, TF (Winkler) tables, the monotonicity guard, missing-value modes, and the
//! two-table linkage sampling. Those are additive and do not change this core.
//!
//! # The counted (distributed) entry points
//!
//! Phase 0 of `docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md`
//! adds [`train_em_from_counts`], [`estimate_u_from_counts`] and
//! [`combine_em_sessions`] — the shape distributed training produces, where
//! identical comparison vectors have already been collapsed to one row carrying a
//! count. The E-step reads only the vector, so those rows are interchangeable and
//! every M-step quantity is a sum linear in the count: aggregating is EXACT, not a
//! sample.
//!
//! **These have no callers yet.** Phase 1 wires them over the C-ABI / wasm / JNI
//! so Python and TypeScript stop carrying their own copies of this loop. Until
//! then `tests/em_counts_parity.rs` is the only thing keeping this file honest,
//! and it is what found two divergences on its first run: Python was reporting
//! `log2(m/u)` where a conditioned field must take the bounded ramp, and was
//! reporting the random-pair `u` where it must take the neutral prior. Both
//! produced entirely plausible models.

/// A comparison field's shape for EM. `is_blocking` marks a configured blocking
/// field, which — mirroring the shipped #1836 posture — always takes the fixed
/// prior regardless of per-pair conditioning.
#[derive(Debug, Clone)]
pub struct EmField {
    pub n_levels: usize,
    pub is_blocking: bool,
}

/// EM stopping controls (mirror `train_em`'s defaults at the call site).
#[derive(Debug, Clone)]
pub struct EmParams {
    pub max_iterations: usize,
    pub convergence: f64,
}

impl Default for EmParams {
    fn default() -> Self {
        Self {
            max_iterations: 20,
            convergence: 0.001,
        }
    }
}

/// Trained Fellegi–Sunter parameters (the numeric subset of the Python `EMResult`).
#[derive(Debug, Clone, PartialEq)]
pub struct EmOutput {
    pub m_probs: Vec<Vec<f64>>,
    pub u_probs: Vec<Vec<f64>>,
    pub match_weights: Vec<Vec<f64>>,
    pub converged: bool,
    pub iterations: usize,
    pub proportion_matched: f64,
}

const SMOOTH: f64 = 1e-6;
const FLOOR: f64 = 1e-10;
const P_MATCH_INIT: f64 = 0.02;

fn neutral_u(n_levels: usize) -> Vec<f64> {
    match n_levels {
        2 => vec![0.5, 0.5],
        3 => vec![0.34, 0.33, 0.33],
        n => vec![1.0 / n as f64; n],
    }
}

/// The bounded `-3..+3` ramp a conditioned (blocking) field's weights take.
///
/// The other half of the #1835 prior; [`neutral_u`] supplies the `u` side. A
/// conditioned field's `m` is never updated -- it keeps the exponential
/// initialisation -- so `log2(m/u)` for one of these is the ratio of an
/// arbitrary init to a random-pair estimate. It is not a quantity, and it lands
/// in the model looking exactly like every other weight.
///
/// The ramp supplies both properties that estimate would destroy: a real
/// DISAGREEMENT PENALTY at the bottom (drives precision) and an agreement weight
/// BOUNDED at `+3` (preserves recall, by denying a near-unique key the 20-plus
/// bits that let it dominate every other field).
pub fn fixed_blocking_weights(n_levels: usize) -> Vec<f64> {
    if n_levels <= 1 {
        return vec![3.0];
    }
    (0..n_levels)
        .map(|k| -3.0 + 6.0 * k as f64 / (n_levels - 1) as f64)
        .collect()
}

/// `u` from COUNTED comparison vectors over random pairs.
///
/// `patterns[i]` is one distinct comparison vector and `counts[i]` how many
/// random pairs had it. Mirrors the Python `estimate_u_from_counts`:
/// `(count + 1e-6) / (observed + n_levels * 1e-6)`, with `observed` excluding
/// unobserved (`-1`) entries -- a field null on one side is missing data, not a
/// disagreement, and putting those pairs in the denominator would shrink every
/// level of a sparse field and inflate its `log2(m/u)` in proportion to how
/// little data supports it.
pub fn estimate_u_from_counts(
    fields: &[EmField],
    patterns: &[Vec<i32>],
    counts: &[f64],
) -> Vec<Vec<f64>> {
    fields
        .iter()
        .enumerate()
        .map(|(j, f)| {
            let mut level_counts = vec![0.0f64; f.n_levels];
            let mut observed = 0.0f64;
            for (row, &c) in patterns.iter().zip(counts) {
                let lvl = row[j];
                if lvl >= 0 {
                    observed += c;
                    let l = lvl as usize;
                    if l < f.n_levels {
                        level_counts[l] += c;
                    }
                }
            }
            let total = observed + f.n_levels as f64 * SMOOTH;
            level_counts.iter().map(|c| (c + SMOOTH) / total).collect()
        })
        .collect()
}

/// Train from COUNTED comparison vectors -- the distributed shape.
///
/// The E-step reads only the comparison vector, so pairs sharing one are
/// interchangeable and every M-step quantity is a sum linear in how many pairs
/// share it. Identical rows therefore collapse to one row carrying a count, and
/// the count multiplies through every sum. **Exact, not a sample.**
///
/// `conditioned` is per-FIELD, not per-row: within one blocking pass the
/// conditioning is constant, which is exactly why the counts need no pass
/// column and can be produced by a `GROUP BY` on a cluster.
///
/// `u_probs` is required rather than derived here: `u` comes from RANDOM
/// (unblocked) pairs, which this function never sees.
pub fn train_em_from_counts(
    fields: &[EmField],
    patterns: &[Vec<i32>],
    counts: &[f64],
    u_probs: &[Vec<f64>],
    conditioned: &[bool],
    params: &EmParams,
) -> EmOutput {
    let nf = fields.len();
    let nrows = patterns.len();
    let total_weight: f64 = counts.iter().sum();

    let always: Vec<bool> = (0..nf)
        .map(|j| fields[j].is_blocking || conditioned[j])
        .collect();

    let mut u_probs: Vec<Vec<f64>> = u_probs.to_vec();
    for j in 0..nf {
        if always[j] {
            u_probs[j] = neutral_u(fields[j].n_levels);
        }
    }

    let mut m_probs: Vec<Vec<f64>> = fields
        .iter()
        .map(|f| {
            let raw: Vec<f64> = (0..f.n_levels).map(|k| (1u64 << k) as f64).collect();
            let s: f64 = raw.iter().sum();
            raw.iter().map(|r| r / s).collect()
        })
        .collect();

    let mut p_match = P_MATCH_INIT;
    let mut converged = false;
    let mut iterations = 0usize;

    for it in 0..params.max_iterations {
        iterations = it + 1;
        let old_m = m_probs.clone();

        // E-step, then weight each row's posterior by its count.
        let mut wp = vec![0.0f64; nrows];
        for i in 0..nrows {
            let mut log_m = 0.0f64;
            let mut log_u = 0.0f64;
            for j in 0..nf {
                let lvl = patterns[i][j];
                if lvl >= 0 && !conditioned[j] {
                    let l = lvl as usize;
                    log_m += m_probs[j][l].max(FLOOR).ln();
                    log_u += u_probs[j][l].max(FLOOR).ln();
                }
            }
            let log_match = p_match.max(FLOOR).ln() + log_m;
            let log_non = (1.0 - p_match).max(FLOOR).ln() + log_u;
            let mx = log_match.max(log_non);
            let e_m = (log_match - mx).exp();
            let e_n = (log_non - mx).exp();
            wp[i] = (e_m / (e_m + e_n)) * counts[i];
        }

        let total_match: f64 = wp.iter().sum();
        p_match = (total_match / total_weight).max(SMOOTH);

        for j in 0..nf {
            if always[j] {
                continue;
            }
            let nl = fields[j].n_levels;
            let mut elig_match = 0.0f64;
            for i in 0..nrows {
                if patterns[i][j] >= 0 {
                    elig_match += wp[i];
                }
            }
            let mut new_m = vec![0.0f64; nl];
            for (lvl, slot) in new_m.iter_mut().enumerate() {
                let mut s = 0.0f64;
                for i in 0..nrows {
                    if patterns[i][j] >= 0 && patterns[i][j] as usize == lvl {
                        s += wp[i];
                    }
                }
                *slot = (s + SMOOTH) / (elig_match + nl as f64 * SMOOTH);
            }
            m_probs[j] = new_m;
        }

        let mut max_delta = 0.0f64;
        for j in 0..nf {
            if always[j] {
                continue;
            }
            for k in 0..fields[j].n_levels {
                max_delta = max_delta.max((m_probs[j][k] - old_m[j][k]).abs());
            }
        }
        if max_delta < params.convergence {
            converged = true;
            break;
        }
    }

    let match_weights = weights_from(fields, &always, &m_probs, &u_probs);
    EmOutput {
        m_probs,
        u_probs,
        match_weights,
        converged,
        iterations,
        proportion_matched: p_match,
    }
}

fn weights_from(
    fields: &[EmField],
    always: &[bool],
    m_probs: &[Vec<f64>],
    u_probs: &[Vec<f64>],
) -> Vec<Vec<f64>> {
    (0..fields.len())
        .map(|j| {
            if always[j] {
                fixed_blocking_weights(fields[j].n_levels)
            } else {
                (0..fields[j].n_levels)
                    .map(|k| (m_probs[j][k].max(FLOOR) / u_probs[j][k].max(FLOOR)).log2())
                    .collect()
            }
        })
        .collect()
}

/// One trained per-pass session: which fields its pass conditioned, its result,
/// and how many pairs it saw.
#[derive(Debug, Clone)]
pub struct EmSession {
    pub conditioned: Vec<bool>,
    pub out: EmOutput,
    pub weight: f64,
}

/// Combine independent per-pass sessions into one model.
///
/// `m` is the pair-weighted mean over the sessions that COULD estimate the field
/// (those whose pass does not block on it), weighted because a pass contributing
/// 10 pairs and one contributing 10,000 are not equal evidence.
///
/// `u` is neutralised for the UNION of every pass's blocking fields. Taking one
/// session's `u` wholesale is wrong even though all sessions share a random-pair
/// sample: each neutralises only its OWN blocking fields, so lifting the vector
/// from session 0 leaves every other pass's blocking field carrying the
/// random-pair estimate and re-opens #1835 for it.
pub fn combine_em_sessions(fields: &[EmField], sessions: &[EmSession]) -> EmOutput {
    let nf = fields.len();
    let base = &sessions[0].out;

    let union: Vec<bool> = (0..nf)
        .map(|j| {
            sessions
                .iter()
                .any(|s| s.conditioned[j] || fields[j].is_blocking)
        })
        .collect();

    let mut m_probs: Vec<Vec<f64>> = Vec::with_capacity(nf);
    for (j, field) in fields.iter().enumerate() {
        let estimable: Vec<&EmSession> = sessions
            .iter()
            .filter(|s| !s.conditioned[j] && !field.is_blocking)
            .collect();
        if estimable.is_empty() {
            // No pass leaves this field free, so every session fell back to the
            // fixed prior and they agree; the first is as good as any.
            m_probs.push(base.m_probs[j].clone());
            continue;
        }
        let total: f64 = estimable.iter().map(|s| s.weight).sum();
        let nl = field.n_levels;
        m_probs.push(
            (0..nl)
                .map(|k| {
                    estimable
                        .iter()
                        .map(|s| s.out.m_probs[j][k] * s.weight)
                        .sum::<f64>()
                        / total
                })
                .collect(),
        );
    }

    let mut u_probs = base.u_probs.clone();
    for j in 0..nf {
        if union[j] {
            u_probs[j] = neutral_u(fields[j].n_levels);
        }
    }

    let total_w: f64 = sessions.iter().map(|s| s.weight).sum();
    let proportion_matched = sessions
        .iter()
        .map(|s| s.out.proportion_matched * s.weight)
        .sum::<f64>()
        / total_w;

    let match_weights = weights_from(fields, &union, &m_probs, &u_probs);
    EmOutput {
        m_probs,
        u_probs,
        match_weights,
        converged: sessions.iter().all(|s| s.out.converged),
        iterations: sessions.iter().map(|s| s.out.iterations).max().unwrap_or(0),
        proportion_matched,
    }
}

/// Train a Fellegi–Sunter model from pre-binned comparison vectors.
///
/// * `random_levels`: `n_random x n_fields` level matrix over random pairs (u source).
/// * `blocked_levels`: `n_pairs x n_fields` level matrix over blocked pairs (m source).
/// * `conditioned`: `n_pairs x n_fields`; `true` = the field was conditioned out for
///   that pair's blocking pass and contributes no likelihood.
///
/// All three matrices are indexed `[row][field]`; `-1` denotes an unobserved
/// comparison (carries no evidence, per #1819). Panics only on inconsistent
/// widths — callers pass rectangular matrices.
pub fn train_em_core(
    fields: &[EmField],
    random_levels: &[Vec<i32>],
    blocked_levels: &[Vec<i32>],
    conditioned: &[Vec<bool>],
    params: &EmParams,
) -> EmOutput {
    let nf = fields.len();

    // ── Step 1: u from random pairs (fixed; Splink posture) ──
    let mut u_probs: Vec<Vec<f64>> = Vec::with_capacity(nf);
    for (j, f) in fields.iter().enumerate() {
        let mut counts = vec![0.0f64; f.n_levels];
        let mut observed = 0.0f64;
        for row in random_levels {
            let lvl = row[j];
            if lvl >= 0 {
                observed += 1.0;
                counts[lvl as usize] += 1.0;
            }
        }
        let total = observed + f.n_levels as f64 * SMOOTH;
        u_probs.push(counts.iter().map(|c| (c + SMOOTH) / total).collect());
    }

    // ── always_conditioned: blocking field (#1836) OR conditioned in EVERY pair ──
    let npairs = blocked_levels.len();
    let always: Vec<bool> = (0..nf)
        .map(|j| {
            let all_cond = npairs > 0 && (0..npairs).all(|i| conditioned[i][j]);
            fields[j].is_blocking || all_cond
        })
        .collect();
    for j in 0..nf {
        if always[j] {
            u_probs[j] = neutral_u(fields[j].n_levels); // fixed fields carry neutral u
        }
    }

    // ── m init: exponential prior 2^k, normalised ──
    let mut m_probs: Vec<Vec<f64>> = fields
        .iter()
        .map(|f| {
            let raw: Vec<f64> = (0..f.n_levels).map(|k| (1u64 << k) as f64).collect();
            let s: f64 = raw.iter().sum();
            raw.iter().map(|r| r / s).collect()
        })
        .collect();

    let mut p_match = P_MATCH_INIT;
    let mut converged = false;
    let mut iterations = 0usize;

    // ── Step 2: EM iterations (update m only; u fixed) ──
    for it in 0..params.max_iterations {
        iterations = it + 1;
        let old_m = m_probs.clone();

        // E-step: posterior P(match | comparison vector), per pair.
        let mut post = vec![0.0f64; npairs];
        for i in 0..npairs {
            let mut log_m = 0.0f64;
            let mut log_u = 0.0f64;
            for j in 0..nf {
                let lvl = blocked_levels[i][j];
                if lvl >= 0 && !conditioned[i][j] {
                    let l = lvl as usize;
                    log_m += m_probs[j][l].max(FLOOR).ln();
                    log_u += u_probs[j][l].max(FLOOR).ln();
                }
            }
            let log_match = p_match.max(FLOOR).ln() + log_m;
            let log_non = (1.0 - p_match).max(FLOOR).ln() + log_u;
            let mx = log_match.max(log_non);
            let e_m = (log_match - mx).exp();
            let e_n = (log_non - mx).exp();
            post[i] = e_m / (e_m + e_n);
        }

        // M-step: update p_match and m (u stays fixed).
        let total_match: f64 = post.iter().sum();
        p_match = (total_match / npairs as f64).max(SMOOTH);

        for j in 0..nf {
            if always[j] {
                continue; // fixed field: no m update
            }
            let nl = fields[j].n_levels;
            let mut elig_match = 0.0f64;
            for i in 0..npairs {
                if blocked_levels[i][j] >= 0 && !conditioned[i][j] {
                    elig_match += post[i];
                }
            }
            let mut new_m = vec![0.0f64; nl];
            for (lvl, slot) in new_m.iter_mut().enumerate() {
                let mut s = 0.0f64;
                for i in 0..npairs {
                    if blocked_levels[i][j] >= 0
                        && !conditioned[i][j]
                        && blocked_levels[i][j] as usize == lvl
                    {
                        s += post[i];
                    }
                }
                *slot = (s + SMOOTH) / (elig_match + nl as f64 * SMOOTH);
            }
            m_probs[j] = new_m;
        }

        // Convergence: max |Δm| over learnable fields only.
        let mut max_delta = 0.0f64;
        for j in 0..nf {
            if always[j] {
                continue;
            }
            for k in 0..fields[j].n_levels {
                max_delta = max_delta.max((m_probs[j][k] - old_m[j][k]).abs());
            }
        }
        if max_delta < params.convergence {
            converged = true;
            break;
        }
    }

    // ── Match weights: log2(m/u) for learnable fields; the fixed ramp otherwise ──
    let match_weights = weights_from(fields, &always, &m_probs, &u_probs);

    EmOutput {
        m_probs,
        u_probs,
        match_weights,
        converged,
        iterations,
        proportion_matched: p_match,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Assert two nested prob/weight tables agree within `tol` (decision-level
    /// parity; libm ln/log2/exp differ from CPython in the low mantissa bits).
    fn approx_eq(got: &[Vec<f64>], want: &[Vec<f64>], tol: f64, label: &str) {
        assert_eq!(got.len(), want.len(), "{label}: field count");
        for (fj, (g, w)) in got.iter().zip(want).enumerate() {
            assert_eq!(g.len(), w.len(), "{label}: field {fj} level count");
            for (k, (a, b)) in g.iter().zip(w).enumerate() {
                assert!(
                    (a - b).abs() <= tol,
                    "{label}: field {fj} level {k}: got {a} want {b} (Δ {})",
                    (a - b).abs()
                );
            }
        }
    }

    // Parity anchors are the output of the faithful Python transcription of
    // train_em's discrete E/M math (see the C1 commit message / scratch script).
    // Tolerances: 1e-9 on probabilities, 1e-7 on weights (weights amplify tiny
    // m near the 1e-10 floor).

    #[test]
    fn s1_learned_field_plus_blocking_field() {
        // field0: 2-level, learnable; field1: 2-level, blocking (fixed prior).
        let fields = vec![
            EmField {
                n_levels: 2,
                is_blocking: false,
            },
            EmField {
                n_levels: 2,
                is_blocking: true,
            },
        ];
        let random = vec![
            vec![0, 0],
            vec![0, 1],
            vec![1, 0],
            vec![0, 0],
            vec![1, 0],
            vec![0, 1],
        ];
        let blocked = vec![vec![1, 1], vec![1, 0], vec![0, 1], vec![1, 1], vec![0, 0]];
        let cond = vec![
            vec![false, true],
            vec![false, true],
            vec![false, true],
            vec![false, true],
            vec![false, true],
        ];
        let out = train_em_core(&fields, &random, &blocked, &cond, &EmParams::default());

        approx_eq(
            &out.u_probs,
            &[vec![0.6666666111111296, 0.3333333888888704], vec![0.5, 0.5]],
            1e-9,
            "u",
        );
        approx_eq(
            &out.m_probs,
            &[
                vec![0.001339945659776483, 0.9986600543402234],
                vec![0.3333333333333333, 0.6666666666666666],
            ],
            1e-9,
            "m",
        );
        approx_eq(
            &out.match_weights,
            &[
                vec![-8.958647168974087, 1.5830278310089891],
                vec![-3.0, 3.0],
            ],
            1e-7,
            "weights",
        );
        assert!(out.converged);
        assert_eq!(out.iterations, 7);
        assert!((out.proportion_matched - 0.2734769611550527).abs() <= 1e-9);
    }

    #[test]
    fn s2_three_level_learned_with_partial_conditioning() {
        // field0: 3-level learnable; field1: 2-level learnable, conditioned on
        // SOME pairs (mask exercised) — neither field is all-conditioned so both
        // train; runs to max_iterations (does not converge).
        let fields = vec![
            EmField {
                n_levels: 3,
                is_blocking: false,
            },
            EmField {
                n_levels: 2,
                is_blocking: false,
            },
        ];
        let random = vec![
            vec![0, 0],
            vec![2, 1],
            vec![1, 0],
            vec![0, 1],
            vec![2, 0],
            vec![1, 1],
            vec![0, 0],
        ];
        let blocked = vec![vec![2, 1], vec![2, 0], vec![1, 1], vec![0, 0], vec![2, 1]];
        let cond = vec![
            vec![false, true],
            vec![false, false],
            vec![true, false],
            vec![false, false],
            vec![false, true],
        ];
        let out = train_em_core(&fields, &random, &blocked, &cond, &EmParams::default());

        approx_eq(
            &out.u_probs,
            &[
                vec![
                    0.42857138775511955,
                    0.28571430612244025,
                    0.28571430612244025,
                ],
                vec![0.571428551020414, 0.428571448979586],
            ],
            1e-9,
            "u",
        );
        approx_eq(
            &out.m_probs,
            &[
                vec![
                    0.12643317905887277,
                    3.2554184846399456e-07,
                    0.8735664953992788,
                ],
                vec![0.6521093093266348, 0.34789069067336514],
            ],
            1e-9,
            "m",
        );
        approx_eq(
            &out.match_weights,
            &[
                vec![-1.7611604256237956, -19.74329883118059, 1.6123442485924109],
                vec![0.19054069408015709, -0.3009016683490896],
            ],
            1e-7,
            "weights",
        );
        assert!(!out.converged);
        assert_eq!(out.iterations, 20);
        assert!((out.proportion_matched - 0.7519314896981475).abs() <= 1e-9);
    }

    #[test]
    fn all_conditioned_field_takes_fixed_prior() {
        // A non-blocking field conditioned in EVERY pair must be treated as fixed
        // (neutral u, linear weights) — the per-pair all-conditioned branch.
        let fields = vec![
            EmField {
                n_levels: 2,
                is_blocking: false,
            },
            EmField {
                n_levels: 2,
                is_blocking: false,
            },
        ];
        let random = vec![vec![0, 0], vec![1, 1], vec![0, 1]];
        let blocked = vec![vec![1, 1], vec![1, 0], vec![0, 1]];
        let cond = vec![vec![false, true], vec![false, true], vec![false, true]];
        let out = train_em_core(&fields, &random, &blocked, &cond, &EmParams::default());
        // field1 is all-conditioned -> fixed: neutral u + linear weights.
        assert_eq!(out.u_probs[1], vec![0.5, 0.5]);
        assert_eq!(out.match_weights[1], vec![-3.0, 3.0]);
        // field0 is learnable -> real log2(m/u) weights (not the linear prior).
        assert_ne!(out.match_weights[0], vec![-3.0, 3.0]);
    }
}
