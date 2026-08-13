//! Golden-record survivorship, pyo3-free, byte-identical to
//! `goldenmatch.core.golden.merge_field` **as the Spark tier calls it**.
//!
//! # Why this exists
//!
//! It did not. Verified against `origin/main`: `merge_field` appeared in exactly
//! one Rust file, `native/src/golden.rs`, which is pyo3 + pyarrow throughout AND
//! is a different thing -- the fused columnar kernel returning INDICES for the
//! one-box path, not the per-cluster value merge. So survivorship was Python-only
//! and `spark/golden.py` had to be an `arrow_udf`.
//!
//! # Scope, and why it is narrow on purpose
//!
//! `spark/golden.py` calls `merge_field(values, rule)` -- values and a strategy,
//! nothing else. No sources, no dates, no quality weights, no pair scores, and
//! it discards the confidence and source index, keeping only the value. This
//! crate implements exactly that call.
//!
//! The strategies needing the arguments Spark does not pass are [`Refused`], not
//! approximated:
//!
//! - `source_priority` -- Python RAISES without a sources list. Guessing an
//!   order would silently prefer the wrong system of record.
//! - `most_recent` -- Python RAISES without dates. Without them there is no
//!   "recent"; picking the first row would be an arbitrary answer wearing a
//!   deterministic hat.
//! - `custom:*` -- arbitrary Python from a plugin registry.
//!
//! A survivor chosen by a different rule is a **wrong golden record that looks
//! right**: no exception, no null, just a plausible value in the output that
//! nothing downstream can flag.
//!
//! # Ordering is semantic here, not incidental
//!
//! Python's `Counter.most_common(1)` breaks count ties by INSERTION order, and
//! `max()` over a list keeps the FIRST maximum. Every tie-break below preserves
//! first-encountered order for that reason. A `HashMap` would lose it, so the
//! vote tally is an insertion-ordered `Vec`.

/// A strategy this crate deliberately does not run, and why.
#[derive(Debug, PartialEq, Eq)]
pub struct Refused(pub String);

/// Strategies runnable from values alone -- the Spark call site's whole surface.
pub const SUPPORTED: &[&str] = &[
    "most_complete",
    "majority_vote",
    "first_non_null",
    "longest_value",
    "unanimous_or_null",
    // Falls back to count-majority when pair_scores is None, which is always
    // the case from Spark. Included because that fallback is the DOCUMENTED
    // behaviour, not because the edge-weighted form is implemented.
    "confidence_majority",
];

/// Whether [`merge_field`] can run `strategy`.
///
/// Exposed so a host refuses at PLAN time, naming the strategy, rather than
/// per row inside a distributed job.
pub fn supports(strategy: &str) -> bool {
    SUPPORTED.contains(&strategy)
}

/// Insertion-ordered tally: `(value, count)` in first-seen order.
///
/// Deliberately a `Vec`, not a `HashMap`. Python's `Counter.most_common(1)`
/// resolves a count tie to the first-inserted key, so iteration order IS the
/// tie-break rule; a hash map would make the winner depend on hash seeding.
fn tally<'a>(values: &[(usize, &'a str)]) -> Vec<(&'a str, usize, usize)> {
    let mut out: Vec<(&str, usize, usize)> = Vec::new();
    for (i, v) in values {
        match out.iter_mut().find(|(k, _, _)| k == v) {
            Some((_, c, _)) => *c += 1,
            None => out.push((v, 1, *i)),
        }
    }
    out
}

