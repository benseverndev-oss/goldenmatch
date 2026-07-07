use crate::schema::TargetSchema;
use serde_json::Value;

pub struct NormalizedRow {
    pub values: std::collections::BTreeMap<String, Option<String>>,
    pub confidence: std::collections::BTreeMap<String, f64>,
}

fn py_str(v: &Value) -> Option<String> {
    match v {
        Value::Null => None,
        Value::String(s) => Some(s.clone()),
        Value::Bool(b) => Some(if *b { "True".into() } else { "False".into() }),
        Value::Number(n) => Some(n.to_string()), // ints match; floats: see risk note
        other => Some(other.to_string()),
    }
}

pub fn normalize_record(values_json: &str, confidence_json: &str, schema: &TargetSchema)
    -> Result<NormalizedRow, String> {
    let vals: Value = serde_json::from_str(values_json).map_err(|e| e.to_string())?;
    let conf: Value = serde_json::from_str(confidence_json).map_err(|e| e.to_string())?;
    let mut values = std::collections::BTreeMap::new();
    let mut confidence = std::collections::BTreeMap::new();
    for col in schema.column_names() {
        let v = vals.get(&col).and_then(|x| py_str(x));  // missing or null -> None
        values.insert(col.clone(), v);
        let c = conf.get(&col).and_then(|x| x.as_f64()).unwrap_or(0.0);
        confidence.insert(col, c);
    }
    Ok(NormalizedRow { values, confidence })
}

pub fn row_confidence(row: &NormalizedRow) -> f64 {
    if row.confidence.is_empty() {
        0.0
    } else {
        row.confidence.values().cloned().fold(f64::INFINITY, f64::min)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{Field, TargetSchema};
    fn schema() -> TargetSchema { TargetSchema{ fields: vec![
        Field{name:"a".into(),kind:"text".into(),hint:None},
        Field{name:"n".into(),kind:"number".into(),hint:None},
        Field{name:"b".into(),kind:"text".into(),hint:None},
        Field{name:"missing".into(),kind:"text".into(),hint:None}]}}
    #[test]
    fn coerces_like_python_str() {
        let out = normalize_record(r#"{"a":"Ada","n":90210,"b":true,"junk":"x"}"#, r#"{"a":0.9}"#, &schema()).unwrap();
        // values: str-coerced, missing->null, unknown 'junk' dropped
        assert_eq!(out.values["a"], Some("Ada".into()));
        assert_eq!(out.values["n"], Some("90210".into()));
        assert_eq!(out.values["b"], Some("True".into()));      // NOT "true"
        assert_eq!(out.values["missing"], None);
        assert!(!out.values.contains_key("junk"));
        // confidence: default 0.0
        assert_eq!(out.confidence["a"], 0.9);
        assert_eq!(out.confidence["n"], 0.0);
        assert_eq!(row_confidence(&out), 0.0);
    }
}
