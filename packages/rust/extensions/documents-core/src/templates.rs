//! Doctype template registry -- the single source of truth for the four
//! structured-doc templates (invoice, po, statement, receipt). Static data;
//! no I/O. The `DocTemplate` struct serializes in DECLARATION order
//! (doctype, header_fields, line_item_fields) -- see `schema.rs` for the
//! "serialize the STRUCT not json!/BTreeMap" discipline.
use crate::schema::Field;
use serde::Serialize;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct DocTemplate {
    pub doctype: String,
    pub header_fields: Vec<Field>,
    pub line_item_fields: Vec<Field>,
}

fn f(name: &str, kind: &str) -> Field {
    Field { name: name.into(), kind: kind.into(), hint: None }
}

pub fn template(doctype: &str) -> Result<DocTemplate, String> {
    let t = match doctype {
        "invoice" => DocTemplate { doctype: "invoice".into(),
            header_fields: vec![f("invoice_number","text"), f("invoice_date","date"),
                f("vendor_name","text"), f("vendor_address","address"),
                f("buyer_name","text"), f("buyer_address","address"),
                f("total_amount","number"), f("currency","text")],
            line_item_fields: vec![f("description","text"), f("quantity","number"),
                f("unit_price","number"), f("line_total","number")] },
        "po" => DocTemplate { doctype: "po".into(),
            header_fields: vec![f("po_number","text"), f("order_date","date"),
                f("buyer_name","text"), f("buyer_address","address"),
                f("vendor_name","text"), f("vendor_address","address"),
                f("total_amount","number"), f("currency","text")],
            line_item_fields: vec![f("description","text"), f("quantity","number"),
                f("unit_price","number"), f("line_total","number")] },
        "statement" => DocTemplate { doctype: "statement".into(),
            header_fields: vec![f("account_number","text"), f("account_holder","text"),
                f("statement_date","date"), f("period_start","date"), f("period_end","date"),
                f("opening_balance","number"), f("closing_balance","number"), f("currency","text")],
            line_item_fields: vec![f("transaction_date","date"), f("description","text"),
                f("amount","number"), f("balance","number")] },
        "receipt" => DocTemplate { doctype: "receipt".into(),
            header_fields: vec![f("merchant_name","text"), f("merchant_address","address"),
                f("purchase_date","date"), f("total_amount","number"), f("payment_method","text")],
            line_item_fields: vec![] },
        other => return Err(format!("unknown doctype: {other}")),
    };
    Ok(t)
}

pub fn template_list() -> Vec<String> {
    vec!["invoice".into(), "po".into(), "statement".into(), "receipt".into()]
}

pub fn template_json(doctype: &str) -> Result<String, String> {
    template(doctype).map(|t| serde_json::to_string(&t).expect("template serializes"))
}

pub fn template_list_json() -> String {
    serde_json::to_string(&template_list()).expect("list serializes")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn receipt_is_flat() {
        let t = template("receipt").unwrap();
        assert_eq!(t.doctype, "receipt");
        assert!(t.line_item_fields.is_empty());
        assert_eq!(t.header_fields[0].name, "merchant_name");
        assert_eq!(t.header_fields[0].kind, "text");
    }
    #[test]
    fn invoice_has_line_items() {
        let t = template("invoice").unwrap();
        assert_eq!(t.header_fields.len(), 8);
        assert_eq!(t.line_item_fields.len(), 4);
        assert_eq!(t.line_item_fields[0].name, "description");
    }
    #[test]
    fn unknown_doctype_errs() { assert!(template("nope").is_err()); }
    #[test]
    fn list_is_stable_order() {
        assert_eq!(template_list(), vec!["invoice","po","statement","receipt"]);
    }
    #[test]
    fn template_json_key_order_is_declaration_order() {
        // serialize the STRUCT, not json!/BTreeMap -- doctype, header_fields, line_item_fields
        let j = template_json("receipt").unwrap();
        assert!(j.starts_with(r#"{"doctype":"receipt","header_fields":["#));
        assert!(j.contains(r#""line_item_fields":[]"#));
    }
}