/// Choose the surviving value for one cluster's collected field values.
///
/// `values` is one entry per cluster member; `None` is a missing value.
/// Returns the survivor, or `None` when there is none (no non-null members, or
/// `unanimous_or_null` on a disagreement).
///
/// Mirrors `merge_field`'s two early-outs before any strategy runs: all-null
/// yields `None`, and a single distinct non-null value yields that value
/// regardless of strategy.
pub fn merge_field(values: &[Option<&str>], strategy: &str) -> Result<Option<String>, Refused> {
    if strategy.starts_with("custom:") {
        return Err(Refused(format!(
            "strategy {strategy:?} dispatches to a Python plugin, which cannot \
             run outside Python. Refused rather than approximated: a survivor \
             chosen by a different rule is a wrong golden record that looks \
             right."
        )));
    }
    if !supports(strategy) {
        return Err(Refused(match strategy {
            "source_priority" => "strategy \"source_priority\" needs a sources \
                list, which the Spark path does not supply -- Python raises \
                rather than guessing, and so does this. Guessing an order would \
                silently prefer the wrong system of record."
                .to_string(),
            "most_recent" => "strategy \"most_recent\" needs a dates list, which \
                the Spark path does not supply -- Python raises rather than \
                guessing. Without dates there is no \"recent\", and picking the \
                first row would be an arbitrary answer wearing a deterministic \
                hat."
                .to_string(),
            other => format!("unknown strategy {other:?}"),
        }));
    }

    let non_null: Vec<(usize, &str)> = values
        .iter()
        .enumerate()
        .filter_map(|(i, v)| v.map(|s| (i, s)))
        .collect();
    if non_null.is_empty() {
        return Ok(None);
    }
    // All non-null values identical -> that value, whatever the strategy.
    let first = non_null[0].1;
    if non_null.iter().all(|(_, v)| *v == first) {
        return Ok(Some(first.to_string()));
    }

    let winner = match strategy {
        // `str(v)` length in Python; every Spark value is already a string.
        // Length is in CHARACTERS, not bytes -- Python's len() counts code
        // points, so a byte length would prefer an accented value over a longer
        // ASCII one.
        "most_complete" | "longest_value" => {
            let max_len = non_null
                .iter()
                .map(|(_, v)| v.chars().count())
                .max()
                .unwrap_or(0);
            // FIRST maximum: Python takes longest[0] when the tie is unbroken.
            non_null
                .iter()
                .find(|(_, v)| v.chars().count() == max_len)
                .map(|(_, v)| *v)
        }
        "majority_vote" | "confidence_majority" => {
            let counts = tally(&non_null);
            // `max_by_key` returns the LAST maximum in Rust and the FIRST in
            // Python's `most_common`. Fold keeping strict `>` to match Python.
            counts
                .iter()
                .fold(None::<(&str, usize)>, |best, (v, c, _)| match best {
                    Some((_, bc)) if *c <= bc => best,
                    _ => Some((v, *c)),
                })
                .map(|(v, _)| v)
        }
        "first_non_null" => Some(non_null[0].1),
        // Disagreement is deliberately a NULL, not a guess: the strategy exists
        // for compliance fields where a heuristic value is worse than none.
        // Unanimity was already handled by the early-out above, so reaching
        // here means members disagree.
        "unanimous_or_null" => None,
        _ => unreachable!("supports() gates this match"),
    };
    Ok(winner.map(str::to_string))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn m(vals: &[Option<&str>], s: &str) -> Option<String> {
        merge_field(vals, s).unwrap()
    }

    #[test]
    fn all_null_yields_none_for_every_strategy() {
        for s in SUPPORTED {
            assert_eq!(m(&[None, None], s), None, "{s}");
        }
    }

    #[test]
    fn a_single_distinct_value_wins_regardless_of_strategy() {
        // merge_field's early-out, before any strategy runs.
        for s in SUPPORTED {
            assert_eq!(m(&[Some("a"), None, Some("a")], s), Some("a".into()), "{s}");
        }
    }

    #[test]
    fn most_complete_takes_the_longest_then_first() {
        assert_eq!(
            m(&[Some("ab"), Some("abcd")], "most_complete"),
            Some("abcd".into())
        );
        // Tie -> FIRST, matching Python's longest[0].
        assert_eq!(
            m(&[Some("ab"), Some("cd")], "most_complete"),
            Some("ab".into())
        );
    }

    #[test]
    fn length_is_counted_in_characters_not_bytes() {
        // "café" is 4 chars / 5 bytes; "abcde" is 5 chars / 5 bytes. A byte
        // comparison would call these tied and take the first.
        assert_eq!(
            m(&[Some("café"), Some("abcde")], "longest_value"),
            Some("abcde".into())
        );
    }

    #[test]
    fn majority_vote_breaks_count_ties_by_first_seen() {
        assert_eq!(
            m(&[Some("b"), Some("a"), Some("a")], "majority_vote"),
            Some("a".into())
        );
        // 1-1 tie: Python's Counter.most_common keeps insertion order, so "b".
        assert_eq!(
            m(&[Some("b"), Some("a")], "majority_vote"),
            Some("b".into())
        );
    }

    #[test]
    fn confidence_majority_falls_back_to_count_majority() {
        // Without pair_scores -- which Spark never supplies -- Python documents
        // this as vanilla count-majority.
        assert_eq!(
            m(&[Some("b"), Some("a"), Some("a")], "confidence_majority"),
            Some("a".into())
        );
    }

    #[test]
    fn first_non_null_skips_leading_nulls() {
        assert_eq!(
            m(&[None, Some("x"), Some("y")], "first_non_null"),
            Some("x".into())
        );
    }

    #[test]
    fn unanimous_or_null_emits_null_on_disagreement() {
        assert_eq!(
            m(&[Some("a"), Some("a")], "unanimous_or_null"),
            Some("a".into())
        );
        // A heuristic value would be worse than none for this strategy's whole
        // use case (medical IDs, licence numbers).
        assert_eq!(m(&[Some("a"), Some("b")], "unanimous_or_null"), None);
        // Nulls are ignored: absence is not contradiction.
        assert_eq!(
            m(&[Some("a"), None, Some("a")], "unanimous_or_null"),
            Some("a".into())
        );
    }

    #[test]
    fn strategies_needing_arguments_spark_never_passes_are_refused() {
        for s in ["source_priority", "most_recent", "custom:mine", "nope"] {
            assert!(!supports(s) || s.starts_with("custom:"), "{s}");
            let err = merge_field(&[Some("a"), Some("b")], s).unwrap_err();
            assert!(!err.0.is_empty(), "{s} refused without a reason");
        }
    }

    #[test]
    fn a_refusal_beats_a_plausible_wrong_survivor() {
        // The property the refusals exist for: these must NOT quietly return a
        // value. A wrong golden record raises nothing and looks right.
        assert!(merge_field(&[Some("a"), Some("b")], "most_recent").is_err());
        assert!(merge_field(&[Some("a"), Some("b")], "source_priority").is_err());
    }
}
