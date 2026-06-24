use crate::contract::*;
use crate::diagnostics::ScoreDiagnostics;

const EVERYTHING_MATCHES: f64 = 0.90; // mirrors controller precision_collapse_floor
const RECALL_RISK_BAND: f64 = 0.20; // fraction just-below to call recall risk

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
        if (dip - current).abs() > 0.05 {
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
                proposed_value: format!("{dip:.2}"),
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
        let proposed = round2(current - 0.05);
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

fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::diagnostics::ScoreDiagnostics;

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
            .find(|s| matches!(s.patch, ConfigPatch::SetThreshold { .. }))
            .unwrap();
        // dip at 0.5 is below current 0.80 -> suggest lowering toward the valley
        assert!(
            matches!(&s.patch, ConfigPatch::SetThreshold { value, .. } if (*value - 0.5).abs() < 0.11)
        );
    }
}
