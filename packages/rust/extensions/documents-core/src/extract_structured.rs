//! Structured-extract parse kernel -- turns a VLM structured response
//! `{"header": {...}, "line_items": [{...}, ...]}` into a normalized header row +
//! per-line-item rows, reusing `normalize::normalize_record` for BOTH the header
//! (against `header_fields`) and each line item (against `line_item_fields`).
//!
//! Takes the FULL `DocTemplate` (parsed from JSON in the shim), NOT a doctype
//! lookup -- keeps native/pure parity for ANY template, incl. custom ones.
use crate::normalize::{normalize_record, NormalizedRow};
use crate::schema::TargetSchema;
use crate::templates::DocTemplate;
use serde_json::{json, Map, Value};

pub struct StructuredParsed {
    pub header: NormalizedRow,
    pub line_items: Vec<NormalizedRow>,
}

/// Split a response record into (values, confidence) JSON values, mirroring the
/// flat extractor's record shape. If the object carries a `values` object, it's
/// the wrapped `{"values":..,"confidence":..}` shape (confidence defaults to `{}`
/// when absent or non-object); otherwise the object itself is the bare
/// `{field: value}` values map with empty confidence. A non-object collapses to
/// two empty maps (every field -> null / 0.0).
fn record_parts(obj: &Value) -> (Value, Value) {
    let empty = || Value::Object(Map::new());
    if let Some(vals) = obj.get("values") {
        if vals.is_object() {
            let conf = obj
                .get("confidence")
                .filter(|c| c.is_object())
                .cloned()
                .unwrap_or_else(empty);
            return (vals.clone(), conf);
        }
    }
    if obj.is_object() {
        return (obj.clone(), empty());
    }
    (empty(), empty())
}

fn normalize_value(
    values: &Value,
    confidence: &Value,
    schema: &TargetSchema,
) -> Result<NormalizedRow, String> {
    let v = serde_json::to_string(values).map_err(|e| e.to_string())?;
    let c = serde_json::to_string(confidence).map_err(|e| e.to_string())?;
    normalize_record(&v, &c, schema)
}

pub fn parse_structured(text: &str, template: &DocTemplate) -> Result<StructuredParsed, String> {
    let root: Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    let header_obj = root
        .get("header")
        .ok_or("structured response missing 'header'")?;
    let header_schema = TargetSchema {
        fields: template.header_fields.clone(),
    };
    let (hv, hc) = record_parts(header_obj);
    let header = normalize_value(&hv, &hc, &header_schema)?;

    let item_schema = TargetSchema {
        fields: template.line_item_fields.clone(),
    };
    let mut line_items = Vec::new();
    // Flat doctypes (empty line_item_fields, e.g. receipt) force [] regardless of
    // what the response carries.
    if !item_schema.fields.is_empty() {
        if let Some(arr) = root.get("line_items").and_then(|x| x.as_array()) {
            for item in arr {
                let (iv, ic) = record_parts(item);
                line_items.push(normalize_value(&iv, &ic, &item_schema)?);
            }
        }
    }
    Ok(StructuredParsed { header, line_items })
}

fn row_to_json(row: NormalizedRow) -> Value {
    let values: Map<_, _> = row.values.into_iter().map(|(k, v)| (k, json!(v))).collect();
    let confidence: Map<_, _> = row
        .confidence
        .into_iter()
        .map(|(k, v)| (k, json!(v)))
        .collect();
    json!({"values": values, "confidence": confidence})
}

