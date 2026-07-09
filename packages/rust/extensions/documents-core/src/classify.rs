//! Doctype classification kernel -- the fixed classify prompt + the parser for
//! the VLM's classify response. Deterministic, no I/O. The prompt is a fixed
//! constant (mirror `prompt.rs::suggest_prompt`); the parser reuses the
//! `parse.rs` fence-strip discipline (`rfind`/rsplit, NOT `strip_suffix`).
use serde::Serialize;

/// The four registry doctypes plus the `generic` escape hatch.
const DOCTYPES: [&str; 5] = ["invoice", "po", "statement", "receipt", "generic"];

pub fn classify_prompt() -> &'static str {
    "You are shown a document. Classify it as exactly one of these types: invoice, po, \
     statement, receipt. If it is none of these, answer \"generic\". Return ONLY JSON: \
     {\"doctype\": \"<one of: invoice|po|statement|receipt|generic>\", \"confidence\": \
     <0..1>}. No prose."
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ClassifyResult {
    pub doctype: String,
    pub confidence: f64,
}

/// Strip a leading ```json (or ```) fence -- same discipline as
/// `parse.rs::parse_message_text`: drop from the LAST ``` (rfind, mirrors
/// Python `rsplit("```", 1)[0]`), NOT `strip_suffix`.
fn strip_fence(text: &str) -> String {
    let mut t = text.trim().to_string();
    if t.starts_with("```") {
        if let Some(nl) = t.find('\n') {
            t = t[nl + 1..].to_string();
            if let Some(idx) = t.rfind("```") {
                t = t[..idx].to_string();
            }
        } // no newline -> leave as-is (Python edge case)
    }
    t.trim().to_string()
}

pub fn parse_classify(text: &str) -> Result<ClassifyResult, String> {
    let t = strip_fence(text);
    let v: serde_json::Value = serde_json::from_str(&t).map_err(|e| e.to_string())?;
    let doctype = v
        .get("doctype")
        .and_then(|d| d.as_str())
        .ok_or("classify response missing string 'doctype'")?;
    if !DOCTYPES.contains(&doctype) {
        return Err(format!("unknown doctype: {doctype}"));
    }
    let conf = v
        .get("confidence")
        .ok_or("classify response missing 'confidence'")?
        .as_f64()
        .ok_or("confidence is not a number")?;
    let conf = conf.clamp(0.0, 1.0);
    Ok(ClassifyResult {
        doctype: doctype.to_string(),
        confidence: conf,
    })
}

pub fn parse_classify_json(text: &str) -> Result<String, String> {
    parse_classify(text).map(|r| serde_json::to_string(&r).expect("classify serializes"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_prompt_is_the_fixed_constant() {
        assert!(classify_prompt().starts_with("You are shown a document."));
        assert!(classify_prompt().ends_with("No prose."));
    }

    #[test]
    fn parse_clean_json() {
        let r = parse_classify(r#"{"doctype":"invoice","confidence":0.9}"#).unwrap();
        assert_eq!(r.doctype, "invoice");
        assert_eq!(r.confidence, 0.9);
    }

    #[test]
    fn parse_fenced_blob() {
        let r =
            parse_classify("```json\n{\"doctype\":\"receipt\",\"confidence\":0.5}\n```").unwrap();
        assert_eq!(r.doctype, "receipt");
        assert_eq!(r.confidence, 0.5);
    }

    #[test]
    fn unknown_doctype_errs() {
        assert!(parse_classify(r#"{"doctype":"nope","confidence":0.9}"#).is_err());
    }

    #[test]
    fn missing_confidence_errs() {
        assert!(parse_classify(r#"{"doctype":"invoice"}"#).is_err());
    }

    #[test]
    fn out_of_range_confidence_clamps() {
        let hi = parse_classify(r#"{"doctype":"po","confidence":1.5}"#).unwrap();
        assert_eq!(hi.confidence, 1.0);
        let lo = parse_classify(r#"{"doctype":"po","confidence":-0.3}"#).unwrap();
        assert_eq!(lo.confidence, 0.0);
    }

    #[test]
    fn generic_is_valid() {
        let r = parse_classify(r#"{"doctype":"generic","confidence":0.2}"#).unwrap();
        assert_eq!(r.doctype, "generic");
    }

    #[test]
    fn json_round_trip_key_order() {
        let j = parse_classify_json(r#"{"doctype":"invoice","confidence":0.9}"#).unwrap();
        assert_eq!(j, r#"{"doctype":"invoice","confidence":0.9}"#);
    }
}

#[cfg(test)]
mod byte_exact_check {
    use super::*;
    #[test]
    fn matches_python_exactly() {
        let expected = "You are shown a document. Classify it as exactly one of these types: invoice, po, statement, receipt. If it is none of these, answer \"generic\". Return ONLY JSON: {\"doctype\": \"<one of: invoice|po|statement|receipt|generic>\", \"confidence\": <0..1>}. No prose.";
        assert_eq!(classify_prompt(), expected);
    }
}
