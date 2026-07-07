use crate::schema::TargetSchema;

pub fn extract_instruction(schema: &TargetSchema) -> String {
    let lines: Vec<String> = schema.fields.iter().map(|f| {
        let base = format!("- \"{}\" ({})", f.name, f.kind);
        // Python: `(f": {f.hint}" if f.hint else "")` -- an EMPTY-STRING hint is falsy, so no
        // `: ` is appended. `Some("")` must behave like None here.
        match &f.hint { Some(h) if !h.is_empty() => format!("{base}: {h}"), _ => base }
    }).collect();
    let cols = schema.column_names().join(", ");
    format!(
        "Extract every record present in the attached document image(s).\n\
         A form/card/ID is ONE record; a table/list is MANY records (one per row).\n\
         Target fields:\n{lines}\n\n\
         Return ONLY a JSON object of the form:\n\
         {{\"records\": [{{\"values\": {{<field>: <string or null>, ...}}, \"confidence\": {{<field>: <0..1>, ...}}}}, ...]}}\n\
         Use exactly these field keys: {cols}. Omit a field if absent. No prose.",
        lines = lines.join("\n"), cols = cols,
    )
}

pub fn suggest_prompt() -> &'static str {
    "You are shown a sample document. Propose a compact extraction schema: the fields a \
     person would want pulled from documents like this for record matching (names, \
     emails, addresses, phones, ids, dates...). Return ONLY JSON:\n\
     {\"fields\": [{\"name\": \"<snake_case>\", \"kind\": \"text|email|phone|address|date|number\", \"hint\": \"<short guidance>\"}, ...]}\n\
     Prefer 3-12 stable, matchable fields. No prose."
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{Field, TargetSchema};
    #[test]
    fn extract_instruction_exact() {
        let s = TargetSchema { fields: vec![
            Field{name:"full_name".into(), kind:"text".into(), hint:None},
            Field{name:"email".into(), kind:"email".into(), hint:Some("work".into())}]};
        let got = extract_instruction(&s);
        assert!(got.starts_with("Extract every record present"));
        assert!(got.contains("- \"full_name\" (text)\n- \"email\" (email): work"));
        assert!(got.ends_with("Use exactly these field keys: full_name, email. Omit a field if absent. No prose."));
    }
    #[test]
    fn suggest_prompt_is_the_fixed_constant() {
        assert!(suggest_prompt().starts_with("You are shown a sample document."));
        assert!(suggest_prompt().ends_with("Prefer 3-12 stable, matchable fields. No prose."));
    }
}

#[cfg(test)]
mod byte_exact_check {
    use super::*;
    use crate::schema::{Field, TargetSchema};
    #[test]
    fn matches_python_exactly() {
        let s = TargetSchema { fields: vec![
            Field{name:"full_name".into(), kind:"text".into(), hint:None},
            Field{name:"email".into(), kind:"email".into(), hint:Some("work".into())}]};
        let expected_instruction = "Extract every record present in the attached document image(s).\nA form/card/ID is ONE record; a table/list is MANY records (one per row).\nTarget fields:\n- \"full_name\" (text)\n- \"email\" (email): work\n\nReturn ONLY a JSON object of the form:\n{\"records\": [{\"values\": {<field>: <string or null>, ...}, \"confidence\": {<field>: <0..1>, ...}}, ...]}\nUse exactly these field keys: full_name, email. Omit a field if absent. No prose.";
        assert_eq!(extract_instruction(&s), expected_instruction);
        let expected_prompt = "You are shown a sample document. Propose a compact extraction schema: the fields a person would want pulled from documents like this for record matching (names, emails, addresses, phones, ids, dates...). Return ONLY JSON:\n{\"fields\": [{\"name\": \"<snake_case>\", \"kind\": \"text|email|phone|address|date|number\", \"hint\": \"<short guidance>\"}, ...]}\nPrefer 3-12 stable, matchable fields. No prose.";
        assert_eq!(suggest_prompt(), expected_prompt);
    }
}