pub fn parse_structured_json(text: &str, template: &DocTemplate) -> Result<String, String> {
    let parsed = parse_structured(text, template)?;
    let header = row_to_json(parsed.header);
    let items: Vec<Value> = parsed.line_items.into_iter().map(row_to_json).collect();
    Ok(json!({"header": header, "line_items": items}).to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::templates::template;

    fn invoice() -> DocTemplate {
        template("invoice").unwrap()
    }

    #[test]
    fn header_plus_two_line_items() {
        let t = invoice();
        let text = r#"{
            "header": {"values": {"invoice_number": "INV-1", "total_amount": 42,
                                  "currency": "USD"},
                       "confidence": {"invoice_number": 0.9}},
            "line_items": [
                {"values": {"description": "Widget", "quantity": 2, "unit_price": 10,
                            "line_total": 20}, "confidence": {"description": 0.8}},
                {"values": {"description": "Gadget", "quantity": 1}}
            ]
        }"#;
        let p = parse_structured(text, &t).unwrap();
        // header: str-coerced, missing -> null
        assert_eq!(p.header.values["invoice_number"], Some("INV-1".into()));
        assert_eq!(p.header.values["total_amount"], Some("42".into()));
        assert_eq!(p.header.values["vendor_name"], None);
        assert_eq!(p.header.confidence["invoice_number"], 0.9);
        assert_eq!(p.header.confidence["currency"], 0.0);
        // two items
        assert_eq!(p.line_items.len(), 2);
        assert_eq!(p.line_items[0].values["description"], Some("Widget".into()));
        assert_eq!(p.line_items[0].values["quantity"], Some("2".into()));
        assert_eq!(p.line_items[0].confidence["description"], 0.8);
        // second item: missing fields -> null, missing confidence -> 0.0
        assert_eq!(p.line_items[1].values["description"], Some("Gadget".into()));
        assert_eq!(p.line_items[1].values["unit_price"], None);
        assert_eq!(p.line_items[1].confidence["quantity"], 0.0);
    }

    #[test]
    fn missing_header_field_is_null() {
        let t = invoice();
        let text = r#"{"header": {"values": {"invoice_number": "INV-9"}}}"#;
        let p = parse_structured(text, &t).unwrap();
        assert_eq!(p.header.values["invoice_number"], Some("INV-9".into()));
        assert_eq!(p.header.values["buyer_name"], None);
        assert!(p.line_items.is_empty());
    }

    #[test]
    fn extra_key_dropped() {
        let t = invoice();
        let text = r#"{"header": {"invoice_number": "INV-3", "junk": "x"}}"#;
        let p = parse_structured(text, &t).unwrap();
        assert_eq!(p.header.values["invoice_number"], Some("INV-3".into()));
        assert!(!p.header.values.contains_key("junk"));
    }

    #[test]
    fn bare_header_shape_supported() {
        // header is a bare {field: value} map (no values/confidence wrapper)
        let t = invoice();
        let text = r#"{"header": {"invoice_number": "INV-B", "currency": "EUR"}}"#;
        let p = parse_structured(text, &t).unwrap();
        assert_eq!(p.header.values["invoice_number"], Some("INV-B".into()));
        assert_eq!(p.header.values["currency"], Some("EUR".into()));
        assert_eq!(p.header.confidence["invoice_number"], 0.0);
    }

    #[test]
    fn empty_line_items_yields_empty() {
        let t = invoice();
        let text = r#"{"header": {"values": {"invoice_number": "INV-4"}}, "line_items": []}"#;
        let p = parse_structured(text, &t).unwrap();
        assert!(p.line_items.is_empty());
    }

    #[test]
    fn receipt_ignores_stray_line_items() {
        let t = template("receipt").unwrap();
        let text = r#"{"header": {"values": {"merchant_name": "Shop"}},
                       "line_items": [{"values": {"whatever": 1}}]}"#;
        let p = parse_structured(text, &t).unwrap();
        assert_eq!(p.header.values["merchant_name"], Some("Shop".into()));
        assert!(p.line_items.is_empty()); // forced [] -- receipt has no line_item_fields
    }

    #[test]
    fn confidence_non_numeric_coerces_to_zero() {
        // Rust reference: `as_f64().unwrap_or(0.0)` -- null/string/bool -> 0.0
        // (coerce, NOT error; distinct from the classify kernel which errors).
        let t = invoice();
        let text = r#"{"header": {"values": {"invoice_number": "X"},
            "confidence": {"invoice_number": null, "total_amount": "0.9", "currency": true}}}"#;
        let p = parse_structured(text, &t).unwrap();
        assert_eq!(p.header.confidence["invoice_number"], 0.0);
        assert_eq!(p.header.confidence["total_amount"], 0.0);
        assert_eq!(p.header.confidence["currency"], 0.0);
    }

    #[test]
    fn container_values_render_compact_json() {
        // Rust reference: non-scalar value -> serde compact JSON, NOT a repr.
        let t = invoice();
        let text = r#"{"header": {"values": {"invoice_number": [1,2,3], "vendor_name": {"a":1}}}}"#;
        let p = parse_structured(text, &t).unwrap();
        assert_eq!(p.header.values["invoice_number"], Some("[1,2,3]".into()));
        assert_eq!(p.header.values["vendor_name"], Some("{\"a\":1}".into()));
    }

    #[test]
    fn header_null_is_all_null_row() {
        // "header": null -> key present, so NOT missing; record_parts collapses a
        // non-object to empty maps -> all fields null / 0.0 (no Err).
        let t = invoice();
        let p = parse_structured(r#"{"header": null}"#, &t).unwrap();
        assert_eq!(p.header.values["invoice_number"], None);
        assert_eq!(p.header.confidence["invoice_number"], 0.0);
    }

    #[test]
    fn header_values_non_object_falls_through_to_bare() {
        // {"values": 5} -- `values` present but not an object -> treated as bare map.
        let t = invoice();
        let p = parse_structured(r#"{"header": {"values": 5}}"#, &t).unwrap();
        assert_eq!(p.header.values["invoice_number"], None);
    }

    #[test]
    fn bare_scalar_line_item_is_all_null() {
        let t = invoice();
        let p = parse_structured(r#"{"header": {"values": {}}, "line_items": [5]}"#, &t).unwrap();
        assert_eq!(p.line_items.len(), 1);
        assert_eq!(p.line_items[0].values["description"], None);
    }

    #[test]
    fn missing_header_errs() {
        let t = invoice();
        assert!(parse_structured(r#"{"line_items": []}"#, &t).is_err());
    }

    #[test]
    fn invalid_json_errs() {
        let t = invoice();
        assert!(parse_structured("not json", &t).is_err());
    }

    #[test]
    fn json_shape_is_values_confidence() {
        let t = invoice();
        let text = r#"{"header": {"values": {"invoice_number": "INV-1"}},
                       "line_items": [{"values": {"description": "W"}}]}"#;
        let j = parse_structured_json(text, &t).unwrap();
        let v: Value = serde_json::from_str(&j).unwrap();
        assert_eq!(v["header"]["values"]["invoice_number"], "INV-1");
        assert_eq!(v["header"]["values"]["vendor_name"], Value::Null);
        assert_eq!(v["line_items"][0]["values"]["description"], "W");
        assert_eq!(v["line_items"][0]["confidence"]["description"], 0.0);
    }
}
