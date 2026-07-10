//! Compiler IR `lower` — the reference twin of goldenpipe/compiler/ir.py. Builds
//! IR nodes as serde_json Value objects by hand so the emitted JSON (key set +
//! ORDER, via preserve_order) is byte-identical to the Python dicts.
use serde_json::{json, Map, Value};

fn base(kind: &str, id: u64, origin: &str, resolved: bool) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("kind".into(), Value::from(kind));
    m.insert("id".into(), Value::from(id));
    m.insert("origin_stage".into(), Value::from(origin));
    m.insert("resolved".into(), Value::from(resolved));
    m
}

pub fn lower(
    origin_stage: &str,
    kind_hint: &str,
    concrete: &Value,
    next_id: u64,
    resolved: bool,
) -> (Vec<Value>, u64) {
    let mut nid = next_id;
    let mut nodes: Vec<Value> = Vec::new();

    match kind_hint {
        "source" => {
            let mut m = base("Source", nid, origin_stage, resolved);
            m.insert("produces".into(), json!(["df"]));
            nodes.push(Value::Object(m));
            nid += 1;
        }
        "scan" => {
            for col in concrete
                .get("columns")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let mut m = base("Scan", nid, origin_stage, resolved);
                m.insert(
                    "column".into(),
                    col.get("column").cloned().unwrap_or(Value::Null),
                );
                m.insert(
                    "ops".into(),
                    col.get("ops").cloned().unwrap_or_else(|| json!([])),
                );
                nodes.push(Value::Object(m));
                nid += 1;
            }
        }
        "map" => {
            for spec in concrete
                .get("transforms")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let col = spec.get("column").cloned().unwrap_or(Value::Null);
                for op in spec
                    .get("ops")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                {
                    let mut m = base("Map", nid, origin_stage, resolved);
                    m.insert("column".into(), col.clone());
                    m.insert("op".into(), op.clone());
                    nodes.push(Value::Object(m));
                    nid += 1;
                }
            }
        }
        "match" => {
            let mut p = base("Partition", nid, origin_stage, resolved);
            p.insert(
                "keys".into(),
                concrete.get("keys").cloned().unwrap_or_else(|| json!([])),
            );
            nodes.push(Value::Object(p));
            nid += 1;
            let mut s = base("PairScore", nid, origin_stage, resolved);
            s.insert(
                "scorer".into(),
                concrete.get("scorer").cloned().unwrap_or(Value::Null),
            );
            nodes.push(Value::Object(s));
            nid += 1;
            let mut c = base("Connected", nid, origin_stage, resolved);
            c.insert(
                "method".into(),
                concrete.get("method").cloned().unwrap_or(Value::Null),
            );
            nodes.push(Value::Object(c));
            nid += 1;
        }
        _ => {
            let mut m = base("Barrier", nid, origin_stage, resolved);
            m.insert("raw_config".into(), concrete.clone());
            nodes.push(Value::Object(m));
            nid += 1;
        }
    }
    (nodes, nid)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_emits_one_source_node() {
        let (nodes, next_id) = lower("load", "source", &json!({}), 0, false);
        assert_eq!(next_id, 1);
        assert_eq!(
            serde_json::to_value(&nodes).unwrap(),
            json!([{"kind": "Source", "id": 0, "origin_stage": "load", "resolved": false, "produces": ["df"]}])
        );
    }

    #[test]
    fn map_emits_one_node_per_op_with_sequential_ids() {
        let concrete = json!({"transforms": [{"column": "email", "ops": ["a", "b"]}]});
        let (nodes, next_id) = lower("goldenflow.transform", "map", &concrete, 5, true);
        assert_eq!(next_id, 7);
        assert_eq!(
            serde_json::to_value(&nodes).unwrap(),
            json!([
                {"kind": "Map", "id": 5, "origin_stage": "goldenflow.transform", "resolved": true, "column": "email", "op": "a"},
                {"kind": "Map", "id": 6, "origin_stage": "goldenflow.transform", "resolved": true, "column": "email", "op": "b"}
            ])
        );
    }
}
