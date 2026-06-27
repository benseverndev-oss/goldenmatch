//! Top-level `suggest()` entry point -- integrates all kernel modules over Arrow batches.

use arrow::record_batch::RecordBatch;

use crate::contract::{AcceptancePriors, ConfigSummary, Suggestion};
use crate::diagnostics::{column_signals_from_batch, ClusterDiagnostics, ScoreDiagnostics};
use crate::rank::rank;
use crate::rules::{negative_evidence_rule, scorer_swap_rule, threshold_rule};

/// Bins for the score histogram feeding dip() detection.
const HISTOGRAM_BINS: i64 = 24;

/// Main entry point for the config-suggestion kernel.
///
/// Accepts three Arrow `RecordBatch`es produced by a finished goldenmatch run:
/// - `scored_pairs`:   schema `id_a:i64, id_b:i64, score:f64` (one row per candidate pair)
/// - `clusters`:       schema `cluster_id:i64, size:i64, confidence:f64, quality:utf8, oversized:bool`
/// - `column_signals`: schema `field:utf8, col_type:utf8, scorer:utf8, in_blocking:bool,
///                      in_negative_evidence:bool, identity_score:f64, corruption_score:f64,
///                      collision_rate:f64, cardinality_ratio:f64, null_rate:f64, variant_rate:f64`
///
/// `config_json` must serialise to `ConfigSummary`; `priors_json` to `AcceptancePriors`.
///
/// Returns a JSON array of `Suggestion` objects sorted by descending ranking score.
pub fn suggest(
    scored_pairs: &RecordBatch,
    clusters: &RecordBatch,
    column_signals: &RecordBatch,
    config_json: &str,
    priors_json: &str,
) -> Result<String, String> {
    // 1. Parse inputs.
    let config: ConfigSummary =
        serde_json::from_str(config_json).map_err(|e| e.to_string())?;
    let priors: AcceptancePriors =
        serde_json::from_str(priors_json).map_err(|e| e.to_string())?;

    // 2. One-shot reductions shared across all matchkeys.
    let cluster_diag = ClusterDiagnostics::from_batch(clusters)?;
    let signals = column_signals_from_batch(column_signals)?;

    // 3. Collect suggestions.
    let mut all: Vec<Suggestion> = Vec::new();

    // Per-matchkey threshold + scorer-swap rules.
    // v1 simplification: ClusterDiagnostics counts weak/oversized GLOBALLY
    // and those global counts are passed to every matchkey's threshold_rule.
    // This is correct for the common zero-config case where a single weighted
    // matchkey dominates; revisit when multi-matchkey configs are common.
    for mk in &config.matchkeys {
        if let Some(t) = mk.threshold {
            let sd = ScoreDiagnostics::from_batch(scored_pairs, t, HISTOGRAM_BINS)?;
            all.extend(threshold_rule(
                &mk.name,
                t,
                &sd,
                cluster_diag.weak,
                cluster_diag.oversized,
            ));
        }
        // scorer_swap runs for every matchkey (threshold or not); rank() deduplicates
        // by id ("swap:{field}") so duplicates from multi-matchkey configs are harmless.
        all.extend(scorer_swap_rule(&mk.name, &signals));
    }

    // negative_evidence_rule produces at most one suggestion per field and is
    // matchkey-agnostic, so run it once globally.
    all.extend(negative_evidence_rule(&signals));

    // 4. Rank (deduplicates by id, suppresses repeatedly-rejected, sorts by score).
    let ranked = rank(all, &priors);

    // 5. Serialise.
    serde_json::to_string(&ranked).map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// Tests (both unit and golden live here to avoid a dev-dependency on arrow
// in a separate `tests/` integration crate).
// ---------------------------------------------------------------------------
#[cfg(test)]
mod tests {
    use super::*;
    use crate::diagnostics::ColumnSignal;
    use arrow::array::{BooleanArray, Float64Array, Int64Array, StringArray};
    use arrow::datatypes::{DataType, Field, Schema};
    use arrow::record_batch::RecordBatch;
    use std::sync::Arc;

    // -----------------------------------------------------------------------
    // Batch builders (mirrors the helpers in diagnostics.rs tests)
    // -----------------------------------------------------------------------

    /// Empty scored_pairs batch (0 rows, correct schema).
    fn empty_scored_pairs() -> RecordBatch {
        let schema = Arc::new(Schema::new(vec![
            Field::new("id_a", DataType::Int64, false),
            Field::new("id_b", DataType::Int64, false),
            Field::new("score", DataType::Float64, false),
        ]));
        RecordBatch::try_new(
            schema,
            vec![
                Arc::new(Int64Array::from(Vec::<i64>::new())),
                Arc::new(Int64Array::from(Vec::<i64>::new())),
                Arc::new(Float64Array::from(Vec::<f64>::new())),
            ],
        )
        .unwrap()
    }

    /// Empty clusters batch (0 rows, correct schema).
    fn empty_clusters() -> RecordBatch {
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
                Arc::new(Int64Array::from(Vec::<i64>::new())),
                Arc::new(Int64Array::from(Vec::<i64>::new())),
                Arc::new(Float64Array::from(Vec::<f64>::new())),
                Arc::new(StringArray::from(Vec::<&str>::new())),
                Arc::new(BooleanArray::from(Vec::<bool>::new())),
            ],
        )
        .unwrap()
    }

    /// Build a column_signals batch by reading the fields off each `ColumnSignal`.
    /// Taking `&[ColumnSignal]` (vs 11 positional slices) makes each call site read
    /// what it means and removes the silent-transposition footgun.
    fn column_signals_batch(rows: &[ColumnSignal]) -> RecordBatch {
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
                Arc::new(StringArray::from(
                    rows.iter().map(|c| c.field.as_str()).collect::<Vec<_>>(),
                )),
                Arc::new(StringArray::from(
                    rows.iter().map(|c| c.col_type.as_str()).collect::<Vec<_>>(),
                )),
                Arc::new(StringArray::from(
                    rows.iter().map(|c| c.scorer.as_str()).collect::<Vec<_>>(),
                )),
                Arc::new(BooleanArray::from(
                    rows.iter().map(|c| c.in_blocking).collect::<Vec<_>>(),
                )),
                Arc::new(BooleanArray::from(
                    rows.iter().map(|c| c.in_negative_evidence).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    rows.iter().map(|c| c.identity_score).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    rows.iter().map(|c| c.corruption_score).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    rows.iter().map(|c| c.collision_rate).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    rows.iter().map(|c| c.cardinality_ratio).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    rows.iter().map(|c| c.null_rate).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    rows.iter().map(|c| c.variant_rate).collect::<Vec<_>>(),
                )),
            ],
        )
        .unwrap()
    }

    // -----------------------------------------------------------------------
    // Unit test: scorer-swap should be the top suggestion when scored_pairs
    // is empty (no threshold suggestions) and we have a corrupted address
    // column using token_sort.
    // -----------------------------------------------------------------------

    #[test]
    fn scorer_swap_is_top_when_no_threshold_signal() {
        let pairs = empty_scored_pairs();
        let clusters = empty_clusters();
        let signals = column_signals_batch(&[ColumnSignal {
            field: "res_street_address".into(),
            col_type: "address".into(),
            scorer: "token_sort".into(),
            in_blocking: false,
            in_negative_evidence: false,
            identity_score: 0.0,
            corruption_score: 0.6, // >= 0.30 -> triggers swap
            collision_rate: 0.0,
            cardinality_ratio: 0.5,
            null_rate: 0.0,
            variant_rate: 0.0,
        }]);
        let config_json = r#"{
            "matchkeys": [{
                "name": "person",
                "kind": "weighted",
                "threshold": 0.8,
                "fields": [{"field": "res_street_address", "scorer": "token_sort", "weight": 1.0}]
            }],
            "negative_evidence": []
        }"#;
        let priors_json = r#"{"counts": {}}"#;

        let result_json = suggest(&pairs, &clusters, &signals, config_json, priors_json)
            .expect("suggest should not error");

        let suggestions: Vec<serde_json::Value> =
            serde_json::from_str(&result_json).expect("result should be valid JSON array");

        assert!(!suggestions.is_empty(), "should produce at least one suggestion");
        let top = &suggestions[0];
        assert_eq!(
            top["kind"].as_str().unwrap(),
            "swap_scorer",
            "top suggestion should be swap_scorer, got: {top}"
        );
        assert_eq!(
            top["target"].as_str().unwrap(),
            "res_street_address",
            "top suggestion target should be res_street_address"
        );
    }

    // -----------------------------------------------------------------------
    // Golden-vector test: loads tests/golden/ncvr_address.json and asserts
    // the kernel output matches the expected top suggestion.  This is the
    // determinism pin.
    // -----------------------------------------------------------------------

    #[test]
    fn golden_ncvr_address() {
        let fixture_path =
            concat!(env!("CARGO_MANIFEST_DIR"), "/tests/golden/ncvr_address.json");
        let raw = std::fs::read_to_string(fixture_path)
            .unwrap_or_else(|e| panic!("failed to read golden fixture: {e}"));

        let doc: serde_json::Value =
            serde_json::from_str(&raw).expect("golden fixture must be valid JSON");

        // Build column_signals RecordBatch from the JSON array in the fixture.
        let cs_rows = doc["column_signals"].as_array().expect("column_signals array");
        let signals: Vec<ColumnSignal> = cs_rows
            .iter()
            .map(|row| ColumnSignal {
                field: row["field"].as_str().unwrap().to_owned(),
                col_type: row["col_type"].as_str().unwrap().to_owned(),
                scorer: row["scorer"].as_str().unwrap().to_owned(),
                in_blocking: row["in_blocking"].as_bool().unwrap(),
                in_negative_evidence: row["in_negative_evidence"].as_bool().unwrap(),
                identity_score: row["identity_score"].as_f64().unwrap(),
                corruption_score: row["corruption_score"].as_f64().unwrap(),
                collision_rate: row["collision_rate"].as_f64().unwrap(),
                cardinality_ratio: row["cardinality_ratio"].as_f64().unwrap(),
                null_rate: row["null_rate"].as_f64().unwrap(),
                variant_rate: row["variant_rate"].as_f64().unwrap(),
            })
            .collect();
        let signals_batch = column_signals_batch(&signals);

        // This golden scenario is scorer-swap only: it uses empty scored_pairs and
        // clusters by design (no threshold/cluster signal), so the fixture carries
        // no pairs/clusters arrays -- the batches are built empty unconditionally.
        let pairs = empty_scored_pairs();
        let clusters = empty_clusters();

        let config_json = serde_json::to_string(&doc["config"]).unwrap();
        let priors_json = serde_json::to_string(&doc["priors"]).unwrap();

        let result_json = suggest(&pairs, &clusters, &signals_batch, &config_json, &priors_json)
            .expect("golden suggest should not error");

        let suggestions: Vec<serde_json::Value> =
            serde_json::from_str(&result_json).expect("result should be valid JSON array");

        let expected_top = &doc["expected_top"];
        assert!(
            !suggestions.is_empty(),
            "golden test should produce at least one suggestion"
        );
        let top = &suggestions[0];
        assert_eq!(
            top["kind"].as_str().unwrap(),
            expected_top["kind"].as_str().unwrap(),
            "golden top suggestion kind mismatch"
        );
        assert_eq!(
            top["target"].as_str().unwrap(),
            expected_top["target"].as_str().unwrap(),
            "golden top suggestion target mismatch"
        );
    }
}
