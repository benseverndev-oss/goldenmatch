use crate::contract::*;
use crate::diagnostics::{ColumnSignal, ScoreDiagnostics};

pub(crate) const EVERYTHING_MATCHES: f64 = 0.90; // mirrors controller precision_collapse_floor
const RECALL_RISK_BAND: f64 = 0.20; // fraction just-below to call recall risk
const DIP_MIN_GAP: f64 = 0.05; // min |dip - threshold| before the dip suggestion fires
const RECALL_STEP_DOWN: f64 = 0.05; // fixed lowering step for the recall-risk suggestion

const SWAP_CORRUPTION_MIN: f64 = 0.30; // corruption_score at/above which token_sort is noise-fragile
const SWAP_VARIANT_MIN: f64 = 0.02; // fraction of fuzzy-variant rows that also triggers the swap
const SWAP_TARGET_SCORER: &str = "jaro_winkler";

/// Returns config suggestions for a single matchkey's threshold.
///
/// `weak_clusters` / `oversized_clusters` are counts from ClusterDiagnostics.
pub fn threshold_rule(
    matchkey: &str,
    current: f64,
    sd: &ScoreDiagnostics,
    weak_clusters: usize,
    oversized_clusters: usize,
) -> Vec<Suggestion> {
    let mut out = Vec::new();

    // (a) bimodality dip not aligned with current threshold
    if let Some(dip) = sd.dip() {
        if (dip - current).abs() > DIP_MIN_GAP {
            let kind = if dip > current {
                SuggestionKind::RaiseThreshold
            } else {
                SuggestionKind::LowerThreshold
            };
            let effect = if dip > current {
                PredictedEffect::PrecisionUp
            } else {
                PredictedEffect::RecallUp
            };
            out.push(Suggestion {
                id: format!("thr:dip:{matchkey}"),
                kind: kind.clone(),
                target: matchkey.into(),
                current_value: format!("{current:.2}"),
                proposed_value: format!("{:.2}", round2(dip)),
                rationale: format!(
                    "Pair scores split into two groups with a gap near {dip:.2}, but the \
                     `{matchkey}` threshold sits at {current:.2}. Moving it to {dip:.2} \
                     separates the two groups cleanly."
                ),
                predicted_effect: effect,
                confidence: 0.7,
                patch: ConfigPatch::SetThreshold {
                    matchkey: matchkey.into(),
                    value: round2(dip),
                },
                evidence: serde_json::json!({"dip": dip, "current": current}),
            });
        }
    }

    // (b) "everything matches" -> raise
    if sd.mass_above > EVERYTHING_MATCHES {
        let proposed = round2((current + 1.0) / 2.0); // halfway to 1.0
        out.push(Suggestion {
            id: format!("thr:raise:{matchkey}"),
            kind: SuggestionKind::RaiseThreshold,
            target: matchkey.into(),
            current_value: format!("{current:.2}"),
            proposed_value: format!("{proposed:.2}"),
            rationale: format!(
                "{:.0}% of scored pairs clear the `{matchkey}` threshold of {current:.2} -- \
                 almost everything is matching, which usually means false merges. Raising it \
                 to {proposed:.2} tightens the match.",
                sd.mass_above * 100.0
            ),
            predicted_effect: PredictedEffect::PrecisionUp,
            confidence: 0.6,
            patch: ConfigPatch::SetThreshold {
                matchkey: matchkey.into(),
                value: proposed,
            },
            evidence: serde_json::json!({"mass_above": sd.mass_above}),
        });
    }

    // (c) recall risk: mass just below + weak/oversized clusters -> lower
    if sd.mass_just_below > RECALL_RISK_BAND && (weak_clusters + oversized_clusters) > 0 {
        let proposed = round2(current - RECALL_STEP_DOWN);
        out.push(Suggestion {
            id: format!("thr:lower:{matchkey}"),
            kind: SuggestionKind::LowerThreshold,
            target: matchkey.into(),
            current_value: format!("{current:.2}"),
            proposed_value: format!("{proposed:.2}"),
            rationale: format!(
                "{:.0}% of pairs score just below the `{matchkey}` threshold ({current:.2}), \
                 and there are weak/oversized clusters nearby -- likely missed matches. \
                 Lowering it to {proposed:.2} recovers them.",
                sd.mass_just_below * 100.0
            ),
            predicted_effect: PredictedEffect::RecallUp,
            confidence: 0.5,
            patch: ConfigPatch::SetThreshold {
                matchkey: matchkey.into(),
                value: proposed,
            },
            evidence: serde_json::json!({
                "mass_just_below": sd.mass_just_below,
                "weak": weak_clusters,
                "oversized": oversized_clusters
            }),
        });
    }

    out
}

