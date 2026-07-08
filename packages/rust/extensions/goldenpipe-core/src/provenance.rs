//! Field-level provenance over the SP1 IR — the reference twin of
//! goldenpipe/compiler/provenance.py. Builds the `{fields, unmapped}` result as
//! serde_json Value objects by hand so the emitted JSON (key set + ORDER, via
//! preserve_order) is byte-identical to the Python dicts. Field key order is
//! exactly column, origin, checks, transforms, blocking_key, scorer_input,
//! node_ids; unmapped key order is node_id, kind, note.
use serde_json::{json, Map, Value};
use std::collections::{BTreeSet, HashMap};

pub fn provenance(compiled: &Value) -> Value {
    let mut order: Vec<String> = Vec::new();
    let mut idx: HashMap<String, usize> = HashMap::new();
    let mut checks: Vec<Vec<Value>> = Vec::new();
    let mut transforms: Vec<Vec<Value>> = Vec::new();
    let mut node_ids: Vec<Vec<Value>> = Vec::new();
    let mut unmapped: Vec<Value> = Vec::new();
    // BTreeSet keeps the union sorted, matching Python `sorted(blocking | scorer)`.
    let mut blocking: BTreeSet<String> = BTreeSet::new();
    let mut scorer: BTreeSet<String> = BTreeSet::new();

    // Inline get-or-create returning the field index for `col` (a closure would
    // need &mut captures of every accumulator — the borrow checker rejects that).
    macro_rules! ensure {
        ($col:expr) => {{
            let c = $col;
            match idx.get(c) {
                Some(&i) => i,
                None => {
                    let i = order.len();
                    order.push(c.to_string());
                    idx.insert(c.to_string(), i);
                    checks.push(Vec::new());
                    transforms.push(Vec::new());
                    node_ids.push(Vec::new());
                    i
                }
            }
        }};
    }

    for n in compiled
        .get("nodes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let kind = n.get("kind").and_then(Value::as_str).unwrap_or("");
        match kind {
            "Scan" => {
                let col = n.get("column").and_then(Value::as_str).unwrap_or("");
                let i = ensure!(col);
                for op in n.get("ops").and_then(Value::as_array).into_iter().flatten() {
                    checks[i].push(op.clone());
                }
                if let Some(id) = n.get("id") {
                    node_ids[i].push(id.clone());
                }
            }
            "Map" => {
                let col = n.get("column").and_then(Value::as_str).unwrap_or("");
                let i = ensure!(col);
                if let Some(op) = n.get("op") {
                    transforms[i].push(op.clone());
                }
                if let Some(id) = n.get("id") {
                    node_ids[i].push(id.clone());
                }
            }
            "Partition" => {
                for k in n
                    .get("keys")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                {
                    if let Some(s) = k.as_str() {
                        blocking.insert(s.to_string());
                    }
                }
            }
            "PairScore" => {
                for c in n
                    .get("scorer")
                    .and_then(|s| s.get("columns"))
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                {
                    if let Some(s) = c.as_str() {
                        scorer.insert(s.to_string());
                    }
                }
            }
            // Source / Connected / Barrier (and any unknown kind -> the kind itself).
            other => {
                let note = match other {
                    "Source" => "data loaded",
                    "Connected" => "clustering",
                    "Barrier" => "opaque stage",
                    _ => other,
                };
                let mut m = Map::new();
                m.insert(
                    "node_id".into(),
                    n.get("id").cloned().unwrap_or(Value::Null),
                );
                m.insert("kind".into(), Value::from(other));
                m.insert("note".into(), Value::from(note));
                unmapped.push(Value::Object(m));
            }
        }
    }

    // Role-only columns (no Scan/Map) in sorted union order (BTreeSet union is sorted).
    for col in blocking.union(&scorer) {
        let _ = ensure!(col.as_str());
    }

    let mut fields: Vec<Value> = Vec::new();
    for col in &order {
        let i = idx[col];
        let mut m = Map::new();
        m.insert("column".into(), Value::from(col.clone()));
        m.insert("origin".into(), Value::from("source"));
        m.insert(
            "checks".into(),
            Value::Array(std::mem::take(&mut checks[i])),
        );
        m.insert(
            "transforms".into(),
            Value::Array(std::mem::take(&mut transforms[i])),
        );
        m.insert("blocking_key".into(), Value::from(blocking.contains(col)));
        m.insert("scorer_input".into(), Value::from(scorer.contains(col)));
        m.insert(
            "node_ids".into(),
            Value::Array(std::mem::take(&mut node_ids[i])),
        );
        fields.push(Value::Object(m));
    }

    json!({ "fields": fields, "unmapped": unmapped })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scan_and_map_build_one_field() {
        let compiled = json!({"nodes": [
            {"kind": "Scan", "id": 0, "column": "email", "ops": ["pattern_consistency"]},
            {"kind": "Map", "id": 1, "column": "email", "op": "email_normalize"}
        ]});
        assert_eq!(
            provenance(&compiled),
            json!({
                "fields": [{
                    "column": "email", "origin": "source",
                    "checks": ["pattern_consistency"], "transforms": ["email_normalize"],
                    "blocking_key": false, "scorer_input": false, "node_ids": [0, 1]
                }],
                "unmapped": []
            })
        );
    }

    #[test]
    fn partition_and_pairscore_mark_roles_sorted() {
        let compiled = json!({"nodes": [
            {"kind": "Partition", "id": 0, "keys": ["last_name"]},
            {"kind": "PairScore", "id": 1, "scorer": {"columns": ["email", "last_name"]}}
        ]});
        assert_eq!(
            provenance(&compiled),
            json!({
                "fields": [
                    {"column": "email", "origin": "source", "checks": [], "transforms": [],
                     "blocking_key": false, "scorer_input": true, "node_ids": []},
                    {"column": "last_name", "origin": "source", "checks": [], "transforms": [],
                     "blocking_key": true, "scorer_input": true, "node_ids": []}
                ],
                "unmapped": []
            })
        );
    }
}
