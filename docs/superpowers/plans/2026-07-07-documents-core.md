# documents-core Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pyo3-free `goldenmatch-documents-core` Rust crate (4 deterministic kernels) as the single source of truth, bound to Python via the aggregated `native/` extension and to TS via a `documents-wasm` crate, with the shipped pure-Python code as the lossy fallback and a parity corpus locking Rust == Python.

**Architecture:** `documents-core` holds schema validate/round-trip, response parse, prompt build, and record normalize — the drift-prone logic. `native/src/documents.rs` pyo3-wraps it (string in / string out, like `suggest.rs`). Python call sites try native via `_native_loader`, else run the existing pure-Python impl unchanged. `documents-wasm` mirrors `suggest-wasm`. A JSONL corpus + parity test assert native == pure-Python.

**Tech Stack:** Rust (serde/serde_json, pyo3 in the native shim, wasm-bindgen in the wasm crate), Python 3.11+, pytest.

**Spec:** `docs/superpowers/specs/2026-07-07-documents-core-design.md`

---

## Conventions for every task

- **Worktree:** `D:/show_case/gm-docs-core` (branch `feat/documents-core`). Do NOT push, do NOT touch `main`.
- **Rust build env (exFAT D: — REQUIRED, per `reference_rustup_proxy_exfat_direct_binary`):**
  ```bash
  export CARGO_HOME=D:/.cargo
  export PATH="D:/.rustup/toolchains/1.94.0/bin:$PATH"   # rustc/cargo direct binaries
  ```
  Verify once: `rustc --version` and `cargo --version` resolve. Rust crates live under
  `packages/rust/extensions/`.
- **Python test env** (from `packages/python/goldenmatch`):
  ```bash
  cd D:/show_case/gm-docs-core/packages/python/goldenmatch
  PY="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="D:/show_case/gm-docs-core/packages/python/goldenmatch;D:/show_case/gm-docs-core/packages/python/goldenflow"
  export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
  ```
  Note: do NOT globally set `GOLDENMATCH_NATIVE=0` — Task 6/7 toggle it explicitly.
- **Rust build verification** (per `feedback_verify_rust_builds_explicitly`): after each Rust change,
  run the crate's own `cargo test` / `cargo build` and grep for `^error` — do not trust a piped tail.
- **Commit trailers:** copy from `git log -1 --format=%B`. `git -c commit.gpgsign=false commit`.

---

## File structure (locked)

| path | responsibility |
|---|---|
| `packages/rust/extensions/documents-core/Cargo.toml` + `src/{lib,schema,parse,prompt,normalize}.rs` | the 4 pyo3-free kernels + Rust unit tests |
| `packages/rust/extensions/native/src/documents.rs` | pyo3 shims (string in/out) delegating to the core |
| `packages/rust/extensions/native/Cargo.toml` (modify) | `goldenmatch-documents-core = { path = "../documents-core" }` |
| `packages/rust/extensions/native/src/lib.rs` (modify) | `mod documents;` + register the 4 functions in the `#[pymodule]` |
| `packages/rust/extensions/documents-wasm/` | wasm-bindgen crate over the core (build check only) |
| `goldenmatch/core/_native_loader.py` (modify) | add a `"documents"` entry to `_COMPONENT_SYMBOLS` |
| `goldenmatch/documents/{schema_io,_openai,vlm_backend,suggest,types}.py` (modify) | native-with-fallback at each kernel call site (pure-Python body stays as the fallback) |
| `scripts/gen_documents_corpus.py` + `tests/parity/documents_corpus.jsonl` + `tests/parity/test_documents_parity.py` | parity harness (native vs pure vs committed expected) |

The kernels take/return JSON strings at the pyo3/wasm boundary (mirrors `suggest_from_json`), so the
shim stays logic-free and both surfaces share one contract.

---

## Task 1: Scaffold `documents-core` crate

**Files:** Create `packages/rust/extensions/documents-core/Cargo.toml`, `src/lib.rs`

