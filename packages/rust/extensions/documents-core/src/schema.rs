use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Field {
    pub name: String,
    #[serde(default = "default_kind")]
    pub kind: String,
    #[serde(default)]
    pub hint: Option<String>,
}
fn default_kind() -> String { "text".to_string() }

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TargetSchema { pub fields: Vec<Field> }

impl TargetSchema {
    pub fn column_names(&self) -> Vec<String> {
        self.fields.iter().map(|f| f.name.clone()).collect()
    }
}

/// Mirror of schema_io.schema_from_dict: object with a non-empty `fields` list;
/// every item an object with `name`; `kind` defaults "text"; `hint` optional.
pub fn schema_from_json(s: &str) -> Result<TargetSchema, String> {
    let v: serde_json::Value = serde_json::from_str(s).map_err(|e| e.to_string())?;
    let arr = v.get("fields").and_then(|f| f.as_array())
        .ok_or("schema must be an object with a 'fields' list")?;
    let mut fields = Vec::new();
    for item in arr {
        let obj = item.as_object().ok_or_else(|| format!("schema field must be an object, got {item}"))?;
        let name = obj.get("name").and_then(|n| n.as_str())
            .ok_or_else(|| format!("schema field missing 'name': {item}"))?;
        let kind = obj.get("kind").and_then(|k| k.as_str()).unwrap_or("text").to_string();
        let hint = obj.get("hint").and_then(|h| h.as_str()).map(|s| s.to_string());
        fields.push(Field { name: name.to_string(), kind, hint });
    }
    if fields.is_empty() { return Err("schema has no fields".into()); }
    Ok(TargetSchema { fields })
}

/// Canonical JSON (always name/kind/hint), byte-matching schema_io.schema_to_dict + json.dumps.
/// Serialize the STRUCT (serde emits struct fields in DECLARATION order: name, kind, hint) --
/// do NOT use `serde_json::json!({...})`, whose backing map is a BTreeMap and would emit keys
/// ALPHABETICALLY (hint, kind, name), failing the Step-1 assertion and diverging from Python's
/// `{"name","kind","hint"}` dict order.
pub fn schema_to_json(schema: &TargetSchema) -> String {
    serde_json::to_string(schema).expect("schema serializes")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn round_trip_and_defaults() {
        let s = schema_from_json(r#"{"fields":[{"name":"full_name"},{"name":"email","kind":"email","hint":"work"}]}"#).unwrap();
        assert_eq!(s.fields[0].kind, "text");      // default
        assert_eq!(s.fields[0].hint, None);
        assert_eq!(s.column_names(), vec!["full_name","email"]);
        // canonical JSON always emits name/kind/hint
        assert_eq!(schema_to_json(&s), r#"{"fields":[{"name":"full_name","kind":"text","hint":null},{"name":"email","kind":"email","hint":"work"}]}"#);
    }
    #[test]
    fn rejects_bad_shapes() {
        assert!(schema_from_json(r#"{"nope":1}"#).is_err());          // no fields list
        assert!(schema_from_json(r#"{"fields":[]}"#).is_err());        // empty
        assert!(schema_from_json(r#"{"fields":["full_name"]}"#).is_err()); // non-object item
        assert!(schema_from_json(r#"{"fields":[{"kind":"text"}]}"#).is_err()); // missing name
    }
}
