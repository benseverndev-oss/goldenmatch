//! Diagnostic reductions from Arrow run artifacts.
//!
//! `ScoreDiagnostics::from_batch` reduces the `scored_pairs` RecordBatch to
//! frame-free stats the suggestion rules consume.  `ClusterDiagnostics::from_batch`
//! does the same for the `clusters` RecordBatch.  Both reuse `analysis_core`
//! kernels -- do NOT add a second histogram / quantile implementation here.

#[cfg(feature = "arrow")]
use arrow::array::{Array, BooleanArray, Float64Array, StringArray};
#[cfg(feature = "arrow")]
use arrow::record_batch::RecordBatch;

// ---------------------------------------------------------------------------
// ColumnSignal — per-column signals extracted from the column_signals batch.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ColumnSignal {
    pub field: String,
    pub col_type: String,
    pub scorer: String,
    pub in_blocking: bool,
    pub in_negative_evidence: bool,
    pub identity_score: f64,
    pub corruption_score: f64,
    pub collision_rate: f64,
    pub cardinality_ratio: f64,
    pub null_rate: f64,
    pub variant_rate: f64,
}

/// Extract one `ColumnSignal` per row from a `column_signals` RecordBatch.
///
/// Schema (one row per column):
/// `field:utf8, col_type:utf8, scorer:utf8, in_blocking:bool,
///  in_negative_evidence:bool, identity_score:f64, corruption_score:f64,
///  collision_rate:f64, cardinality_ratio:f64, null_rate:f64, variant_rate:f64`
#[cfg(feature = "arrow")]
pub fn column_signals_from_batch(batch: &RecordBatch) -> Result<Vec<ColumnSignal>, String> {
    let field_arr = batch
        .column_by_name("field")
        .ok_or("missing field column")?
        .as_any()
        .downcast_ref::<StringArray>()
        .ok_or("field not utf8")?;

    let col_type_arr = batch
        .column_by_name("col_type")
        .ok_or("missing col_type column")?
        .as_any()
        .downcast_ref::<StringArray>()
        .ok_or("col_type not utf8")?;

    let scorer_arr = batch
        .column_by_name("scorer")
        .ok_or("missing scorer column")?
        .as_any()
        .downcast_ref::<StringArray>()
        .ok_or("scorer not utf8")?;

    let in_blocking_arr = batch
        .column_by_name("in_blocking")
        .ok_or("missing in_blocking column")?
        .as_any()
        .downcast_ref::<BooleanArray>()
        .ok_or("in_blocking not bool")?;

    let in_neg_arr = batch
        .column_by_name("in_negative_evidence")
        .ok_or("missing in_negative_evidence column")?
        .as_any()
        .downcast_ref::<BooleanArray>()
        .ok_or("in_negative_evidence not bool")?;

    let identity_score_arr = batch
        .column_by_name("identity_score")
        .ok_or("missing identity_score column")?
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or("identity_score not f64")?;

    let corruption_score_arr = batch
        .column_by_name("corruption_score")
        .ok_or("missing corruption_score column")?
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or("corruption_score not f64")?;

    let collision_rate_arr = batch
        .column_by_name("collision_rate")
        .ok_or("missing collision_rate column")?
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or("collision_rate not f64")?;

    let cardinality_ratio_arr = batch
        .column_by_name("cardinality_ratio")
        .ok_or("missing cardinality_ratio column")?
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or("cardinality_ratio not f64")?;

    let null_rate_arr = batch
        .column_by_name("null_rate")
        .ok_or("missing null_rate column")?
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or("null_rate not f64")?;

    let variant_rate_arr = batch
        .column_by_name("variant_rate")
        .ok_or("missing variant_rate column")?
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or("variant_rate not f64")?;

    let mut signals = Vec::with_capacity(batch.num_rows());
    for i in 0..batch.num_rows() {
        signals.push(ColumnSignal {
            field: field_arr.value(i).to_owned(),
            col_type: col_type_arr.value(i).to_owned(),
            scorer: scorer_arr.value(i).to_owned(),
            in_blocking: in_blocking_arr.value(i),
            in_negative_evidence: in_neg_arr.value(i),
            identity_score: identity_score_arr.value(i),
            corruption_score: corruption_score_arr.value(i),
            collision_rate: collision_rate_arr.value(i),
            cardinality_ratio: cardinality_ratio_arr.value(i),
            null_rate: null_rate_arr.value(i),
            variant_rate: variant_rate_arr.value(i),
        });
    }
    Ok(signals)
}

