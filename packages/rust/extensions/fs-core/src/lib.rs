//! Canonical Fellegi-Sunter block-scoring math, pyo3-free.
//!
//! This crate is the single source of truth for the FS scoring *math* shared
//! across surfaces (the `native` Python extension today; an `fs-wasm` binding
//! next). Per-string similarity lives in `goldenmatch-score-core`; reference
//! data (census frequencies / name aliases) is injected by the host and never
//! bundled here. See `docs/superpowers/specs/2026-07-17-fs-core-cross-surface-extraction-design.md`.
//!
//! Increment 1 extracted the two pure leaf functions (`fs_normalize` +
//! `fs_level_from_sim`). Increment 2 adds [`score_fs_pair`] — the per-pair FS
//! scoring math (field loop → level → weight → negative evidence → normalize).
//! Both `score_block_pairs_fs` entry points in `native` (Vec + zero-copy Arrow)
//! now call it through a field accessor, so span iteration, rayon, GIL release,
//! and Arrow/Vec marshaling stay in `native` while the scoring *math* is single-
//! sourced here. Parity holds by construction (same computation, relocated).

use std::collections::{HashMap, HashSet};

use goldenmatch_score_core::score_one;

// ---------------------------------------------------------------------------
// Reference-data-aware name scorers (`name_freq_weighted_jw` /
// `given_name_aliased_jw`).
//
// These are the two flagship person-name FS comparison scorers. Today the
// native FS kernel handles only `score_one` ids 0..=3, so a probabilistic
// matchkey whose name field carries one of these scorers declines to the numpy
// fallback (`_NATIVE_FS_SCORER_IDS` in `core/probabilistic.py` lists neither) —
// the direct reason zero-config person dedupe still needs numpy. Porting the
// *math* here (with the census / alias tables INJECTED by the host, never
// bundled — see the design's § Reference data) is what lets the native kernel
// own the person-name path.
//
// Under "Rust is the reference" the base similarity is score-core's
// Jaro-Winkler (`score_one(0, ..)`, rapidfuzz-rs), NOT rapidfuzz-py — so the
// native result IS the answer and the numpy path is the lossy fallback, exactly
// as for the rest of the FS math.
// ---------------------------------------------------------------------------

/// Normalize a name the way the Python refdata modules do before every lookup
/// (`surnames._normalize` / `given_names._normalize`):
/// `"".join(ch for ch in name if ch.isalpha()).lower()`.
///
/// ASCII-scoped: Rust `char::is_alphabetic` + `to_lowercase` match Python
/// `str.isalpha` / `str.lower` on ASCII; non-ASCII is a documented cross-language
/// parity edge (the same one `infermap-core` carries — see the extensions
/// CLAUDE.md). Name fields are ASCII in every gated dataset.
#[inline]
pub fn normalize_name(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_alphabetic())
        .flat_map(|c| c.to_lowercase())
        .collect()
}

/// Injected surname-frequency reference data for [`name_freq_weighted_sim`].
///
/// The host builds this from the US Census surname table (or any frequency
/// source) and hands a borrow across every scoring call — `fs-core` bundles NO
/// data. `idf(value)` returns the IDF weight in `[0, 1]` for an IN-VOCAB value,
/// or `None` when the value is out-of-vocabulary. `None` is the exact
/// `surname_rank(v) is None` OOV gate the Python scorer uses to fall back to
/// plain Jaro-Winkler (a name present in the table always has both a rank and an
/// idf; OOV has neither).
pub trait SurnameFreq {
    fn idf(&self, value: &str) -> Option<f64>;
}

/// Injected given-name alias equivalence for [`given_name_aliased_sim`].
///
/// `are_equivalent(a, b)` is true iff `a` and `b` are known forms of the same
/// canonical given name (William ↔ Bill). Built by the host from the alias
/// table; `fs-core` bundles no data.
pub trait NameAliases {
    fn are_equivalent(&self, a: &str, b: &str) -> bool;
}

// Borderline re-weight zone + common-name floor — the exact constants from
// `refdata/scorer.py` (`_BORDERLINE_LOW/HIGH`, `_COMMON_NAME_FLOOR`).
const NFW_BORDERLINE_LOW: f64 = 0.70;
const NFW_BORDERLINE_HIGH: f64 = 0.95;
const NFW_COMMON_NAME_FLOOR: f64 = 0.6;