/// Returns scorer-swap suggestions for each column signal in a matchkey.
///
/// Fires when a free-text/name/address column is still scored by `token_sort`
/// but carries corruption or variant-rate signal above the threshold.  `token_sort`
/// is robust to word reordering but fragile to character-level noise; `jaro_winkler`
/// handles the noise better.  Mirrors the #662 noise-aware auto-config behavior.
pub fn scorer_swap_rule(matchkey: &str, signals: &[ColumnSignal]) -> Vec<Suggestion> {
    let mut out = Vec::new();
    for c in signals {
        let is_free_text = matches!(c.col_type.as_str(), "address" | "string" | "name");
        let is_token_sort = c.scorer == "token_sort";
        let is_noisy = c.corruption_score >= SWAP_CORRUPTION_MIN
            || c.variant_rate >= SWAP_VARIANT_MIN;
        if !is_free_text || !is_token_sort || !is_noisy {
            continue;
        }
        let trigger = if c.corruption_score >= SWAP_CORRUPTION_MIN {
            format!(
                "corruption_score {:.0}%",
                c.corruption_score * 100.0
            )
        } else {
            format!("variant_rate {:.0}%", c.variant_rate * 100.0)
        };
        out.push(Suggestion {
            id: format!("swap:{}", c.field),
            kind: SuggestionKind::SwapScorer,
            target: c.field.clone(),
            current_value: "token_sort".into(),
            proposed_value: SWAP_TARGET_SCORER.into(),
            rationale: format!(
                "`{}` has {} signal, but `token_sort` is robust to word reordering \
                 while fragile to character noise. Switching to `jaro_winkler` will score \
                 the corrupted values better.",
                c.field, trigger
            ),
            predicted_effect: PredictedEffect::PrecisionUp,
            confidence: 0.65,
            patch: ConfigPatch::SetScorer {
                matchkey: matchkey.into(),
                field: c.field.clone(),
                scorer: SWAP_TARGET_SCORER.into(),
            },
            evidence: serde_json::json!({
                "corruption_score": c.corruption_score,
                "variant_rate": c.variant_rate,
                "col_type": c.col_type
            }),
        });
    }
    out
}

const NE_IDENTITY_MIN: f64 = 0.75; // identity_score floor (mirrors _IDENTITY_SCORE_THRESHOLD)
const NE_CARDINALITY_MIN: f64 = 0.50; // cardinality_ratio floor (mirrors _CARDINALITY_THRESHOLD)
const NE_COLLISION_MIN: f64 = 0.50; // in-cluster disagreement rate that flags over-trust

/// Returns add-negative-evidence suggestions for each column signal.
///
/// Fires when a column looks like a strong identity field (high identity_score,
/// high cardinality_ratio) that is NOT already in negative evidence, but disagrees
/// within merged clusters at a rate above the collision threshold.  Adding it as
/// negative evidence penalises future merges where the field conflicts.  Mirrors
/// the shipped `promote_negative_evidence` thresholds plus a result-driven
/// collision gate.
pub fn negative_evidence_rule(signals: &[ColumnSignal]) -> Vec<Suggestion> {
    let mut out = Vec::new();
    for c in signals {
        if c.identity_score < NE_IDENTITY_MIN
            || c.cardinality_ratio < NE_CARDINALITY_MIN
            || c.in_negative_evidence
            || c.collision_rate < NE_COLLISION_MIN
        {
            continue;
        }
        out.push(Suggestion {
            id: format!("ne:{}", c.field),
            kind: SuggestionKind::AddNegativeEvidence,
            target: c.field.clone(),
            current_value: "none".into(),
            proposed_value: "negative_evidence".into(),
            rationale: format!(
                "`{}` looks like a strong identity column (identity_score {:.2}, \
                 cardinality_ratio {:.2}) yet disagrees within {:.0}% of merged clusters. \
                 Adding it as negative evidence will penalise merges where it conflicts.",
                c.field,
                c.identity_score,
                c.cardinality_ratio,
                c.collision_rate * 100.0
            ),
            predicted_effect: PredictedEffect::PrecisionUp,
            confidence: 0.55,
            patch: ConfigPatch::AddNegativeEvidence {
                field: c.field.clone(),
            },
            evidence: serde_json::json!({
                "identity_score": c.identity_score,
                "collision_rate": c.collision_rate,
                "cardinality_ratio": c.cardinality_ratio
            }),
        });
    }
    out
}