pub struct ScoreDiagnostics {
    pub histogram: Vec<(f64, i64)>,
    pub mass_above: f64,      // fraction of pairs with score >= threshold
    pub mass_just_below: f64, // fraction in [threshold-0.10, threshold)
    // Total scored_pairs rows (incl. null scores); the mass fractions divide by
    // the count of NON-NULL scores so blocked/null pairs don't dilute them.
    pub n_pairs: usize,
}

impl ScoreDiagnostics {
    /// Arrow-free twin of `from_batch` -- the single source of truth for the math.
    ///
    /// `scores` = the NON-NULL pair scores; `n_pairs` = total rows incl. null
    /// scores (matches `batch.num_rows()`). The mass fractions divide by the
    /// non-null score count (`scores.len()`) so blocked/null pairs don't dilute
    /// them; `n_pairs` is carried for rationale text only.
    pub fn from_scores(scores: &[f64], n_pairs: usize, threshold: f64, bins: i64) -> Self {
        let n = scores.len();
        if n == 0 {
            return Self {
                histogram: vec![],
                mass_above: 0.0,
                mass_just_below: 0.0,
                n_pairs,
            };
        }
        let above = scores.iter().filter(|&&s| s >= threshold).count();
        let band_lo = (threshold - 0.10).max(0.0);
        let just_below = scores
            .iter()
            .filter(|&&s| s >= band_lo && s < threshold)
            .count();
        // Reuse analysis-core histogram (no second implementation).
        let histogram = analysis_core::histogram(scores, bins);
        Self {
            histogram,
            mass_above: above as f64 / n as f64,
            mass_just_below: just_below as f64 / n as f64,
            n_pairs,
        }
    }

    #[cfg(feature = "arrow")]
    pub fn from_batch(batch: &RecordBatch, threshold: f64, bins: i64) -> Result<Self, String> {
        let col = batch
            .column_by_name("score")
            .ok_or("missing score column")?;
        let scores = col
            .as_any()
            .downcast_ref::<Float64Array>()
            .ok_or("score not f64")?;
        let vals: Vec<f64> = scores.iter().flatten().collect();
        // Total rows (incl. null scores) -- surfaced in rationale text. The mass
        // fractions divide by non-null score count so null pairs don't dilute them.
        let n_pairs = batch.num_rows();
        Ok(Self::from_scores(&vals, n_pairs, threshold, bins))
    }

    /// Locate the threshold valley that separates the true-match mass (a
    /// high-score mode) from the non-match bulk. Right-anchored: finds the
    /// RIGHTMOST prominent high-score peak (the match mode -- detectable even when
    /// it carries little total mass, because it stands well above the trough to its
    /// left), then walks LEFT from that peak to the adjacent local minimum and
    /// returns that trough's left edge. Returns None when there is no prominent
    /// high-score mode (unimodal / pure decay) -- in that case the kernel emits no
    /// dip suggestion rather than collapse the threshold into the left tail.
    pub fn dip(&self) -> Option<f64> {
        let counts: Vec<i64> = self.histogram.iter().map(|(_, c)| *c).collect();
        let n = counts.len();
        if n < 3 {
            return None;
        }
        const PEAK_PROMINENCE: f64 = 3.0; // match-mode peak must stand >=3x above its left trough
        // Leftmost global-max bin (the non-match bulk anchor). `max_by_key`
        // returns the LAST max on ties, which would place the anchor at the
        // right mode of a clean bimodal and leave nothing to its right -- pick
        // the FIRST max instead so the right-anchored search has the high band
        // to scan.
        let global_max = counts.iter().copied().max().unwrap_or(0);
        if global_max == 0 {
            return None;
        }
        let global_max_idx = counts.iter().position(|&c| c == global_max)?;

        // 1. Find the RIGHTMOST prominent local-maximum bin to the right of the
        //    global max. "Prominent" = it stands >= PEAK_PROMINENCE x above the
        //    local trough immediately to its left (walk left while non-increasing).
        let mut peak_idx: Option<usize> = None;
        for i in (global_max_idx + 1)..n {
            let is_local_max = counts[i] >= counts[i - 1] && (i + 1 == n || counts[i] >= counts[i + 1]);
            if !is_local_max {
                continue;
            }
            // trough immediately left of this peak
            let mut t = i;
            while t > 0 && counts[t - 1] <= counts[t] {
                t -= 1;
            }
            let trough = counts[t];
            if trough == 0 || (counts[i] as f64) >= PEAK_PROMINENCE * (trough as f64) {
                peak_idx = Some(i); // rightmost qualifying peak wins (loop continues)
            }
        }
        let peak = peak_idx?;

        // 2. Valley = the local minimum adjacent to (left of) that peak: walk left
        //    while the left neighbor is no greater (descending into the trough).
        let mut v = peak;
        while v > 0 && counts[v - 1] <= counts[v] {
            v -= 1;
        }
        Some(self.histogram[v].0)
    }
}

