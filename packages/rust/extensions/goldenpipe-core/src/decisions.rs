//! evaluate_builtin(name, ctx) -> Option<Decision>. Mirrors decisions.py EXACTLY
//! (Python is canonical). Not engine-invoked today; here for one-source-of-truth so
//! the predicate logic can't drift Python<->TS later.
use serde_json::Value;

use crate::model::{CtxSubset, Decision};

fn findings(ctx: &CtxSubset) -> Option<&Vec<Value>> {
    match ctx.artifacts.get("findings") {
        Some(Value::Array(a)) if !a.is_empty() => Some(a),
        _ => None, // absent or empty -> None (matches `if not findings`)
    }
}

pub fn evaluate_builtin(name: &str, ctx: &CtxSubset) -> Option<Decision> {
    match name {
        "severity_gate" => {
            let f = findings(ctx)?;
            let critical = f
                .iter()
                .any(|x| x.get("severity").and_then(Value::as_str) == Some("critical"));
            critical.then(|| Decision {
                abort: true,
                reason: "Critical findings detected".into(),
                ..Default::default()
            })
        }
        "pii_router" => {
            let f = findings(ctx)?;
            let pii = f
                .iter()
                .any(|x| x.get("check").and_then(Value::as_str) == Some("pii_detection"));
            pii.then(|| Decision {
                skip: vec!["goldenmatch.dedupe".into()],
                insert: vec!["goldenmatch.dedupe_pprl".into()],
                reason: "PII detected, routing to PPRL matching".into(),
                ..Default::default()
            })
        }
        "row_count_gate" => {
            // Python: ctx.metadata.get("input_rows", 0) -> default 0. input_rows is always an
            // int in practice (int(len(df))); as_i64 matches that. A non-int/absent value -> 0
            // (fires "Only 0 row(s)"), reproducing Python's default-0.
            let n = ctx
                .metadata
                .get("input_rows")
                .and_then(Value::as_i64)
                .unwrap_or(0);
            (n < 2).then(|| Decision {
                skip: vec!["goldenmatch.dedupe".into()],
                reason: format!("Only {} row(s), skipping deduplication", n),
                ..Default::default()
            })
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::evaluate_builtin;
    use crate::model::CtxSubset;

    fn ctx(json: &str) -> CtxSubset {
        serde_json::from_str(json).unwrap()
    }

    #[test]
    fn severity_gate_critical_aborts() {
        let c = ctx(r#"{"artifacts":{"findings":[{"severity":"critical"}]}}"#);
        let d = evaluate_builtin("severity_gate", &c).unwrap();
        assert!(d.abort);
        assert_eq!(d.reason, "Critical findings detected");
    }

    #[test]
    fn severity_gate_none_and_empty() {
        assert!(evaluate_builtin(
            "severity_gate",
            &ctx(r#"{"artifacts":{"findings":[{"severity":"info"}]}}"#)
        )
        .is_none());
        assert!(evaluate_builtin("severity_gate", &ctx(r#"{"artifacts":{}}"#)).is_none());
    }

    #[test]
    fn pii_router_hits() {
        let d = evaluate_builtin(
            "pii_router",
            &ctx(r#"{"artifacts":{"findings":[{"check":"pii_detection"}]}}"#),
        )
        .unwrap();
        assert_eq!(d.skip, vec!["goldenmatch.dedupe"]);
        assert_eq!(d.insert, vec!["goldenmatch.dedupe_pprl"]);
        assert_eq!(d.reason, "PII detected, routing to PPRL matching");
    }

    #[test]
    fn row_count_gate_reason_bytes_match_python() {
        let d =
            evaluate_builtin("row_count_gate", &ctx(r#"{"metadata":{"input_rows":1}}"#)).unwrap();
        assert_eq!(d.reason, "Only 1 row(s), skipping deduplication"); // byte-match f-string
        assert_eq!(d.skip, vec!["goldenmatch.dedupe"]);
        // >= 2 -> None; missing -> default 0 -> fires
        assert!(
            evaluate_builtin("row_count_gate", &ctx(r#"{"metadata":{"input_rows":2}}"#)).is_none()
        );
        let missing = evaluate_builtin("row_count_gate", &ctx(r#"{"metadata":{}}"#)).unwrap();
        assert_eq!(missing.reason, "Only 0 row(s), skipping deduplication"); // default 0 -> fires
    }

    #[test]
    fn unknown_name_none() {
        assert!(evaluate_builtin("nope", &CtxSubset::default()).is_none());
    }
}