/// Returns a drop-matchkey suggestion when an over-broad `exact` matchkey on a
/// DERIVED column is the likely over-merge source.
///
/// Fires only when ALL hold:
///   1. `precision_collapsed` -- a thresholded matchkey shows the "everything
///      matches" signal (mass_above > EVERYTHING_MATCHES), so raising a fuzzy
///      threshold alone won't fix precision.
///   2. There is an `exact` matchkey whose field is a DERIVED/internal column
///      (name starts with `__`, e.g. the domain-extraction `__title_key__`).
///      A normalized-key exact match is a blocking-grade signal masquerading as
///      an identity claim; we NEVER target a user column.
///   3. The config has >= 2 matchkeys, so dropping one still leaves recall
///      coverage from the others.
///
/// The suggestion is deliberately aggressive (it removes a rule), so it relies
/// on the caller's self-verify pass (apply -> re-run -> keep only if the
/// unsupervised health proxy improves) as the safety net. Mirrors the
/// DBLP-ACM finding (#1299) where an `exact __title_key__` matchkey collapsed
/// precision and dropping it was the only lever that helped.
pub fn drop_overmerge_matchkey_rule(
    config: &ConfigSummary,
    precision_collapsed: bool,
) -> Vec<Suggestion> {
    if !precision_collapsed || config.matchkeys.len() < 2 {
        return Vec::new();
    }
    for mk in &config.matchkeys {
        if mk.kind != "exact" {
            continue;
        }
        // Only ever propose dropping an exact matchkey built on a DERIVED column
        // (internal `__*__` name). A user's real identifier column is never touched.
        let derived_field = mk
            .fields
            .iter()
            .find(|f| f.field.starts_with("__") && f.field.ends_with("__"));
        let Some(field) = derived_field else {
            continue;
        };
        return vec![Suggestion {
            id: format!("drop:{}", mk.name),
            kind: SuggestionKind::DropMatchkey,
            target: mk.name.clone(),
            current_value: "exact matchkey present".into(),
            proposed_value: "dropped".into(),
            rationale: format!(
                "Almost every pair is matching (precision looks collapsed), and the \
                 `{}` matchkey is an exact match on the derived key `{}`. A normalized-key \
                 exact match merges every record sharing that key regardless of other \
                 fields, which over-merges. Dropping it lets the remaining matchkeys carry \
                 recall; the change is re-run and kept only if cluster health improves.",
                mk.name, field.field
            ),
            predicted_effect: PredictedEffect::PrecisionUp,
            confidence: 0.6,
            patch: ConfigPatch::DropMatchkey {
                matchkey: mk.name.clone(),
            },
            evidence: serde_json::json!({
                "matchkey": mk.name,
                "derived_field": field.field,
                "precision_collapsed": precision_collapsed,
                "n_matchkeys": config.matchkeys.len(),
            }),
        }];
    }
    Vec::new()
}

fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::diagnostics::{ColumnSignal, ScoreDiagnostics};

    fn sd(mass_above: f64, mass_just_below: f64, dip: Option<f64>) -> ScoreDiagnostics {
        // construct directly for unit isolation
        ScoreDiagnostics {
            histogram: dip
                .map(|_| vec![(0.0, 100), (0.5, 2), (0.9, 100)])
                .unwrap_or_else(|| vec![(0.0, 50), (0.5, 50)]),
            mass_above,
            mass_just_below,
            n_pairs: 1000,
        }
    }

    #[test]
    fn raises_on_everything_matches() {
        let out = threshold_rule("name", 0.80, &sd(0.95, 0.0, None), 0, 0);
        assert!(out
            .iter()
            .any(|s| s.kind == SuggestionKind::RaiseThreshold));
    }

    #[test]
    fn lowers_on_recall_risk() {
        // lots of mass just below + weak/oversized clusters present
        let out = threshold_rule("name", 0.80, &sd(0.10, 0.30, None), 5, 2);
        assert!(out
            .iter()
            .any(|s| s.kind == SuggestionKind::LowerThreshold));
    }

    #[test]
    fn moves_to_dip_when_threshold_off_valley() {
        let out = threshold_rule("name", 0.80, &sd(0.4, 0.05, Some(0.5)), 0, 0);
        let s = out
            .iter()
            .find(|s| s.id == "thr:dip:name")
            .unwrap();
        // dip at 0.5 is below current 0.80 -> suggest lowering toward the valley
        assert!(
            matches!(&s.patch, ConfigPatch::SetThreshold { value, .. } if (*value - 0.5).abs() < 1e-9)
        );
    }

    #[test]
    fn no_suggestions_when_nothing_triggered() {
        // mass_above 0.5 (< 0.90), mass_just_below 0.05 (< 0.20), no dip
        let out = threshold_rule("name", 0.80, &sd(0.50, 0.05, None), 0, 0);
        assert!(out.is_empty());
    }

    // ---------------------------------------------------------------------------
    // scorer_swap_rule helpers + tests
    // ---------------------------------------------------------------------------

    /// Build a ColumnSignal with sane defaults; override the fields under test.
    fn cs(
        field: &str,
        col_type: &str,
        scorer: &str,
        corruption_score: f64,
        variant_rate: f64,
    ) -> ColumnSignal {
        ColumnSignal {
            field: field.into(),
            col_type: col_type.into(),
            scorer: scorer.into(),
            in_blocking: false,
            in_negative_evidence: false,
            identity_score: 0.5,
            corruption_score,
            collision_rate: 0.0,
            cardinality_ratio: 0.5,
            null_rate: 0.0,
            variant_rate,
        }
    }

    #[test]
    fn swaps_token_sort_on_corrupted_address() {
        // address + token_sort + corruption_score 0.5 (>= 0.30) -> one SwapScorer suggestion
        let signals = vec![cs("res_street_address", "address", "token_sort", 0.5, 0.0)];
        let out = scorer_swap_rule("person", &signals);
        assert_eq!(out.len(), 1, "expected exactly one suggestion");
        let s = &out[0];
        assert_eq!(s.kind, SuggestionKind::SwapScorer);
        assert_eq!(s.id, "swap:res_street_address");
        assert_eq!(s.current_value, "token_sort");
        assert_eq!(s.proposed_value, "jaro_winkler");
        assert!(
            matches!(&s.patch, ConfigPatch::SetScorer { matchkey, field, scorer }
                if matchkey == "person" && field == "res_street_address" && scorer == "jaro_winkler")
        );
    }

    #[test]
    fn swaps_on_high_variant_even_if_low_corruption() {
        // name + token_sort + variant_rate 0.05 (>= 0.02), corruption 0.0 -> fires
        let signals = vec![cs("given_name", "name", "token_sort", 0.0, 0.05)];
        let out = scorer_swap_rule("person", &signals);
        assert_eq!(out.len(), 1, "expected one suggestion from variant_rate trigger");
        assert_eq!(out[0].kind, SuggestionKind::SwapScorer);
    }

    #[test]
    fn no_swap_on_clean_column() {
        // corruption 0.1 (< 0.30) and variant_rate 0.0 (< 0.02) -> empty
        let signals = vec![cs("address_line", "address", "token_sort", 0.1, 0.0)];
        let out = scorer_swap_rule("person", &signals);
        assert!(out.is_empty(), "clean column should emit no suggestion");
    }

    #[test]
    fn no_swap_when_not_token_sort() {
        // qgram scorer with high corruption -> no swap (rule only targets token_sort)
        let signals = vec![cs("address_line", "address", "qgram", 0.9, 0.0)];
        let out = scorer_swap_rule("person", &signals);
        assert!(out.is_empty(), "non-token_sort scorer should not trigger swap");
    }

    // ---------------------------------------------------------------------------
    // negative_evidence_rule tests
    // ---------------------------------------------------------------------------

    /// Build a ColumnSignal for NE-rule tests, reusing the cs() helper's base
    /// but overriding the NE-relevant fields explicitly.
    fn ne_signal(
        field: &str,
        identity_score: f64,
        cardinality_ratio: f64,
        collision_rate: f64,
        in_negative_evidence: bool,
    ) -> ColumnSignal {
        ColumnSignal {
            field: field.into(),
            col_type: "string".into(),
            scorer: "exact".into(),
            in_blocking: false,
            in_negative_evidence,
            identity_score,
            corruption_score: 0.0,
            collision_rate,
            cardinality_ratio,
            null_rate: 0.0,
            variant_rate: 0.0,
        }
    }

    #[test]
    fn adds_ne_for_colliding_identity_column() {
        // identity 0.9, cardinality 0.8, collision 0.6, not already NE -> one suggestion
        let signals = vec![ne_signal("npi", 0.9, 0.8, 0.6, false)];
        let out = negative_evidence_rule(&signals);
        assert_eq!(out.len(), 1, "expected exactly one suggestion");
        let s = &out[0];
        assert_eq!(s.kind, SuggestionKind::AddNegativeEvidence);
        assert_eq!(s.id, "ne:npi");
        assert_eq!(s.current_value, "none");
        assert_eq!(s.proposed_value, "negative_evidence");
        assert!(
            matches!(&s.patch, ConfigPatch::AddNegativeEvidence { field } if field == "npi"),
            "patch should be AddNegativeEvidence for npi"
        );
    }

    #[test]
    fn no_ne_when_already_negative_evidence() {
        // same strong signals but already in NE -> empty
        let signals = vec![ne_signal("npi", 0.9, 0.8, 0.6, true)];
        let out = negative_evidence_rule(&signals);
        assert!(out.is_empty(), "already-NE column should not re-suggest NE");
    }

    #[test]
    fn no_ne_when_low_collision() {
        // identity 0.9, cardinality 0.8, but collision 0.1 (< 0.50) -> empty
        let signals = vec![ne_signal("npi", 0.9, 0.8, 0.1, false)];
        let out = negative_evidence_rule(&signals);
        assert!(out.is_empty(), "low collision_rate should not trigger NE");
    }

    #[test]
    fn no_ne_when_low_identity() {
        // identity 0.3 (< 0.75), cardinality 0.8, collision 0.9 -> empty
        let signals = vec![ne_signal("weak_field", 0.3, 0.8, 0.9, false)];
        let out = negative_evidence_rule(&signals);
        assert!(out.is_empty(), "low identity_score should not trigger NE");
    }

    // ---------------------------------------------------------------------------
    // drop_overmerge_matchkey_rule helpers + tests
    // ---------------------------------------------------------------------------

    fn mk_summary(name: &str, kind: &str, field: &str) -> MatchkeySummary {
        MatchkeySummary {
            name: name.into(),
            kind: kind.into(),
            threshold: if kind == "exact" { None } else { Some(0.7) },
            fields: vec![FieldSummary {
                field: field.into(),
                scorer: None,
                weight: None,
            }],
        }
    }

    fn cfg(matchkeys: Vec<MatchkeySummary>) -> ConfigSummary {
        ConfigSummary {
            matchkeys,
            negative_evidence: Vec::new(),
        }
    }

    #[test]
    fn drops_exact_derived_key_when_precision_collapsed() {
        let config = cfg(vec![
            mk_summary("fuzzy_match", "weighted", "title"),
            mk_summary("title_key", "exact", "__title_key__"),
        ]);
        let out = drop_overmerge_matchkey_rule(&config, true);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].kind, SuggestionKind::DropMatchkey);
        assert_eq!(out[0].target, "title_key");
        assert!(matches!(
            &out[0].patch,
            ConfigPatch::DropMatchkey { matchkey } if matchkey == "title_key"
        ));
    }

    #[test]
    fn no_drop_when_not_collapsed() {
        let config = cfg(vec![
            mk_summary("fuzzy_match", "weighted", "title"),
            mk_summary("title_key", "exact", "__title_key__"),
        ]);
        assert!(drop_overmerge_matchkey_rule(&config, false).is_empty());
    }

    #[test]
    fn no_drop_when_only_one_matchkey() {
        // dropping the last matchkey would destroy recall -> never propose it.
        let config = cfg(vec![mk_summary("title_key", "exact", "__title_key__")]);
        assert!(drop_overmerge_matchkey_rule(&config, true).is_empty());
    }

    #[test]
    fn no_drop_when_exact_key_is_a_user_column() {
        // an exact match on a real user column (e.g. email) is a legitimate
        // identity claim -- never targeted, only derived `__*__` keys are.
        let config = cfg(vec![
            mk_summary("fuzzy_match", "weighted", "name"),
            mk_summary("email_key", "exact", "email"),
        ]);
        assert!(drop_overmerge_matchkey_rule(&config, true).is_empty());
    }
}