pub struct ClusterDiagnostics {
    pub weak: usize,
    pub oversized: usize,
    pub split: usize,
    pub n_clusters: usize,
}

impl ClusterDiagnostics {
    /// Arrow-free twin of `from_batch` -- the single source of truth for the math.
    /// `quality` = per-cluster quality labels; `oversized` aligned per cluster.
    pub fn from_rows(quality: &[String], oversized: &[bool], n_clusters: usize) -> Self {
        let mut weak = 0usize;
        let mut split = 0usize;
        for q in quality {
            match q.as_str() {
                "weak" => weak += 1,
                "split" => split += 1,
                _ => {}
            }
        }
        let oversized_n = oversized.iter().filter(|&&b| b).count();
        Self {
            weak,
            oversized: oversized_n,
            split,
            n_clusters,
        }
    }

    #[cfg(feature = "arrow")]
    pub fn from_batch(batch: &RecordBatch) -> Result<Self, String> {
        let n_clusters = batch.num_rows();

        let quality_col = batch
            .column_by_name("quality")
            .ok_or("missing quality column")?;
        let quality = quality_col
            .as_any()
            .downcast_ref::<StringArray>()
            .ok_or("quality not utf8")?;

        let oversized_col = batch
            .column_by_name("oversized")
            .ok_or("missing oversized column")?;
        let oversized_arr = oversized_col
            .as_any()
            .downcast_ref::<BooleanArray>()
            .ok_or("oversized not bool")?;

        let quality_vec: Vec<String> = quality.iter().flatten().map(|s| s.to_owned()).collect();
        let oversized_vec: Vec<bool> = oversized_arr.iter().flatten().collect();

        Ok(Self::from_rows(&quality_vec, &oversized_vec, n_clusters))
    }
}

#[cfg(all(test, feature = "arrow"))]
mod tests {
    use super::*;
    use arrow::array::{BooleanArray, Float64Array, Int64Array, StringArray};
    use arrow::datatypes::{DataType, Field, Schema};
    use arrow::record_batch::RecordBatch;
    use std::sync::Arc;