/// `name_freq_weighted_jw`: Jaro-Winkler down-weighted by surname frequency in
/// the borderline zone. Mirrors `refdata.scorer.NameFreqWeightedJW.score_pair`'s
/// STATIC-census branch (the branch the probabilistic path takes — it never
/// populates the per-dataset `tf_freqs` table, and TF-adjustment fields decline
/// native anyway):
///
/// ```text
/// jw = JaroWinkler(a, b)
/// if jw >= 0.95 or jw < 0.70:            return jw   # confident — no re-weight
/// if a or b is OOV in the table:         return jw   # can't classify frequency
/// idf = mean(idf(a), idf(b))
/// weight = 0.6 + 0.4 * idf
/// return jw * weight
/// ```
///
/// Common surnames (Smith/Smyth) carry a low idf → their borderline JW is scaled
/// down toward the `0.6` floor; rare surnames keep ~full JW. OOV on either side
/// falls back to plain JW so a typo of a common name isn't credited by rarity.
#[inline]
pub fn name_freq_weighted_sim(a: &str, b: &str, freq: &dyn SurnameFreq) -> f64 {
    let jw = score_one(0, a, b);
    // Outside the borderline band [LOW, HIGH) we trust JW directly (confident
    // match above, non-match below) — no frequency re-weighting.
    if !(NFW_BORDERLINE_LOW..NFW_BORDERLINE_HIGH).contains(&jw) {
        return jw;
    }
    let (idf_a, idf_b) = match (freq.idf(a), freq.idf(b)) {
        (Some(x), Some(y)) => (x, y),
        _ => return jw, // OOV on either side
    };
    let idf = (idf_a + idf_b) / 2.0;
    let weight = NFW_COMMON_NAME_FLOOR + (1.0 - NFW_COMMON_NAME_FLOOR) * idf;
    jw * weight
}

/// `given_name_aliased_jw`: Jaro-Winkler with an alias-aware exact bonus.
/// Mirrors `refdata.scorer.GivenNameAliasedJW.score_pair`:
///
/// ```text
/// if a and b are known aliases of one canonical name:   return 1.0
/// else:                                                 return JaroWinkler(a, b)
/// ```
///
/// Never LOWERS a JW score — only promotes known aliases (William↔Bill, which
/// plain JW scores ~0.55) to `1.0`.
#[inline]
pub fn given_name_aliased_sim(a: &str, b: &str, aliases: &dyn NameAliases) -> f64 {
    if aliases.are_equivalent(a, b) {
        1.0
    } else {
        score_one(0, a, b)
    }
}

/// A host-populated surname IDF table — the default [`SurnameFreq`] impl, keyed
/// on the [`normalize_name`]-normalized surname. The eventual pyo3 marshaling
/// builds one of these from the census `counts`; tests build one directly.
pub struct SurnameIdfTable {
    idf: HashMap<String, f64>,
}

impl SurnameIdfTable {
    /// Build directly from `(raw_name, idf)` pairs (keys normalized). Use when
    /// the host already carries per-name idf values.
    pub fn from_idf_pairs(pairs: impl IntoIterator<Item = (String, f64)>) -> Self {
        Self {
            idf: pairs
                .into_iter()
                .map(|(k, v)| (normalize_name(&k), v))
                .collect(),
        }
    }

    /// Build from raw `(raw_name, count)` census pairs, computing the idf with
    /// the exact `surnames.surname_idf` formula so the frequency weighting is
    /// single-sourced here too:
    /// `idf = clamp(log(total / count) / log(total / min_count), 0, 1)`
    /// (`count >= total → 0.0`, degenerate denominator → 0.0). OOV names are
    /// simply absent from the map → `idf()` returns `None` (the reference
    /// scorer's rank-based OOV gate; note Python's `surname_idf` returns `1.0`
    /// for OOV but the SCORER never reaches it — it gates on `surname_rank`
    /// first, which is `None` for OOV).
    pub fn from_counts(pairs: impl IntoIterator<Item = (String, f64)>) -> Self {
        let counts: Vec<(String, f64)> = pairs
            .into_iter()
            .map(|(k, c)| (normalize_name(&k), c))
            .collect();
        let total: f64 = counts.iter().map(|(_, c)| *c).sum();
        let min_count = counts.iter().map(|(_, c)| *c).fold(f64::INFINITY, f64::min);
        let denom = (total / min_count).ln();
        if total <= 0.0 || min_count <= 0.0 || denom <= 0.0 {
            // Degenerate table (Python `surname_idf` returns None here) -> empty
            // map, so every lookup is OOV and the scorer falls back to plain JW.
            return Self {
                idf: HashMap::new(),
            };
        }
        let mut idf = HashMap::with_capacity(counts.len());
        for (name, c) in counts {
            let v = if c >= total {
                0.0
            } else {
                ((total / c).ln() / denom).clamp(0.0, 1.0)
            };
            idf.insert(name, v);
        }
        Self { idf }
    }
}

impl SurnameFreq for SurnameIdfTable {
    #[inline]
    fn idf(&self, value: &str) -> Option<f64> {
        self.idf.get(&normalize_name(value)).copied()
    }
}

/// A host-populated given-name alias table — the default [`NameAliases`] impl.
/// `canonicals[form]` is the set of canonical ids `form` belongs to (most forms
/// have one; ambiguous short forms like "kate" have several); two forms are
/// equivalent iff their canonical-id sets intersect — mirroring
/// `given_names.are_equivalent` (including the reflexive
/// `normalize(a) == normalize(b) → true` shortcut, which also promotes an OOV
/// pair whose normalized forms collide).
pub struct AliasTable {
    canonicals: HashMap<String, HashSet<String>>,
}

