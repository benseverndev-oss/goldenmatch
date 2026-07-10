pub fn parse_message_text(resp_json: &str) -> Result<String, String> {
    let v: serde_json::Value = serde_json::from_str(resp_json).map_err(|e| e.to_string())?;
    let choice = v.get("choices").and_then(|c| c.get(0))
        .ok_or("unexpected response envelope: missing choices[0]")?;
    if choice.get("finish_reason").and_then(|f| f.as_str()) == Some("length") {
        return Err("response truncated (finish_reason=length); increase max_tokens".into());
    }
    let text = choice.get("message").and_then(|m| m.get("content")).and_then(|c| c.as_str())
        .ok_or("response has no message content")?;
    let mut t = text.trim().to_string();
    if t.starts_with("```") {
        if let Some(nl) = t.find('\n') {
            t = t[nl + 1..].to_string();
            // Python `rsplit("```", 1)[0]`: drop from the LAST ``` (anywhere), NOT just a
            // trailing one. `strip_suffix` is WRONG here (diverges when content follows the
            // closing fence). Use rfind to mirror rsplit.
            if let Some(idx) = t.rfind("```") { t = t[..idx].to_string(); }
        } // no newline -> leave as-is (Python edge case)
    }
    Ok(t.trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn happy_and_fence() {
        assert_eq!(parse_message_text(r#"{"choices":[{"message":{"content":"hello"}}]}"#).unwrap(), "hello");
        assert_eq!(parse_message_text("{\"choices\":[{\"message\":{\"content\":\"```json\\n{\\\"a\\\":1}\\n```\"}}]}").unwrap(), "{\"a\":1}");
    }
    #[test]
    fn errors() {
        assert!(parse_message_text(r#"{"choices":[]}"#).is_err());                        // envelope
        assert!(parse_message_text(r#"{"choices":[{"finish_reason":"length","message":{"content":"x"}}]}"#).unwrap_err().contains("truncated"));
        assert!(parse_message_text(r#"{"choices":[{"message":{"content":123}}]}"#).is_err()); // non-str content
    }
    #[test]
    fn fence_no_newline_left_unstripped() {
        // matches the Python edge case: startswith ``` but no newline -> returned as-is (trimmed)
        assert_eq!(parse_message_text(r#"{"choices":[{"message":{"content":"```abc"}}]}"#).unwrap(), "```abc");
    }
}