    fn pairs_batch(scores: &[f64]) -> RecordBatch {
        let n = scores.len();
        let schema = Arc::new(Schema::new(vec![
            Field::new("id_a", DataType::Int64, false),
            Field::new("id_b", DataType::Int64, false),
            Field::new("score", DataType::Float64, false),
        ]));
        RecordBatch::try_new(
            schema,
            vec![
                Arc::new(Int64Array::from((0..n as i64).collect::<Vec<_>>())),
                Arc::new(Int64Array::from(
                    (0..n as i64).map(|x| x + 1).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(scores.to_vec())),
            ],
        )
        .unwrap()
    }

    #[test]
    fn mass_bands_split_by_threshold() {
        // 6 scores: 3 above 0.8, 1 in [0.7,0.8), 2 below 0.7
        let b = pairs_batch(&[0.95, 0.9, 0.85, 0.75, 0.6, 0.5]);
        let d = ScoreDiagnostics::from_batch(&b, 0.80, 24).unwrap();
        assert!((d.mass_above - 3.0 / 6.0).abs() < 1e-9);
        assert!((d.mass_just_below - 1.0 / 6.0).abs() < 1e-9); // [0.70,0.80)
        assert_eq!(d.histogram.len(), 24);
    }

    #[test]
    fn n_pairs_counts_total_rows_fractions_over_non_null() {
        // 4 rows: scores [0.9, null, 0.85, 0.5]. n_pairs = 4 (total rows),
        // but mass fractions divide by the 3 non-null scores.
        let schema = Arc::new(Schema::new(vec![
            Field::new("id_a", DataType::Int64, false),
            Field::new("id_b", DataType::Int64, false),
            Field::new("score", DataType::Float64, true),
        ]));
        let b = RecordBatch::try_new(
            schema,
            vec![
                Arc::new(Int64Array::from(vec![0i64, 1, 2, 3])),
                Arc::new(Int64Array::from(vec![1i64, 2, 3, 4])),
                Arc::new(Float64Array::from(vec![
                    Some(0.9),
                    None,
                    Some(0.85),
                    Some(0.5),
                ])),
            ],
        )
        .unwrap();
        let d = ScoreDiagnostics::from_batch(&b, 0.80, 8).unwrap();
        assert_eq!(d.n_pairs, 4); // total rows, incl. the null
        // 2 of 3 non-null scores are >= 0.80
        assert!((d.mass_above - 2.0 / 3.0).abs() < 1e-9);
    }

    #[test]
    fn empty_batch_returns_zero_mass() {
        let b = pairs_batch(&[]);
        let d = ScoreDiagnostics::from_batch(&b, 0.80, 24).unwrap();
        assert_eq!(d.n_pairs, 0);
        assert_eq!(d.mass_above, 0.0);
        assert_eq!(d.mass_just_below, 0.0);
        assert!(d.histogram.is_empty());
    }

    #[test]
    fn dip_detection_bimodal() {
        // Build a batch with a clear bimodal distribution: many low and many high,
        // few in the middle. With 10 bins over [0,1] each bin is width 0.1.
        // Bins 0..4 each get 5 counts, bin 4 gets ~0, bins 5..9 each get 5.
        // The valley at ~0.5 should be detected.
        let mut scores: Vec<f64> = Vec::new();
        for _ in 0..5 {
            scores.extend_from_slice(&[0.05, 0.15, 0.25, 0.35]);
        }
        for _ in 0..5 {
            scores.extend_from_slice(&[0.65, 0.75, 0.85, 0.95]);
        }
        // one in the middle to avoid triggering the "all equal" collapse
        scores.push(0.50);

        let b = pairs_batch(&scores);
        let d = ScoreDiagnostics::from_batch(&b, 0.60, 10).unwrap();
        assert!(d.dip().is_some(), "expected a dip in bimodal distribution");
    }

    #[test]
    fn dip_returns_none_for_uniform() {
        // Uniform distribution has no clear valley
        let scores: Vec<f64> = (0..50).map(|i| i as f64 / 50.0).collect();
        let b = pairs_batch(&scores);
        let d = ScoreDiagnostics::from_batch(&b, 0.80, 10).unwrap();
        // uniform won't have a bin below 25% of peak, so dip should be None
        assert!(d.dip().is_none(), "uniform distribution should have no dip");
    }

    fn diag(hist: Vec<(f64, i64)>) -> ScoreDiagnostics {
        let n: i64 = hist.iter().map(|(_, c)| *c).sum();
        ScoreDiagnostics {
            histogram: hist,
            mass_above: 0.0,
            mass_just_below: 0.0,
            n_pairs: n as usize,
        }
    }

    // (1) The real NCVR-synthetic shape: dip MUST land in the high band (the
    //     trough below the true-match mode), NOT the 0.04 left-tail sliver.
    #[test]
    fn dip_targets_valley_below_match_mode_on_right_skewed() {
        let hist = vec![
            (0.0000, 441008),
            (0.0417, 48),
            (0.0833, 1002),
            (0.1250, 5376),
            (0.1667, 6263),
            (0.2083, 10586),
            (0.2500, 16894),
            (0.2917, 39651),
            (0.3333, 52055),
            (0.3750, 40747),
            (0.4167, 49015),
            (0.4583, 72972),
            (0.5000, 66375),
            (0.5417, 40352),
            (0.5833, 19660),
            (0.6250, 7669),
            (0.6667, 2296),
            (0.7083, 536),
            (0.7500, 152),
            (0.7917, 154),
            (0.8333, 181),
            (0.8750, 97),
            (0.9167, 583),
            (0.9583, 1456),
        ];
        let d = diag(hist).dip().expect("should find a high-side valley");
        assert!(d >= 0.75, "expected valley below the match mode (~0.875), got {d}");
        assert!(d >= 0.10, "must not return the left-tail sliver 0.04, got {d}");
    }

    // (2) Clean bimodal (preserves existing behavior): valley between the two modes.
    #[test]
    fn dip_clean_bimodal_returns_mid_valley() {
        let d = diag(vec![(0.0, 100), (0.5, 2), (0.9, 100)]).dip();
        assert_eq!(d, Some(0.5));
    }

    // (3) Single mode / no prominent high-score peak: return None (no suggestion
    //     beats a destructive one). Monotonic decay, no second hump.
    #[test]
    fn dip_single_mode_returns_none() {
        let d = diag(vec![
            (0.0, 500),
            (0.1, 200),
            (0.2, 80),
            (0.3, 30),
            (0.4, 12),
            (0.5, 5),
            (0.6, 2),
            (0.7, 1),
        ])
        .dip();
        assert_eq!(d, None);
    }

    fn clusters_batch(
        qualities: &[&str],
        oversized_flags: &[bool],
    ) -> RecordBatch {
        let n = qualities.len();
        let schema = Arc::new(Schema::new(vec![
            Field::new("cluster_id", DataType::Int64, false),
            Field::new("size", DataType::Int64, false),
            Field::new("confidence", DataType::Float64, false),
            Field::new("quality", DataType::Utf8, false),
            Field::new("oversized", DataType::Boolean, false),
        ]));
        RecordBatch::try_new(
            schema,
            vec![
                Arc::new(Int64Array::from((0..n as i64).collect::<Vec<_>>())),
                Arc::new(Int64Array::from(vec![2i64; n])),
                Arc::new(Float64Array::from(vec![0.9f64; n])),
                Arc::new(StringArray::from(qualities.to_vec())),
                Arc::new(BooleanArray::from(oversized_flags.to_vec())),
            ],
        )
        .unwrap()
    }

    #[test]
    fn cluster_diagnostics_counts_correctly() {
        // 2 weak, 1 split, 2 strong, 3 oversized (indices 0, 2, 3)
        let qualities = ["weak", "weak", "split", "strong", "strong"];
        let oversized = [true, false, true, true, false];
        let b = clusters_batch(&qualities, &oversized);
        let d = ClusterDiagnostics::from_batch(&b).unwrap();
        assert_eq!(d.weak, 2);
        assert_eq!(d.split, 1);
        assert_eq!(d.oversized, 3);
        assert_eq!(d.n_clusters, 5);
    }

    #[test]
    fn cluster_diagnostics_all_strong_none_oversized() {
        let qualities = ["strong", "strong", "strong"];
        let oversized = [false, false, false];
        let b = clusters_batch(&qualities, &oversized);
        let d = ClusterDiagnostics::from_batch(&b).unwrap();
        assert_eq!(d.weak, 0);
        assert_eq!(d.split, 0);
        assert_eq!(d.oversized, 0);
        assert_eq!(d.n_clusters, 3);
    }

    #[allow(clippy::too_many_arguments)] // test helper: one arg per ColumnSignal column
    fn column_signals_batch(
        fields: &[&str],
        col_types: &[&str],
        scorers: &[&str],
        in_blocking: &[bool],
        in_negative_evidence: &[bool],
        identity_scores: &[f64],
        corruption_scores: &[f64],
        collision_rates: &[f64],
        cardinality_ratios: &[f64],
        null_rates: &[f64],
        variant_rates: &[f64],
    ) -> RecordBatch {
        use arrow::datatypes::DataType;
        let schema = Arc::new(Schema::new(vec![
            Field::new("field", DataType::Utf8, false),
            Field::new("col_type", DataType::Utf8, false),
            Field::new("scorer", DataType::Utf8, false),
            Field::new("in_blocking", DataType::Boolean, false),
            Field::new("in_negative_evidence", DataType::Boolean, false),
            Field::new("identity_score", DataType::Float64, false),
            Field::new("corruption_score", DataType::Float64, false),
            Field::new("collision_rate", DataType::Float64, false),
            Field::new("cardinality_ratio", DataType::Float64, false),
            Field::new("null_rate", DataType::Float64, false),
            Field::new("variant_rate", DataType::Float64, false),
        ]));
        RecordBatch::try_new(
            schema,
            vec![
                Arc::new(StringArray::from(fields.to_vec())),
                Arc::new(StringArray::from(col_types.to_vec())),
                Arc::new(StringArray::from(scorers.to_vec())),
                Arc::new(BooleanArray::from(in_blocking.to_vec())),
                Arc::new(BooleanArray::from(in_negative_evidence.to_vec())),
                Arc::new(Float64Array::from(identity_scores.to_vec())),
                Arc::new(Float64Array::from(corruption_scores.to_vec())),
                Arc::new(Float64Array::from(collision_rates.to_vec())),
                Arc::new(Float64Array::from(cardinality_ratios.to_vec())),
                Arc::new(Float64Array::from(null_rates.to_vec())),
                Arc::new(Float64Array::from(variant_rates.to_vec())),
            ],
        )
        .unwrap()
    }

    #[test]
    fn column_signals_round_trip() {
        // Row 0: corrupted address column scored with token_sort, used in blocking
        // Row 1: clean id column scored with exact, not in blocking
        let batch = column_signals_batch(
            &["street_address", "record_id"],
            &["text", "id"],
            &["token_sort", "exact"],
            &[true, false],
            &[false, true],
            &[0.72, 0.99],
            &[0.45, 0.02],
            &[0.08, 0.001],
            &[0.88, 1.0],
            &[0.12, 0.0],
            &[0.30, 0.01],
        );
        let signals = column_signals_from_batch(&batch).unwrap();
        assert_eq!(signals.len(), 2);

        let addr = &signals[0];
        assert_eq!(addr.field, "street_address");
        assert_eq!(addr.col_type, "text");
        assert_eq!(addr.scorer, "token_sort");
        assert!(addr.in_blocking);
        assert!(!addr.in_negative_evidence);
        assert!((addr.identity_score - 0.72).abs() < 1e-9);
        assert!((addr.corruption_score - 0.45).abs() < 1e-9);
        assert!((addr.collision_rate - 0.08).abs() < 1e-9);
        assert!((addr.cardinality_ratio - 0.88).abs() < 1e-9);
        assert!((addr.null_rate - 0.12).abs() < 1e-9);
        assert!((addr.variant_rate - 0.30).abs() < 1e-9);

        let id_col = &signals[1];
        assert_eq!(id_col.field, "record_id");
        assert_eq!(id_col.col_type, "id");
        assert_eq!(id_col.scorer, "exact");
        assert!(!id_col.in_blocking);
        assert!(id_col.in_negative_evidence);
        assert!((id_col.identity_score - 0.99).abs() < 1e-9);
        assert!((id_col.corruption_score - 0.02).abs() < 1e-9);
    }

    #[test]
    fn column_signals_missing_column_returns_error() {
        // Build a batch that is missing the `scorer` column — should error cleanly.
        use arrow::datatypes::DataType;
        let schema = Arc::new(Schema::new(vec![
            Field::new("field", DataType::Utf8, false),
            Field::new("col_type", DataType::Utf8, false),
            // scorer intentionally omitted
        ]));
        let batch = RecordBatch::try_new(
            schema,
            vec![
                Arc::new(StringArray::from(vec!["name"])),
                Arc::new(StringArray::from(vec!["text"])),
            ],
        )
        .unwrap();
        let err = column_signals_from_batch(&batch).unwrap_err();
        assert!(err.contains("scorer"), "expected scorer in error: {err}");
    }
}