impl AliasTable {
    /// Build from `(form, canonical_ids)` pairs (form keys normalized; canonical
    /// ids taken as-is, since the host already normalizes them at table-build).
    pub fn from_forms(pairs: impl IntoIterator<Item = (String, Vec<String>)>) -> Self {
        Self {
            canonicals: pairs
                .into_iter()
                .map(|(form, canons)| (normalize_name(&form), canons.into_iter().collect()))
                .collect(),
        }
    }
}

impl NameAliases for AliasTable {
    #[inline]
    fn are_equivalent(&self, a: &str, b: &str) -> bool {
        let (na, nb) = (normalize_name(a), normalize_name(b));
        if na.is_empty() || nb.is_empty() {
            return false;
        }
        if na == nb {
            return true; // reflexive shortcut (matches given_names.are_equivalent)
        }
        match (self.canonicals.get(&na), self.canonicals.get(&nb)) {
            (Some(ca), Some(cb)) => !ca.is_disjoint(cb),
            _ => false,
        }
    }
}

/// Normalize a summed Fellegi-Sunter match weight to a `[0, 1]` score.
///
/// `calibrated` = posterior probability `1 / (1 + 2^-(prior_w + w))` (with the
/// log-odds clamped to `[-60, 60]`); otherwise a linear min-max over the
/// observed weight range (`0.5` when the range is degenerate). This is the exact
/// contract the Python `score_probabilistic_vectorized` linear/posterior modes
/// use, so the native path is score-identical to the reference.
#[inline]
pub fn fs_normalize(
    total_weight: f64,
    calibrated: bool,
    prior_w: f64,
    min_weight: f64,
    weight_range: f64,
) -> f64 {
    if calibrated {
        let logodds = (prior_w + total_weight).clamp(-60.0, 60.0);
        1.0 / (1.0 + 2.0_f64.powf(-logodds))
    } else if weight_range > 0.0 {
        ((total_weight - min_weight) / weight_range).clamp(0.0, 1.0)
    } else {
        0.5
    }
}

/// Map a per-field similarity to a comparison level, matching
/// `core/probabilistic._levels_from_similarity`:
///   - custom `level_thresholds`: level = count of thresholds `t` with `sim >= t`
///     (inclusive, order-independent — `len(thresholds) + 1` levels total)
///   - 2 levels: `1` if `sim >= partial_threshold` else `0`
///   - 3 levels: `2` if `sim >= 0.95`, elif `sim >= partial_threshold` -> `1`, else `0`
///   - N levels: count of `k in 1..N` with `sim >= k/N` (even spacing)
#[inline]
pub fn fs_level_from_sim(
    sim: f64,
    n_levels: u8,
    partial_threshold: f64,
    level_thresholds: Option<&[f64]>,
) -> usize {
    if let Some(ts) = level_thresholds {
        return ts.iter().filter(|&&t| sim >= t).count();
    }
    match n_levels {
        2 => usize::from(sim >= partial_threshold),
        3 => {
            if sim >= 0.95 {
                2
            } else if sim >= partial_threshold {
                1
            } else {
                0
            }
        }
        n => {
            let n = n as usize;
            let mut lvl = 0usize;
            for k in 1..n {
                if sim >= (k as f64) / (n as f64) {
                    lvl += 1;
                }
            }
            lvl
        }
    }
}

/// Per-matchkey constants for [`score_fs_pair`], borrowed once per scoring call
/// (built in `native` from the marshaled kwargs, reused across every pair).
///
/// `base_min` / `base_max` are the NE-aware weight-range endpoints MINUS the sum
/// of the regular fields' min/max weights, so [`score_fs_pair`] can add back only
/// the fields actually OBSERVED for a pair (nulls contribute nothing) — the exact
/// per-pair min-max the numpy reference computes.
pub struct FsPairParams<'a> {
    pub scorer_ids: &'a [u8],
    pub levels: &'a [u8],
    pub partial_thresholds: &'a [f64],
    pub field_thresholds: &'a [Option<&'a [f64]>],
    pub match_weights: &'a [Vec<f64>],
    pub field_mins: &'a [f64],
    pub field_maxs: &'a [f64],
    pub base_min: f64,
    pub base_max: f64,
    pub ne_scorer_ids: &'a [u8],
    pub ne_thresholds: &'a [f64],
    pub ne_weights: &'a [f64],
    pub calibrated: bool,
    pub prior_w: f64,
    /// Injected surname-frequency table for `name_freq_weighted_jw`
    /// (scorer id [`FS_SCORER_NAME_FREQ_WEIGHTED`]) fields. `None` = no table
    /// available → those fields degrade to plain Jaro-Winkler (Python
    /// `is_available() == False`). One table serves every family-name field.
    /// `+ Sync` so a `&FsPairParams` can cross rayon worker threads in the
    /// native Arrow entry (the table is read-only shared state).
    pub surname_freq: Option<&'a (dyn SurnameFreq + Sync)>,
    /// Injected given-name alias table for `given_name_aliased_jw`
    /// (scorer id [`FS_SCORER_GIVEN_NAME_ALIASED`]) fields. `None` = degrade to
    /// plain Jaro-Winkler. One table serves every given-name field.
    pub name_aliases: Option<&'a (dyn NameAliases + Sync)>,
    /// Per-field Winkler term-frequency tables. `tf_tables[f]` is `Some` only for
    /// a field that opted into `tf_adjustment`; `[]` / all-`None` = no TF (the
    /// common case). Applied on an exact-equal TOP-level agreement — see
    /// [`TfTable`] and the field loop in [`score_fs_pair`]. Indexed like
    /// `scorer_ids`; a shorter/empty slice means "no TF for any field".
    pub tf_tables: &'a [Option<TfTable>],
}

