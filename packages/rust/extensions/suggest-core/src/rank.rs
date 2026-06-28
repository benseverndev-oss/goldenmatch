use crate::contract::{AcceptancePriors, Suggestion, SuggestionKind};

const SUPPRESS_AFTER_NET_REJECTS: i64 = 2;


/// Canonical priors-map key: `"{snake_case kind}:{target}"`. Pin this here so
/// Plan 2's MemoryStore persistence writes the SAME key -- otherwise the
/// accept/reject loop silently won't bind. Uses the snake_case serde names
/// (NOT Debug-format, which would drop the underscores).
pub fn prior_key(kind: &SuggestionKind, target: &str) -> String {
    let k = match kind {
        SuggestionKind::RaiseThreshold => "raise_threshold",
        SuggestionKind::LowerThreshold => "lower_threshold",
        SuggestionKind::SwapScorer => "swap_scorer",
        SuggestionKind::AddNegativeEvidence => "add_negative_evidence",
        SuggestionKind::DropMatchkey => "drop_matchkey",
    };
    format!("{k}:{target}")
}

pub fn rank(mut suggestions: Vec<Suggestion>, priors: &AcceptancePriors) -> Vec<Suggestion> {
    // dedup by id (keep first)
    let mut seen = std::collections::HashSet::new();
    suggestions.retain(|s| seen.insert(s.id.clone()));

    // suppress repeatedly-rejected (kind,target)
    suggestions.retain(|s| {
        match priors.counts.get(&prior_key(&s.kind, &s.target)) {
            Some((acc, rej)) => (*rej as i64 - *acc as i64) < SUPPRESS_AFTER_NET_REJECTS,
            None => true,
        }
    });

    // score = confidence + acceptance nudge
    suggestions.sort_by(|a, b| {
        score(b, priors)
            .partial_cmp(&score(a, priors))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    suggestions
}

fn score(s: &Suggestion, priors: &AcceptancePriors) -> f64 {
    let nudge = match priors.counts.get(&prior_key(&s.kind, &s.target)) {
        Some((acc, rej)) => 0.05 * (*acc as f64 - *rej as f64),
        None => 0.0,
    };
    s.confidence + nudge
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contract::{ConfigPatch, PredictedEffect};
    use std::collections::HashMap;

    /// Quick Suggestion builder for rank tests. Fills non-essential fields with
    /// placeholders so tests can focus on the fields that drive ranking/suppression.
    fn mk(id: &str, kind: SuggestionKind, target: &str, confidence: f64) -> Suggestion {
        Suggestion {
            id: id.into(),
            kind,
            target: target.into(),
            current_value: "old".into(),
            proposed_value: "new".into(),
            rationale: String::new(),
            predicted_effect: PredictedEffect::PrecisionUp,
            confidence,
            patch: ConfigPatch::SetThreshold {
                matchkey: target.into(),
                value: 0.9,
            },
            evidence: serde_json::Value::Null,
        }
    }

    #[test]
    fn higher_confidence_ranks_first() {
        let suggestions = vec![
            mk("a", SuggestionKind::RaiseThreshold, "name", 0.5),
            mk("b", SuggestionKind::RaiseThreshold, "name", 0.8),
        ];
        let priors = AcceptancePriors::default();
        let ranked = rank(suggestions, &priors);
        assert_eq!(ranked[0].id, "b", "higher confidence should rank first");
        assert_eq!(ranked[1].id, "a");
    }

    #[test]
    fn net_rejects_above_threshold_suppresses() {
        // (0 accepts, 3 rejects) -> net = 3 >= SUPPRESS_AFTER_NET_REJECTS(2) -> suppressed
        let kind = SuggestionKind::RaiseThreshold;
        let target = "name";
        let suggestions = vec![mk("s1", kind.clone(), target, 0.7)];
        let priors = AcceptancePriors {
            counts: HashMap::from([(prior_key(&kind, target), (0u32, 3u32))]),
        };
        let ranked = rank(suggestions, &priors);
        assert!(
            ranked.is_empty(),
            "suggestion with 3 net rejects should be suppressed"
        );
    }

    #[test]
    fn acceptance_history_nudges_above_equal_confidence_no_history() {
        // Two suggestions, same confidence 0.6; one has 3 accepts -> score 0.6 + 0.15 = 0.75
        let kind = SuggestionKind::SwapScorer;
        let target = "address";
        let suggestions = vec![
            mk("with_history", kind.clone(), target, 0.6),
            mk("no_history", SuggestionKind::LowerThreshold, "other", 0.6),
        ];
        let priors = AcceptancePriors {
            counts: HashMap::from([(prior_key(&kind, target), (3u32, 0u32))]),
        };
        let ranked = rank(suggestions, &priors);
        assert_eq!(
            ranked[0].id, "with_history",
            "3 accepts should nudge the score above the equal-confidence no-history suggestion"
        );
    }

    #[test]
    fn duplicate_id_collapses_to_one() {
        let suggestions = vec![
            mk("dup", SuggestionKind::RaiseThreshold, "name", 0.7),
            mk("dup", SuggestionKind::RaiseThreshold, "name", 0.7),
            mk("unique", SuggestionKind::LowerThreshold, "addr", 0.5),
        ];
        let priors = AcceptancePriors::default();
        let ranked = rank(suggestions, &priors);
        assert_eq!(ranked.len(), 2, "duplicate id should collapse to one entry");
        assert!(ranked.iter().filter(|s| s.id == "dup").count() == 1);
    }
}
