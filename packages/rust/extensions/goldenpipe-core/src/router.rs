//! apply_decision(decision, remaining) -> ApplyResult. Mirrors router.py:13-41.
//! Pure: returns the new remaining list + the exact `ctx.reasoning["_router"]` string
//! the host must record; it does NOT mutate ctx and does NOT fetch stage objects
//! (the host maps an inserted name -> its stage).
use crate::model::{ApplyResult, Decision, PlannedSpec};

pub fn apply_decision(decision: &Decision, remaining: &[PlannedSpec]) -> ApplyResult {
    if decision.abort {
        return ApplyResult {
            remaining: vec![],
            router_note: Some(format!("ABORT: {}", decision.reason)),
        };
    }
    let note = if decision.reason.is_empty() {
        None
    } else {
        Some(decision.reason.clone())
    };

    let mut kept: Vec<PlannedSpec> = remaining
        .iter()
        .filter(|s| !decision.skip.contains(&s.name))
        .cloned()
        .collect();

    if !decision.insert.is_empty() {
        let mut inserted: Vec<PlannedSpec> = decision
            .insert
            .iter()
            .map(|name| PlannedSpec {
                name: name.clone(),
                use_: name.clone(),
                config: Default::default(),
                skip_if: None,
                on_error: Default::default(),
            })
            .collect();
        inserted.append(&mut kept);
        kept = inserted;
    }
    ApplyResult {
        remaining: kept,
        router_note: note,
    }
}

#[cfg(test)]
mod tests {
    use super::apply_decision;
    use crate::model::*;

    fn planned(name: &str) -> PlannedSpec {
        PlannedSpec {
            name: name.into(),
            use_: name.into(),
            config: JsonMap::new(),
            skip_if: None,
            on_error: OnError::Continue,
        }
    }
    fn dec(skip: &[&str], abort: bool, insert: &[&str], reason: &str) -> Decision {
        Decision {
            skip: skip.iter().map(|s| s.to_string()).collect(),
            abort,
            insert: insert.iter().map(|s| s.to_string()).collect(),
            reason: reason.into(),
        }
    }

    #[test]
    fn abort_empties_and_prefixes_note() {
        let r = apply_decision(&dec(&[], true, &[], "critical"), &[planned("a")]);
        assert!(r.remaining.is_empty());
        assert_eq!(r.router_note.as_deref(), Some("ABORT: critical"));
    }

    #[test]
    fn skip_filters_by_name() {
        let r = apply_decision(
            &dec(&["b"], false, &[], "x"),
            &[planned("a"), planned("b"), planned("c")],
        );
        let names: Vec<_> = r.remaining.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["a", "c"]);
        assert_eq!(r.router_note.as_deref(), Some("x"));
    }

    #[test]
    fn insert_prepends_in_order() {
        let r = apply_decision(&dec(&[], false, &["x", "y"], ""), &[planned("a")]);
        let names: Vec<_> = r.remaining.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["x", "y", "a"]);
        assert_eq!(r.router_note, None); // empty reason -> None
    }

    #[test]
    fn skip_then_insert_combined() {
        let r = apply_decision(
            &dec(&["a"], false, &["z"], "r"),
            &[planned("a"), planned("b")],
        );
        let names: Vec<_> = r.remaining.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["z", "b"]);
    }

    #[test]
    fn empty_decision_is_noop() {
        let r = apply_decision(&Decision::default(), &[planned("a"), planned("b")]);
        assert_eq!(r.remaining.len(), 2);
        assert_eq!(r.router_note, None);
    }
}
