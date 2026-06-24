//! Diagnostic reductions from Arrow run artifacts.
//!
//! `ScoreDiagnostics::from_batch` reduces the `scored_pairs` RecordBatch to
//! frame-free stats the suggestion rules consume.  `ClusterDiagnostics::from_batch`
//! does the same for the `clusters` RecordBatch.  Both reuse `analysis_core`
//! kernels -- do NOT add a second histogram / quantile implementation here.

use arrow::array::{Array, BooleanArray, Float64Array, StringArray};
use arrow::record_batch::RecordBatch;

pub struct ScoreDiagnostics {
    pub histogram: Vec<(f64, i64)>,
    pub mass_above: f64,      // fraction of pairs with score >= threshold
    pub mass_just_below: f64, // fraction in [threshold-0.10, threshold)
    pub n_pairs: usize,
}

impl ScoreDiagnostics {
    pub fn from_batch(batch: &RecordBatch, threshold: f64, bins: i64) -> Result<Self, String> {
        let col = batch
            .column_by_name("score")
            .ok_or("missing score column")?;
        let scores = col
            .as_any()
            .downcast_ref::<Float64Array>()
            .ok_or("score not f64")?;
        let vals: Vec<f64> = scores.iter().flatten().collect();
        let n = vals.len();
        if n == 0 {
            return Ok(Self {
                histogram: vec![],
                mass_above: 0.0,
                mass_just_below: 0.0,
                n_pairs: 0,
            });
        }
        let above = vals.iter().filter(|&&s| s >= threshold).count();
        let band_lo = threshold - 0.10;
        let just_below = vals
            .iter()
            .filter(|&&s| s >= band_lo && s < threshold)
            .count();
        // Reuse analysis-core histogram (no second implementation).
        let histogram = analysis_core::histogram(&vals, bins);
        Ok(Self {
            histogram,
            mass_above: above as f64 / n as f64,
            mass_just_below: just_below as f64 / n as f64,
            n_pairs: n,
        })
    }

    /// Lowest-count bin strictly between the two highest-mass regions -- the
    /// bimodality "dip". Returns the bin's left edge, or None if no clear valley.
    pub fn dip(&self) -> Option<f64> {
        if self.histogram.len() < 3 {
            return None;
        }
        let counts: Vec<i64> = self.histogram.iter().map(|(_, c)| *c).collect();
        let peak = *counts.iter().max().unwrap();
        // find the global min that has a higher-count bin on BOTH sides
        let mut best: Option<(usize, i64)> = None;
        for i in 1..counts.len() - 1 {
            let left_max = counts[..i].iter().max().copied().unwrap_or(0);
            let right_max = counts[i + 1..].iter().max().copied().unwrap_or(0);
            if left_max > counts[i] && right_max > counts[i] {
                if best.map_or(true, |(_, c)| counts[i] < c) {
                    best = Some((i, counts[i]));
                }
            }
        }
        // require the valley to be a real dip (< 25% of peak) to avoid noise
        best.filter(|&(_, c)| (c as f64) < 0.25 * peak as f64)
            .map(|(i, _)| self.histogram[i].0)
    }
}

pub struct ClusterDiagnostics {
    pub weak: usize,
    pub oversized: usize,
    pub split: usize,
    pub n_clusters: usize,
}

impl ClusterDiagnostics {
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

        let mut weak = 0usize;
        let mut split = 0usize;
        for v in quality.iter().flatten() {
            match v {
                "weak" => weak += 1,
                "split" => split += 1,
                _ => {}
            }
        }

        let oversized = oversized_arr.iter().flatten().filter(|&b| b).count();

        Ok(Self {
            weak,
            oversized,
            split,
            n_clusters,
        })
    }
}

#[cfg(test)]
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
}