/// Winkler term-frequency adjustment table for one field (EM-trained; the host
/// builds it from `EMResult.tf_freqs`/`tf_collision`). On an exact-equal
/// top-level agreement, a rare value earns a positive weight bump and a common
/// one a penalty: `clamp(log2(collision / freq(value)), ±10)` — mirroring
/// `core/probabilistic._scalar_tf_contribution`. `freqs` maps the transformed
/// value to its relative frequency; `collision` is `Σ freq(v)²`.
pub struct TfTable {
    pub freqs: HashMap<String, f64>,
    pub collision: f64,
}

/// The `±10` bit clamp on the TF adjustment (`_TF_CLAMP` in probabilistic.py).
const FS_TF_CLAMP: f64 = 10.0;

impl TfTable {
    /// The adjustment for an exact agreement on `value`: `0.0` when the value is
    /// out-of-table or its frequency / the collision rate is non-positive.
    #[inline]
    pub fn adjustment(&self, value: &str) -> f64 {
        if self.collision <= 0.0 {
            return 0.0;
        }
        match self.freqs.get(value) {
            Some(&fv) if fv > 0.0 => (self.collision / fv)
                .log2()
                .clamp(-FS_TF_CLAMP, FS_TF_CLAMP),
            _ => 0.0,
        }
    }
}

/// Reserved FS-kernel scorer id for `name_freq_weighted_jw` (beyond score-core's
/// `score_one` ids 0..=3). Intercepted by [`score_fs_pair`] and routed to the
/// injected [`SurnameFreq`] table; `score_one` does NOT implement it.
pub const FS_SCORER_NAME_FREQ_WEIGHTED: u8 = 4;
/// Reserved FS-kernel scorer id for `given_name_aliased_jw`. Intercepted by
/// [`score_fs_pair`] and routed to the injected [`NameAliases`] table.
pub const FS_SCORER_GIVEN_NAME_ALIASED: u8 = 5;

/// Per-field similarity dispatch for the FS kernel: the reserved name-scorer ids
/// route to the injected reference-data tables (degrading to plain JW when the
/// table is absent), everything else goes to score-core's `score_one`.
#[inline]
fn field_similarity(
    scorer_id: u8,
    a: &str,
    b: &str,
    surname_freq: Option<&(dyn SurnameFreq + Sync)>,
    name_aliases: Option<&(dyn NameAliases + Sync)>,
) -> f64 {
    match scorer_id {
        FS_SCORER_NAME_FREQ_WEIGHTED => match surname_freq {
            Some(fq) => name_freq_weighted_sim(a, b, fq),
            None => score_one(0, a, b),
        },
        FS_SCORER_GIVEN_NAME_ALIASED => match name_aliases {
            Some(al) => given_name_aliased_sim(a, b, al),
            None => score_one(0, a, b),
        },
        id => score_one(id, a, b),
    }
}