- [ ] **Step 1:** Create `Cargo.toml` (mirror `suggest-core` minus arrow — this crate is pure JSON/string):
  ```toml
  # Standalone workspace so this pyo3-free core can be a path dependency of BOTH the
  # `native` crate and `documents-wasm` without either workspace claiming it. Same
  # isolation rationale as score-core/suggest-core. No rust-toolchain.toml on purpose.
  [workspace]

  [package]
  name = "goldenmatch-documents-core"
  version = "0.1.0"
  edition = "2021"
  license = "MIT"
  authors = ["Ben Severn <benzsevern@gmail.com>"]
  description = "Deterministic document-ingest kernels (schema/parse/prompt/normalize, no pyo3) shared across the native ext and the TS/WASM surface"

  [lib]
  name = "goldenmatch_documents_core"

  [dependencies]
  serde = { version = "1", features = ["derive"] }
  serde_json = "1"
  ```
- [ ] **Step 2:** Create `src/lib.rs`:
  ```rust
  //! `goldenmatch-documents-core` -- pyo3-free document-ingest kernels. Single source
  //! of truth for schema validation, response parsing, prompt text, and record
  //! normalization. No I/O, no pyo3. String-in / string-out at the boundary.
  pub mod normalize;
  pub mod parse;
  pub mod prompt;
  pub mod schema;

  #[cfg(test)]
  mod tests {
      #[test]
      fn crate_builds() {
          assert_eq!(2 + 2, 4);
      }
  }
  ```
- [ ] **Step 3:** Build: `cd packages/rust/extensions/documents-core && cargo test 2>&1 | tee /tmp/dc.log; grep -c '^error' /tmp/dc.log` → 0 errors, `crate_builds` passes.
- [ ] **Step 4: Commit** `feat(documents-core): scaffold crate`.

---

## Task 2: schema kernel

**Files:** Create `src/schema.rs`; modify `src/lib.rs` (add tests run under `cargo test`)

Mirror `schema_io.schema_from_dict`/`schema_to_dict` + `types.Field` defaults EXACTLY.

- [ ] **Step 1: Write failing Rust tests** at the bottom of `schema.rs`:
  ```rust
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
  ```
- [ ] **Step 2:** `cargo test -p goldenmatch-documents-core schema 2>&1 | grep -E '^error|test result'` → fails (functions missing).
- [ ] **Step 3: Implement `schema.rs`:**
  ```rust
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

  #[derive(Clone, Debug, PartialEq)]
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
  pub fn schema_to_json(schema: &TargetSchema) -> String {
      let items: Vec<serde_json::Value> = schema.fields.iter().map(|f| serde_json::json!({
          "name": f.name, "kind": f.kind, "hint": f.hint,
      })).collect();
      serde_json::json!({"fields": items}).to_string()
  }
  ```
  NOTE: `serde_json::Value::to_string()` emits compact JSON with keys in insertion order for
  `json!` objects — verify the corpus (Task 7) compares against Python `json.dumps(...,)` with the
  SAME separators. If Python uses `indent`/spaces anywhere, normalize both sides in the corpus
  comparison (parse-then-compare) rather than raw-string compare. (schema_to_dict returns a dict;
  the Python side dumps it — Task 7 compares the parsed dict, not the raw string, to avoid
  whitespace-formatting false diffs.)
- [ ] **Step 4:** `cargo test -p goldenmatch-documents-core schema` → passes.
- [ ] **Step 5: Commit** `feat(documents-core): schema validate + round-trip kernel`.

---

## Task 3: parse kernel

**Files:** Create `src/parse.rs`. Mirror `_openai.parse_message_text` EXACTLY, including the
fence-strip no-newline edge case (text starting with ``` but with no newline is returned
unstripped — do NOT "fix" this).

- [ ] **Step 1: Failing tests:**
  ```rust
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
  ```
- [ ] **Step 2:** `cargo test ... parse` → fails.
- [ ] **Step 3: Implement `parse.rs`** (translate the Python line-for-line):
  ```rust
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
              if let Some(stripped) = t.strip_suffix("```") { t = stripped.to_string(); }
          } // no newline -> leave as-is (Python edge case)
      }
      Ok(t.trim().to_string())
  }
  ```
  Verify the Python `_strip_fence`/`parse_message_text` split semantics: Python does
  `text.split("\n",1)[1].rsplit("```",1)[0]` — i.e. drop everything up to & incl. the first
  newline, then drop from the LAST ```` ``` ````. The Rust above matches (find first `\n`,
  strip_suffix). Confirm against the real source before finalizing.
- [ ] **Step 4:** `cargo test ... parse` → passes.
- [ ] **Step 5: Commit** `feat(documents-core): response-parse kernel (byte-parity, fence edge case)`.

---

## Task 4: prompt kernel

**Files:** Create `src/prompt.rs`. Byte-match `vlm_backend._instruction` and `suggest._PROMPT`.

- [ ] **Step 1: Failing tests** — assert the EXACT strings (copy them verbatim from the current
  `vlm_backend._instruction` and `suggest._PROMPT` in this worktree; reproduce the exact newlines
  and the per-field line format `- "name" (kind): hint`):
  ```rust
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
  ```
- [ ] **Step 2:** `cargo test ... prompt` → fails.
- [ ] **Step 3: Implement `prompt.rs`** — reproduce the Python strings byte-for-byte:
  ```rust
  use crate::schema::TargetSchema;

  pub fn extract_instruction(schema: &TargetSchema) -> String {
      let lines: Vec<String> = schema.fields.iter().map(|f| {
          let base = format!("- \"{}\" ({})", f.name, f.kind);
          match &f.hint { Some(h) => format!("{base}: {h}"), None => base }
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
  ```
  CRITICAL: Rust `\` line-continuations in string literals eat the following whitespace, but the
  Python source uses implicit string concatenation with specific spaces. Diff the produced string
  against the Python output character-by-character (the Task 7 corpus does this) — a single space
  difference breaks VLM-prompt parity. If the `\`-continuation form is error-prone, build the string
  with explicit `concat!`/`push_str` of the exact segments instead.
- [ ] **Step 4:** `cargo test ... prompt` → passes.
- [ ] **Step 5: Commit** `feat(documents-core): prompt kernel (byte-exact extract + suggest text)`.

---

## Task 5: normalize kernel (the str() parity trap)

**Files:** Create `src/normalize.rs`. Mirror `ExtractedRow.from_partial` coercion.

**Python `str()` semantics to replicate for non-null JSON scalars:** JSON string → as-is;
`true`→`"True"`, `false`→`"False"` (Python capitalization, NOT Rust "true"); integer → decimal
string; **float → Python `str(float)`** (shortest round-trip repr — the hard case).

- [ ] **Step 1: Failing tests:**
  ```rust
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
  ```
- [ ] **Step 2:** `cargo test ... normalize` → fails.
- [ ] **Step 3: Implement `normalize.rs`** using an ordered map (preserve schema column order):
  ```rust
  use crate::schema::TargetSchema;
  use serde_json::Value;
  use std::collections::BTreeMap; // or an ordered Vec of (col, val) to match column order

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
      row.confidence.values().cloned().fold(f64::INFINITY, f64::min).min(f64::INFINITY)
          .pipe_or_zero_if_empty(row) // see below
  }
  ```
  Implement `row_confidence` plainly: if `confidence` empty → 0.0, else min of values (do NOT use
  the pseudo `.pipe_or_zero_if_empty`; that's a placeholder — write the explicit empty check).
- [ ] **Step 4:** `cargo test ... normalize` → passes.
- [ ] **Step 5: FLOAT PARITY note for the executor:** the corpus (Task 7) MUST include float values.
  If Rust `serde_json` float `to_string()` diverges from Python `str(float)` on any corpus row and
  you cannot match it cheaply, **do NOT ship a divergence** — add `"documents"` (or a
  finer-grained sub-symbol) to `_FALLBACK_ONLY` in Task 6 and record the reason. Ints/bools/strings
  (the overwhelmingly common VLM outputs) must match regardless.
- [ ] **Step 6: Commit** `feat(documents-core): normalize kernel (Python str() coercion)`.

---

## Task 6: Native binding (`native/src/documents.rs`) + Python fallback wiring

**Files:** Create `native/src/documents.rs`; modify `native/Cargo.toml`, `native/src/lib.rs`,
`goldenmatch/core/_native_loader.py`, and the 5 documents `.py` call sites.

- [ ] **Step 1:** `native/src/documents.rs` — thin pyo3 shims (mirror `suggest.rs`; string in/out):
  ```rust
  use pyo3::exceptions::PyValueError;
  use pyo3::prelude::*;
  use goldenmatch_documents_core as core;

  #[pyfunction] pub fn documents_schema_validate(schema_json: &str) -> PyResult<String> {
      core::schema::schema_from_json(schema_json).map(|s| core::schema::schema_to_json(&s))
          .map_err(PyValueError::new_err)
  }
  #[pyfunction] pub fn documents_parse_message_text(resp_json: &str) -> PyResult<String> {
      core::parse::parse_message_text(resp_json).map_err(PyValueError::new_err)
  }
  #[pyfunction] pub fn documents_extract_instruction(schema_json: &str) -> PyResult<String> {
      let s = core::schema::schema_from_json(schema_json).map_err(PyValueError::new_err)?;
      Ok(core::prompt::extract_instruction(&s))
  }
  #[pyfunction] pub fn documents_suggest_prompt() -> String { core::prompt::suggest_prompt().to_string() }
  #[pyfunction] pub fn documents_normalize_record(values_json: &str, confidence_json: &str, schema_json: &str) -> PyResult<String> {
      let s = core::schema::schema_from_json(schema_json).map_err(PyValueError::new_err)?;
      let row = core::normalize::normalize_record(values_json, confidence_json, &s).map_err(PyValueError::new_err)?;
      // return {"values": {...}, "confidence": {...}} as JSON for the Python side
      let values: serde_json::Map<_,_> = row.values.into_iter().map(|(k,v)| (k, serde_json::json!(v))).collect();
      let confidence: serde_json::Map<_,_> = row.confidence.into_iter().map(|(k,v)| (k, serde_json::json!(v))).collect();
      Ok(serde_json::json!({"values": values, "confidence": confidence}).to_string())
  }
  ```
  (Add `serde_json = "1"` to `native/Cargo.toml` deps if not already present.)
- [ ] **Step 2:** `native/Cargo.toml`: add `goldenmatch-documents-core = { path = "../documents-core" }`.
  `native/src/lib.rs`: add `mod documents;` and register all five:
  `m.add_function(wrap_pyfunction!(documents::documents_schema_validate, m)?)?;` (and the other four).
- [ ] **Step 3: Build the native extension** so `goldenmatch._native` carries the new symbols. Use the
  repo's native build (maturin develop against `packages/rust/extensions/native`, or the existing
  build script if present). With the exFAT Rust env set. Verify:
  `"$PY" -c "import goldenmatch._native as n; print(hasattr(n,'documents_parse_message_text'))"` → True.
  If the native build is too heavy/blocked in this environment, report the blocker — the Python
  fallback wiring (Steps 4-5) and the pure-Python corpus (Task 7) can still land; the native leg of
  the parity test is then exercised in CI's `GOLDENMATCH_NATIVE=1` lane.
- [ ] **Step 4:** `_native_loader.py`: add to `_COMPONENT_SYMBOLS`:
  ```python
      "documents": ("documents_parse_message_text", "documents_schema_validate",
                    "documents_extract_instruction", "documents_normalize_record"),
  ```
- [ ] **Step 5:** Re-point the 5 Python call sites to native-with-fallback, keeping the existing pure
  body as the `else`. Pattern (per `autoconfig.py`):
  ```python
  from goldenmatch.core._native_loader import native_enabled, native_module
  # in schema_io.schema_from_dict / _openai.parse_message_text / vlm_backend._instruction /
  # suggest._PROMPT accessor / types.ExtractedRow.from_partial:
  if native_enabled("documents") and (nm := native_module()) is not None and hasattr(nm, "<symbol>"):
      # call the kernel (JSON in/out), adapt result to the Python return type
      ...
  else:
      <existing pure-Python body>
  ```
  Each site keeps its current tests green. `parse_message_text` native returns text or raises
  ValueError (map the PyValueError message back). `from_partial` calls `documents_normalize_record`
  and rebuilds the `ExtractedRow`. `_instruction`/suggest prompt call the prompt kernels.
- [ ] **Step 6:** Run the full documents suite BOTH ways and confirm identical green:
  - native present: `"$PY" -m pytest tests/documents -q`
  - forced fallback: `GOLDENMATCH_NATIVE=0 "$PY" -m pytest tests/documents -q`
  Expect the same `N passed` both times.
- [ ] **Step 7: Commit** `feat(documents): native kernel binding + fallback wiring`.

---

## Task 7: Parity corpus + test

**Files:** Create `scripts/gen_documents_corpus.py`, `tests/parity/documents_corpus.jsonl`,
`tests/parity/test_documents_parity.py`

- [ ] **Step 1:** `gen_documents_corpus.py` — enumerate inputs for all four kernels (schemas incl.
  hints; response envelopes incl. fenced/truncated/malformed; normalize records incl.
  string/int/float/bool/null/missing/unknown-key), run the PURE-PYTHON impls, and write
  `{kernel, input, expected}` JSONL. Commit the generated `documents_corpus.jsonl`.
- [ ] **Step 2:** `test_documents_parity.py`:
  - For each corpus row, run the PURE-PYTHON kernel → assert == `expected` (locks Python behavior;
    compare PARSED JSON for schema/normalize to avoid whitespace false-diffs).
  - If `native_enabled("documents")` and the symbol exists, ALSO run the native kernel → assert ==
    `expected`. Skip the native leg (with a reason) when native isn't importable, so the test is
    green on a pure-Python machine and strict in CI's `GOLDENMATCH_NATIVE=1` lane.
- [ ] **Step 3:** Run: `"$PY" -m pytest tests/parity/test_documents_parity.py -q` (pure leg green;
  native leg green if built).
- [ ] **Step 4: Commit** `test(documents): Rust==Python parity corpus`.

---

## Task 8: `documents-wasm` crate (build check)

**Files:** Create `packages/rust/extensions/documents-wasm/{Cargo.toml, src/lib.rs}`

Mirror `suggest-wasm`. Export the four kernels via `wasm-bindgen` (string in/out). No TS consumer
yet — this task only proves the core compiles to wasm and exports the surface.

- [ ] **Step 1:** Create the crate mirroring `suggest-wasm/Cargo.toml` (cdylib, wasm-bindgen,
  path-dep `goldenmatch-documents-core`) and `src/lib.rs` with `#[wasm_bindgen]` fns
  `schema_validate`, `parse_message_text`, `extract_instruction`, `suggest_prompt`,
  `normalize_record` delegating to the core (returning `Result<String, JsError>`).
- [ ] **Step 2: Build check:** `cd packages/rust/extensions/documents-wasm && cargo build --target wasm32-unknown-unknown 2>&1 | tee /tmp/dw.log; grep -c '^error' /tmp/dw.log` → 0. (Install the target first if needed: `rustup target add wasm32-unknown-unknown`.) If wasm tooling isn't available in this env, report BLOCKED — the crate + Cargo are still committed for CI to build.
- [ ] **Step 3: Commit** `feat(documents-wasm): wasm-bindgen surface over documents-core`.

---

## Done-when

- `documents-core` `cargo test` green (4 kernels, unit-tested).
- `tests/documents` green with native present AND `GOLDENMATCH_NATIVE=0` (identical counts).
- Parity corpus: pure-Python == expected always; native == expected when built (strict in CI's
  `GOLDENMATCH_NATIVE=1` lane).
- `documents-wasm` compiles to `wasm32-unknown-unknown` exporting the four kernels.
- Any kernel that can't match Python byte-for-byte (float `str()`) is on `_FALLBACK_ONLY` with a
  recorded reason, NOT shipped as a silent divergence.
- Deferred: TS consumption of the WASM; REST/A2A/Web UI surfaces; moving assemble/IO into Rust.