/// Score one within-block pair `(i, j)` and return its normalized `[0, 1]` score.
///
/// `get_field(field, row)` / `get_ne(ne, row)` yield the already-transform-applied
/// value (or `None` for a null), abstracting over `native`'s Vec-of-`String` and
/// zero-copy Arrow columns so both entry points share this one implementation.
/// The returned references are only read transiently (handed to `score_one`), so
/// their lifetime `'d` just has to outlive the call.
///
/// Mirrors `core/probabilistic.py` exactly: each observed field maps to a
/// comparison level whose EM match weight is summed; a negative-evidence field
/// fires (contributing its weight) iff BOTH values are present, non-empty, and
/// similarity is STRICTLY below the NE threshold; the summed weight is normalized
/// via [`fs_normalize`] over the pair's observed min-max range — except a pair
/// with no regular evidence and zero weight is a neutral `0.5` on the linear path.
#[inline]
#[allow(clippy::too_many_arguments)]
pub fn score_fs_pair<'d, F, G>(
    i: usize,
    j: usize,
    p: &FsPairParams<'_>,
    get_field: F,
    get_ne: G,
) -> f64
where
    F: Fn(usize, usize) -> Option<&'d str>,
    G: Fn(usize, usize) -> Option<&'d str>,
{
    let mut total_weight = 0.0_f64;
    let mut pair_min = p.base_min;
    let mut pair_max = p.base_max;
    let mut has_regular_evidence = false;
    for f in 0..p.scorer_ids.len() {
        if let (Some(a), Some(b)) = (get_field(f, i), get_field(f, j)) {
            has_regular_evidence = true;
            let sim = field_similarity(p.scorer_ids[f], a, b, p.surname_freq, p.name_aliases);
            let level = fs_level_from_sim(
                sim,
                p.levels[f],
                p.partial_thresholds[f],
                p.field_thresholds[f],
            );
            total_weight += p.match_weights[f][level];
            pair_min += p.field_mins[f];
            pair_max += p.field_maxs[f];
            // Winkler TF adjustment: on an exact-equal TOP-level agreement, bump
            // the weight by the value's rarity. `a == b` mirrors the numpy
            // `equal & (lvl == top)` mask (equal transformed values → top level).
            if let Some(Some(tf)) = p.tf_tables.get(f) {
                let top = (p.levels[f] as usize).saturating_sub(1);
                if level == top && a == b {
                    total_weight += tf.adjustment(a);
                }
            }
        }
    }
    // Negative evidence: exact `_ne_fired` semantics — fires iff both values are
    // present AND non-empty AND similarity is STRICTLY below the threshold.
    for k in 0..p.ne_scorer_ids.len() {
        if let (Some(a), Some(b)) = (get_ne(k, i), get_ne(k, j)) {
            if !a.is_empty()
                && !b.is_empty()
                && field_similarity(p.ne_scorer_ids[k], a, b, p.surname_freq, p.name_aliases)
                    < p.ne_thresholds[k]
            {
                total_weight += p.ne_weights[k];
            }
        }
    }
    if !p.calibrated && !has_regular_evidence && total_weight == 0.0 {
        0.5
    } else {
        fs_normalize(
            total_weight,
            p.calibrated,
            p.prior_w,
            pair_min,
            pair_max - pair_min,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_linear_min_max() {
        // Midpoint of the observed range -> 0.5.
        assert!((fs_normalize(1.0, false, 0.0, 0.0, 2.0) - 0.5).abs() < 1e-12);
        // Clamped into [0, 1].
        assert_eq!(fs_normalize(5.0, false, 0.0, 0.0, 2.0), 1.0);
        assert_eq!(fs_normalize(-5.0, false, 0.0, 0.0, 2.0), 0.0);
        // Degenerate range -> 0.5.
        assert_eq!(fs_normalize(3.0, false, 0.0, 0.0, 0.0), 0.5);
    }

    #[test]
    fn normalize_posterior_is_probability() {
        // prior_w + w = 0 -> 0.5; large positive -> ~1; large negative -> ~0.
        assert!((fs_normalize(0.0, true, 0.0, 0.0, 0.0) - 0.5).abs() < 1e-12);
        assert!(fs_normalize(100.0, true, 0.0, 0.0, 0.0) > 0.999);
        assert!(fs_normalize(-100.0, true, 0.0, 0.0, 0.0) < 0.001);
    }

    #[test]
    fn level_custom_thresholds_count_inclusive() {
        let ts = [0.8, 0.9, 0.95];
        for (sim, want) in [(1.0, 3usize), (0.93, 2), (0.85, 1), (0.5, 0)] {
            assert_eq!(fs_level_from_sim(sim, 4, 0.8, Some(&ts)), want);
        }
    }

    #[test]
    fn pair_agreement_beats_disagreement() {
        // Two exact-scorer fields (id 3), 2 levels, weights [disagree=-2, agree=+3].
        let mw = vec![vec![-2.0_f64, 3.0], vec![-2.0, 3.0]];
        let field_mins = [-2.0_f64, -2.0];
        let field_maxs = [3.0_f64, 3.0];
        let regular_min: f64 = field_mins.iter().sum();
        let regular_max: f64 = field_maxs.iter().sum();
        // No NE; min_weight/weight_range = regular range (base_* strip then re-add).
        let (min_weight, weight_range) = (regular_min, regular_max - regular_min);
        let p = FsPairParams {
            scorer_ids: &[3, 3],
            levels: &[2, 2],
            partial_thresholds: &[0.9, 0.9],
            field_thresholds: &[None, None],
            match_weights: &mw,
            field_mins: &field_mins,
            field_maxs: &field_maxs,
            base_min: min_weight - regular_min,
            base_max: min_weight + weight_range - regular_max,
            ne_scorer_ids: &[],
            ne_thresholds: &[],
            ne_weights: &[],
            calibrated: false,
            prior_w: 0.0,
            surname_freq: None,
            name_aliases: None,
            tf_tables: &[],
        };
        let rows = [["alice", "smith"], ["alice", "jones"], ["bob", "brown"]];
        let get = |f: usize, r: usize| Some(rows[r][f]);
        let noop_ne = |_: usize, _: usize| None;
        // Row 0 vs 1: agree on field 0, disagree on field 1 -> mid score.
        let s01 = score_fs_pair(0, 1, &p, get, noop_ne);
        // Row 0 vs 2: disagree on both -> bottom of range (0.0).
        let s02 = score_fs_pair(0, 2, &p, get, noop_ne);
        // Row 0 vs 0: agree on both -> top of range (1.0).
        let s00 = score_fs_pair(0, 0, &p, get, noop_ne);
        assert!((s00 - 1.0).abs() < 1e-12, "full agreement = 1.0, got {s00}");
        assert!(
            (s02 - 0.0).abs() < 1e-12,
            "full disagreement = 0.0, got {s02}"
        );
        assert!(
            s02 < s01 && s01 < s00,
            "partial between: {s02} < {s01} < {s00}"
        );
    }

    #[test]
    fn pair_null_field_uses_observed_range_only() {
        // One field null on one side -> that field contributes no evidence and is
        // excluded from the pair's min-max range (pair_min/pair_max unchanged).
        let mw = vec![vec![-2.0_f64, 3.0], vec![-2.0, 3.0]];
        let field_mins = [-2.0_f64, -2.0];
        let field_maxs = [3.0_f64, 3.0];
        let regular_min: f64 = field_mins.iter().sum();
        let regular_max: f64 = field_maxs.iter().sum();
        let (min_weight, weight_range) = (regular_min, regular_max - regular_min);
        let p = FsPairParams {
            scorer_ids: &[3, 3],
            levels: &[2, 2],
            partial_thresholds: &[0.9, 0.9],
            field_thresholds: &[None, None],
            match_weights: &mw,
            field_mins: &field_mins,
            field_maxs: &field_maxs,
            base_min: min_weight - regular_min,
            base_max: min_weight + weight_range - regular_max,
            ne_scorer_ids: &[],
            ne_thresholds: &[],
            ne_weights: &[],
            calibrated: false,
            prior_w: 0.0,
            surname_freq: None,
            name_aliases: None,
            tf_tables: &[],
        };
        // field 1 is null for row 1 -> only field 0 observed; agree -> 1.0 of the
        // single-field observed range.
        let get = |f: usize, r: usize| match (f, r) {
            (0, _) => Some("alice"),
            (1, 0) => Some("smith"),
            (1, 1) => None,
            _ => None,
        };
        let noop_ne = |_: usize, _: usize| None;
        let s = score_fs_pair(0, 1, &p, get, noop_ne);
        assert!(
            (s - 1.0).abs() < 1e-12,
            "observed-only agreement = 1.0, got {s}"
        );
    }

    #[test]
    fn score_fs_pair_routes_name_scorer_through_provider() {
        // A single given-name field with scorer id FS_SCORER_GIVEN_NAME_ALIASED.
        // William<->Bill are aliases -> given_name_aliased_sim returns 1.0, so the
        // pair lands at the TOP of the observed (2-level) weight range = 1.0.
        // Plain JW would score ~0.55 -> a mid/low level -> NOT 1.0. This proves
        // the dispatch actually consulted the injected alias table.
        let aliases = AliasTable::from_forms([
            ("William".into(), vec!["william".into()]),
            ("Bill".into(), vec!["william".into()]),
        ]);
        let mw = vec![vec![-2.0_f64, 3.0]];
        let field_mins = [-2.0_f64];
        let field_maxs = [3.0_f64];
        let regular_min: f64 = field_mins.iter().sum();
        let regular_max: f64 = field_maxs.iter().sum();
        let (min_weight, weight_range) = (regular_min, regular_max - regular_min);
        let p = FsPairParams {
            scorer_ids: &[FS_SCORER_GIVEN_NAME_ALIASED],
            levels: &[2],
            partial_thresholds: &[0.9],
            field_thresholds: &[None],
            match_weights: &mw,
            field_mins: &field_mins,
            field_maxs: &field_maxs,
            base_min: min_weight - regular_min,
            base_max: min_weight + weight_range - regular_max,
            ne_scorer_ids: &[],
            ne_thresholds: &[],
            ne_weights: &[],
            calibrated: false,
            prior_w: 0.0,
            surname_freq: None,
            name_aliases: Some(&aliases),
            tf_tables: &[],
        };
        let rows = ["William", "Bill"];
        let get = |_f: usize, r: usize| Some(rows[r]);
        let noop_ne = |_: usize, _: usize| None;
        let s = score_fs_pair(0, 1, &p, get, noop_ne);
        assert!(
            (s - 1.0).abs() < 1e-12,
            "alias promotion should top the range (1.0), got {s}"
        );

        // Same field with NO alias table injected -> plain JW -> William~Bill is
        // a DISAGREEMENT at the 0.9 partial threshold (level 0) -> bottom = 0.0.
        let p_no = FsPairParams {
            name_aliases: None,
            ..p
        };
        let s_no = score_fs_pair(0, 1, &p_no, get, noop_ne);
        assert!(
            (s_no - 0.0).abs() < 1e-12,
            "no alias table -> plain JW disagreement = 0.0, got {s_no}"
        );
    }

    #[test]
    fn tf_table_adjustment_matches_reference() {
        let freqs: HashMap<String, f64> = [("smith".to_string(), 0.5), ("rare".to_string(), 0.001)]
            .into_iter()
            .collect();
        let collision = 0.5 * 0.5 + 0.001 * 0.001; // Σ freq²
        let tf = TfTable { freqs, collision };
        // Common value -> penalty (log2(collision/0.5) < 0).
        assert!((tf.adjustment("smith") - (collision / 0.5).log2()).abs() < 1e-12);
        assert!(tf.adjustment("smith") < 0.0);
        // Rare value -> positive bump.
        assert!(tf.adjustment("rare") > 0.0);
        // OOV -> 0.
        assert_eq!(tf.adjustment("missing"), 0.0);
        // ±10 clamp on an extreme frequency.
        let extreme = TfTable {
            freqs: [("x".to_string(), 1e-40)].into_iter().collect(),
            collision: 1.0,
        };
        assert_eq!(extreme.adjustment("x"), 10.0);
    }

    #[test]
    fn score_fs_pair_tf_favors_rare_exact_agreement() {
        // One exact field (id 3), 2 levels. Calibrated (posterior) so the score is
        // monotonic in the summed weight with NO min-max clamp — the only way the
        // TF bump is observable on the normalized score.
        let mw = vec![vec![-2.0_f64, 3.0]];
        let field_mins = [-2.0_f64];
        let field_maxs = [3.0_f64];
        let freqs: HashMap<String, f64> = [("smith".to_string(), 0.5), ("rare".to_string(), 0.001)]
            .into_iter()
            .collect();
        let collision = 0.5 * 0.5 + 0.001 * 0.001;
        let tf_tables = vec![Some(TfTable { freqs, collision })];
        let p = FsPairParams {
            scorer_ids: &[3],
            levels: &[2],
            partial_thresholds: &[0.9],
            field_thresholds: &[None],
            match_weights: &mw,
            field_mins: &field_mins,
            field_maxs: &field_maxs,
            base_min: 0.0,
            base_max: 0.0,
            ne_scorer_ids: &[],
            ne_thresholds: &[],
            ne_weights: &[],
            calibrated: true,
            prior_w: 0.0,
            surname_freq: None,
            name_aliases: None,
            tf_tables: &tf_tables,
        };
        let rows = ["smith", "smith", "rare", "rare", "jones"];
        let get = |_f: usize, r: usize| Some(rows[r]);
        let noop_ne = |_: usize, _: usize| None;
        let common = score_fs_pair(0, 1, &p, get, noop_ne); // smith==smith
        let rare = score_fs_pair(2, 3, &p, get, noop_ne); // rare==rare
        let disagree = score_fs_pair(0, 4, &p, get, noop_ne); // smith vs jones
        assert!(
            rare > common && common > disagree,
            "rare {rare} > common {common} > disagree {disagree}"
        );
        // Sanity: with NO tf table, the two exact agreements are identical.
        let p_no = FsPairParams {
            tf_tables: &[],
            ..p
        };
        let a = score_fs_pair(0, 1, &p_no, get, noop_ne);
        let b = score_fs_pair(2, 3, &p_no, get, noop_ne);
        assert!((a - b).abs() < 1e-12, "no tf -> identical exact agreements");
    }

    #[test]
    fn normalize_name_strips_nonalpha_and_lowercases() {
        assert_eq!(normalize_name("O'Brien"), "obrien");
        assert_eq!(normalize_name("Smith-Jones"), "smithjones");
        assert_eq!(normalize_name("  Bob! "), "bob");
        assert_eq!(normalize_name("123"), "");
    }

    #[test]
    fn given_name_alias_promotes_to_one() {
        // William <-> Bill both map to canonical "william".
        let aliases = AliasTable::from_forms([
            ("William".into(), vec!["william".into()]),
            ("Bill".into(), vec!["william".into()]),
            ("Robert".into(), vec!["robert".into()]),
        ]);
        // Alias pair -> 1.0 despite low JW.
        assert_eq!(given_name_aliased_sim("William", "Bill", &aliases), 1.0);
        // Reflexive shortcut: same normalized form (OOV) still 1.0.
        assert_eq!(given_name_aliased_sim("Bob", "bob!", &aliases), 1.0);
        // Unrelated names -> plain JW (never promoted, and < 1.0).
        let s = given_name_aliased_sim("William", "Robert", &aliases);
        assert!(s < 1.0, "unrelated names keep plain JW, got {s}");
        assert!((s - score_one(0, "William", "Robert")).abs() < 1e-12);
    }

    #[test]
    fn name_freq_weight_downweights_common_in_zone() {
        // A borderline-JW pair (Smith~Smyth) on a COMMON surname (idf ~0) gets
        // scaled toward the 0.6 floor; on a RARE surname (idf ~1) keeps ~full JW.
        struct Freq;
        impl SurnameFreq for Freq {
            fn idf(&self, value: &str) -> Option<f64> {
                match normalize_name(value).as_str() {
                    // pretend both spellings are equally common / rare
                    "smith" | "smyth" => Some(0.0),   // common
                    "qwerty" | "qwertz" => Some(1.0), // rare
                    _ => None,                        // OOV
                }
            }
        }
        let f = Freq;
        let jw_common = score_one(0, "smith", "smyth");
        // Only exercise the branch if JW is actually in the borderline band.
        assert!(
            (NFW_BORDERLINE_LOW..NFW_BORDERLINE_HIGH).contains(&jw_common),
            "test fixture assumes smith~smyth is borderline, got {jw_common}"
        );
        let common = name_freq_weighted_sim("smith", "smyth", &f);
        // idf 0 -> weight = 0.6 -> score = jw * 0.6.
        assert!((common - jw_common * 0.6).abs() < 1e-12, "got {common}");

        // Rare surname pair with the SAME jw shape -> idf 1 -> weight 1 -> full jw.
        let jw_rare = score_one(0, "qwerty", "qwertz");
        if (NFW_BORDERLINE_LOW..NFW_BORDERLINE_HIGH).contains(&jw_rare) {
            let rare = name_freq_weighted_sim("qwerty", "qwertz", &f);
            assert!(
                (rare - jw_rare).abs() < 1e-12,
                "rare keeps full jw, got {rare}"
            );
        }
    }

    #[test]
    fn name_freq_weight_oov_and_confident_are_plain_jw() {
        struct Freq;
        impl SurnameFreq for Freq {
            fn idf(&self, _value: &str) -> Option<f64> {
                None // everything OOV
            }
        }
        let f = Freq;
        // OOV in the borderline zone -> plain jw (no re-weight).
        let jw = score_one(0, "smith", "smyth");
        if (NFW_BORDERLINE_LOW..NFW_BORDERLINE_HIGH).contains(&jw) {
            assert!((name_freq_weighted_sim("smith", "smyth", &f) - jw).abs() < 1e-12);
        }
        // Confident agreement (jw >= 0.95) -> plain jw regardless of table.
        struct Common;
        impl SurnameFreq for Common {
            fn idf(&self, _v: &str) -> Option<f64> {
                Some(0.0)
            }
        }
        let identical = name_freq_weighted_sim("anderson", "anderson", &Common);
        assert_eq!(identical, 1.0, "exact agreement stays 1.0, not floored");
        // Below the low bound -> plain jw (no point re-weighting a non-match).
        let low = name_freq_weighted_sim("abc", "xyz", &Common);
        assert!((low - score_one(0, "abc", "xyz")).abs() < 1e-12);
    }

    #[test]
    fn surname_idf_table_from_counts_matches_formula() {
        // total = 100, min_count = 1. Smith count 60, Rare count 1.
        let t = SurnameIdfTable::from_counts([
            ("Smith".into(), 60.0),
            ("Johnson".into(), 39.0),
            ("Rare".into(), 1.0),
        ]);
        let total = 100.0_f64;
        let min_count = 1.0_f64;
        let denom = (total / min_count).ln();
        let want_smith = ((total / 60.0_f64).ln() / denom).clamp(0.0, 1.0);
        let want_rare = ((total / 1.0_f64).ln() / denom).clamp(0.0, 1.0);
        assert!((t.idf("Smith").unwrap() - want_smith).abs() < 1e-12);
        assert!((t.idf("Rare").unwrap() - want_rare).abs() < 1e-12);
        assert!(want_smith < want_rare, "common < rare idf");
        // The rarest (count == min) -> idf 1.0; OOV -> None.
        assert!((t.idf("Rare").unwrap() - 1.0).abs() < 1e-12);
        assert_eq!(t.idf("NotInTable"), None);
    }

    #[test]
    fn level_none_keeps_legacy_banding() {
        // 2-level.
        assert_eq!(fs_level_from_sim(0.9, 2, 0.8, None), 1);
        assert_eq!(fs_level_from_sim(0.7, 2, 0.8, None), 0);
        // 3-level.
        assert_eq!(fs_level_from_sim(1.0, 3, 0.8, None), 2);
        assert_eq!(fs_level_from_sim(0.9, 3, 0.8, None), 1);
        assert_eq!(fs_level_from_sim(0.5, 3, 0.8, None), 0);
        // N-level (even spacing).
        assert_eq!(fs_level_from_sim(0.85, 5, 0.8, None), 4);
        assert_eq!(fs_level_from_sim(0.4, 5, 0.8, None), 2);
    }
}
