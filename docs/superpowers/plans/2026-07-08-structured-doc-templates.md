# Structured-Doc Templates Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Layer per-doctype templates (invoice, PO, statement, receipt) over the generic VLM document extractor so structured docs extract into a typed header record + linked line-item records.

**Architecture:** Core-first. Deterministic logic (template definitions, classify prompt/parse, structured-response parse) lives in the pyo3-free `goldenmatch-documents-core` crate = single source of truth, bound to Python via `_native` shims and (last task) to TS via wasm. Pure-Python is the lossy fallback, guarded by a parity corpus. VLM calls (classify, structured-extract) are Python behind the existing injectable `Transport`. `ingest_documents` owns a per-doc dispatch that routes flat vs structured and assembles two frames joined by a stable `_doc_id`.

**Tech Stack:** Rust (serde, no pyo3 in core; pyo3 in `native`), Python 3 (polars, dataclasses, stdlib urllib), maturin/abi3, wasm-pack (TS leg). Tests: `cargo test`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-08-structured-doc-templates-design.md`

---

## Reference: existing patterns to mirror (read before starting)

- **Core kernel + parity discipline:** `packages/rust/extensions/documents-core/src/{schema,prompt,normalize,parse}.rs`. Note: serialize structs (NOT `json!`/BTreeMap) for stable key order; empty-string hint is falsy. `prompt.rs` has a `byte_exact_check` test — it asserts Rust output equals a **hardcoded Rust string literal** that a human keeps in sync with Python; there is NO cross-language assertion at the Rust layer. The real cross-language guarantee is the `documents_corpus.jsonl` replay on the Python/TS side (pure == native == corpus == TS). So a Rust `byte_exact_check` = "does this kernel emit the exact bytes I wrote down", and the corpus = "do pure/native/TS agree with the recorded expectation". **Order-sensitive kernels** (normalize, and this spec's `parse_structured`) do NOT emit column-ordered JSON — `normalize_record` returns `BTreeMap`s and the shim serializes them **alphabetically**; column order is re-imposed downstream in Python (`ExtractedRow.from_partial` rebuilds `{c: pv.get(c) for c in cols}`) and asserted in the corpus via ordered `[col, val]` pairs. Do NOT assert raw-JSON column order for these.
- **Native shim + registration (TWO touch points per symbol):** `packages/rust/extensions/native/src/documents.rs` (the `#[pyfunction]` shims) + `packages/rust/extensions/native/src/lib.rs:74-78` (`wrap_pyfunction!` lines).
- **Python dual-path with `hasattr` guard (the #688 wheel-skew lesson):** `documents/vlm_backend.py::_instruction` and `documents/types.py::ExtractedRow.from_partial` — each checks `native_enabled("documents") and (nm := native_module()) is not None and hasattr(nm, "<symbol>")` before calling native, else pure-Python.
- **Parity corpus + harness:** `packages/python/goldenmatch/tests/parity/documents_corpus.jsonl` (one `{"kernel","input","expected"}` per line) + `tests/parity/test_documents_parity.py` (`_run_pure`, `_run_native`, `_KERNEL_SYMBOL`). Generator: `packages/python/goldenmatch/scripts/gen_documents_corpus.py`.
- **Flat extractor seam:** `documents/extractor.py` (`Extractor` Protocol + `FakeExtractor`), `documents/vlm_backend.py` (`VLMExtractor`), `documents/config.py::resolve_extractor`.
- **Assemble + report:** `documents/assemble.py` (`SIDECARS`, `_empty_frame`, `assemble`), `documents/types.py::IngestReport`.
- **Public API:** `documents/__init__.py::ingest_documents`.

## Build & test commands (Windows / exFAT D: — see CLAUDE.md + memory)

- **Rust build/test (invoke exe by ABSOLUTE path; exFAT/git-bash mangles `D:/.rustup`):**
  ```bash
  RUSTUP_HOME=D:/.rustup CARGO_HOME=C:/Users/bsevern/.cargo \
    D:/.rustup/toolchains/1.94.0/bin/cargo.exe test -p goldenmatch-documents-core
  ```
  Verify a crate builds explicitly — grep `^error` (see memory `feedback_verify_rust_builds_explicitly`), never trust a piped tail.
- **Python tests (worktree; use main `.venv` python + PYTHONPATH; force pure path for dual-path leg):**
  ```bash
  PYTHONPATH="D:/show_case/gm-doc-templates/packages/python/goldenmatch;D:/show_case/gm-doc-templates/packages/python/goldenflow" \
    GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
    D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <path> -v
  ```
  (Native leg = drop `GOLDENMATCH_NATIVE=0` only after `scripts/build_native.py` rebuilds the wheel; on a pure box the native parity leg SKIPS, which is expected. CI runs the native lane.)
- **NEVER `git stash` in this worktree** — stash is repo-global across worktrees (memory `feedback_git_stash_shared_across_worktrees`).
- Commit after every green step. Do NOT push/PR until the finishing skill.

---

## Task 1: Template definitions (core + Python mirror + accessor)

Adds the four doctype templates as static data in the core crate, a Python literal mirror + accessor, the `DocTemplate` type, two native shims, and parity rows. Pure lookups — no parsing.

**Files:**
- Create: `packages/rust/extensions/documents-core/src/templates.rs`
- Modify: `packages/rust/extensions/documents-core/src/lib.rs` (add `pub mod templates;`)
- Modify: `packages/rust/extensions/native/src/documents.rs` (2 shims)
- Modify: `packages/rust/extensions/native/src/lib.rs` (2 `wrap_pyfunction!` lines)
- Create: `packages/python/goldenmatch/goldenmatch/documents/templates.py`
- Modify: `packages/python/goldenmatch/goldenmatch/documents/types.py` (add `DocTemplate`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/_native_loader.py` (add 2 symbols to `_COMPONENT_SYMBOLS["documents"]`)
- Modify: `packages/python/goldenmatch/scripts/gen_documents_corpus.py` (emit `template` rows)
- Modify: `packages/python/goldenmatch/tests/parity/test_documents_parity.py` (wire `template` kernel)
- Test: `documents-core/src/templates.rs` (`#[cfg(test)]`), `tests/documents/test_templates.py`

### The four templates (authoritative shape — identical in Rust + Python)

`kind` values are the existing set (`text|email|phone|address|date|number`). Header fields are the *entity/match* fields; line-item fields are the repeating detail. Receipt has **no** line items.

```
invoice:
  header:     invoice_number(text), invoice_date(date), vendor_name(text),
              vendor_address(address), buyer_name(text), buyer_address(address),
              total_amount(number), currency(text)
  line_items: description(text), quantity(number), unit_price(number), line_total(number)

po (purchase order):
  header:     po_number(text), order_date(date), buyer_name(text), buyer_address(address),
              vendor_name(text), vendor_address(address), total_amount(number), currency(text)
  line_items: description(text), quantity(number), unit_price(number), line_total(number)

statement (account/bank statement):
  header:     account_number(text), account_holder(text), statement_date(date),
              period_start(date), period_end(date), opening_balance(number),
              closing_balance(number), currency(text)
  line_items: transaction_date(date), description(text), amount(number), balance(number)

receipt:
  header:     merchant_name(text), merchant_address(address), purchase_date(date),
              total_amount(number), payment_method(text)
  line_items: (none)
```

All `hint`s are `None` for v1 (keep the definitions minimal; hints can be tuned later without a parity break as long as Rust + Python + corpus move together).

- [ ] **Step 1: Write the failing Rust test for the registry**

In `documents-core/src/templates.rs`, add tests first:

```rust
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
```

Run: `... cargo.exe test -p goldenmatch-documents-core templates` → FAIL (module doesn't exist).

- [ ] **Step 2: Implement `templates.rs`**

```rust
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
```

Add `pub mod templates;` to `documents-core/src/lib.rs`.

Run: `... cargo.exe test -p goldenmatch-documents-core templates` → PASS.

- [ ] **Step 3: Add the two native shims**

In `native/src/documents.rs`:

```rust
#[pyfunction]
pub fn documents_template(doctype: &str) -> PyResult<String> {
    core::templates::template_json(doctype).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn documents_template_list() -> String { core::templates::template_list_json() }
```

In `native/src/lib.rs` (after line 78):

```rust
    m.add_function(wrap_pyfunction!(documents::documents_template, m)?)?;
    m.add_function(wrap_pyfunction!(documents::documents_template_list, m)?)?;
```

Run: `RUSTUP_HOME=... cargo.exe build -p goldenmatch-native` → grep `^error` shows none. (Wheel not rebuilt yet; native leg exercised in CI. Do NOT block on a local wheel.)

- [ ] **Step 4: Write the failing Python test for the accessor + mirror**

`tests/documents/test_templates.py`:

```python
from goldenmatch.documents.templates import get_template, list_templates
from goldenmatch.documents.types import DocTemplate

def test_list_templates_stable_order():
    assert list_templates() == ["invoice", "po", "statement", "receipt"]

def test_receipt_is_flat():
    t = get_template("receipt")
    assert isinstance(t, DocTemplate)
    assert t.doctype == "receipt"
    assert t.line_items.fields == []
    assert t.header.column_names()[0] == "merchant_name"

def test_invoice_has_line_items():
    t = get_template("invoice")
    assert len(t.header.fields) == 8
    assert [f.name for f in t.line_items.fields] == ["description","quantity","unit_price","line_total"]

def test_unknown_doctype_raises():
    import pytest
    with pytest.raises(ValueError):
        get_template("nope")
```

Run (pure path): `... GOLDENMATCH_NATIVE=0 ... pytest tests/documents/test_templates.py -v` → FAIL (no module).

- [ ] **Step 5: Add `DocTemplate` to `types.py` and implement `templates.py`**

In `documents/types.py` (after `TargetSchema`):

```python
@dataclass(frozen=True)
class DocTemplate:
    doctype: str
    header: TargetSchema
    line_items: TargetSchema      # .fields == [] for flat doctypes (receipt)
```

Create `documents/templates.py` (dual-path: native `documents_template*` when present, else the literal mirror). The mirror is authoritative for the pure path and MUST equal the Rust output:

```python
"""Doctype template registry. Native (documents-core) is the source of truth;
the _PURE literals below are the lossy fallback + the thing the parity corpus
guards against drift. Adding/renaming a field is a THREE-place edit
(templates.rs + here + the corpus)."""
from __future__ import annotations

import json

from goldenmatch.documents.types import DocTemplate, Field, TargetSchema

def _f(name: str, kind: str) -> Field:
    return Field(name=name, kind=kind, hint=None)

_PURE: dict[str, DocTemplate] = {
    "invoice": DocTemplate("invoice",
        TargetSchema([_f("invoice_number","text"), _f("invoice_date","date"),
            _f("vendor_name","text"), _f("vendor_address","address"),
            _f("buyer_name","text"), _f("buyer_address","address"),
            _f("total_amount","number"), _f("currency","text")]),
        TargetSchema([_f("description","text"), _f("quantity","number"),
            _f("unit_price","number"), _f("line_total","number")])),
    "po": DocTemplate("po",
        TargetSchema([_f("po_number","text"), _f("order_date","date"),
            _f("buyer_name","text"), _f("buyer_address","address"),
            _f("vendor_name","text"), _f("vendor_address","address"),
            _f("total_amount","number"), _f("currency","text")]),
        TargetSchema([_f("description","text"), _f("quantity","number"),
            _f("unit_price","number"), _f("line_total","number")])),
    "statement": DocTemplate("statement",
        TargetSchema([_f("account_number","text"), _f("account_holder","text"),
            _f("statement_date","date"), _f("period_start","date"), _f("period_end","date"),
            _f("opening_balance","number"), _f("closing_balance","number"), _f("currency","text")]),
        TargetSchema([_f("transaction_date","date"), _f("description","text"),
            _f("amount","number"), _f("balance","number")])),
    "receipt": DocTemplate("receipt",
        TargetSchema([_f("merchant_name","text"), _f("merchant_address","address"),
            _f("purchase_date","date"), _f("total_amount","number"), _f("payment_method","text")]),
        TargetSchema([])),
}
_ORDER = ["invoice", "po", "statement", "receipt"]

def _from_native_json(doc: dict) -> DocTemplate:
    def sch(items): return TargetSchema([Field(name=i["name"], kind=i["kind"],
                                                hint=i.get("hint")) for i in items])
    return DocTemplate(doc["doctype"], sch(doc["header_fields"]), sch(doc["line_item_fields"]))

def list_templates() -> list[str]:
    from goldenmatch.core._native_loader import native_enabled, native_module
    if native_enabled("documents") and (nm := native_module()) is not None and hasattr(
        nm, "documents_template_list"
    ):
        return json.loads(nm.documents_template_list())
    return list(_ORDER)

def get_template(doctype: str) -> DocTemplate:
    from goldenmatch.core._native_loader import native_enabled, native_module
    if native_enabled("documents") and (nm := native_module()) is not None and hasattr(
        nm, "documents_template"
    ):
        return _from_native_json(json.loads(nm.documents_template(doctype)))  # raises ValueError on unknown
    if doctype not in _PURE:
        raise ValueError(f"unknown doctype: {doctype}")
    return _PURE[doctype]
```

Add the two symbols to `_COMPONENT_SYMBOLS["documents"]` in `core/_native_loader.py`.

Run (pure): `pytest tests/documents/test_templates.py -v` → PASS.

- [ ] **Step 6: Add `template` parity rows + wire the harness**

In `scripts/gen_documents_corpus.py`, emit rows for the `template` kernel: for each of the four doctypes, `input = {"doctype": name}`, `expected = {"ok": <the DocTemplate as the same JSON dict the native shim returns: {"doctype","header_fields","line_item_fields"}>}`; plus one error row `input={"doctype":"nope"}, expected={"error": true}`. Generate the `ok` payload from `_PURE` so pure and corpus can't disagree at generation time. Re-run the generator to append the rows to `documents_corpus.jsonl`.

In `tests/parity/test_documents_parity.py`: add `"template"` to `_KERNEL_SYMBOL` → `"documents_template"`; in `_run_pure`, `if kernel == "template": t = get_template(input_["doctype"]); return _template_to_dict(t)` (helper reshaping to `{"doctype", "header_fields":[{name,kind,hint}...], "line_item_fields":[...]}`); in `_run_native`, `return json.loads(nm.documents_template(input_["doctype"]))`. Error rows already raise `ValueError` on both legs.

Run (pure): `pytest tests/parity/test_documents_parity.py -k template -v` → PASS (native leg SKIPs on the pure box).

- [ ] **Step 7: Commit**

```bash
git add packages/rust/extensions/documents-core/src/templates.rs \
  packages/rust/extensions/documents-core/src/lib.rs \
  packages/rust/extensions/native/src/documents.rs packages/rust/extensions/native/src/lib.rs \
  packages/python/goldenmatch/goldenmatch/documents/templates.py \
  packages/python/goldenmatch/goldenmatch/documents/types.py \
  packages/python/goldenmatch/goldenmatch/core/_native_loader.py \
  packages/python/goldenmatch/scripts/gen_documents_corpus.py \
  packages/python/goldenmatch/tests/parity/test_documents_parity.py \
  packages/python/goldenmatch/tests/parity/documents_corpus.jsonl \
  packages/python/goldenmatch/tests/documents/test_templates.py
git commit -m "feat(documents): doctype template registry (core + python mirror + parity)"
```

---

## Task 2: Classify kernel (prompt + parse)

Adds the doctype-classification prompt and the classify-response parser to core, one shim each, Python accessors, parity rows. No VLM call here — just the deterministic prompt string + response parsing.

**Files:**
- Create: `packages/rust/extensions/documents-core/src/classify.rs`
- Modify: `documents-core/src/lib.rs` (`pub mod classify;`)
- Modify: `native/src/documents.rs` (2 shims) + `native/src/lib.rs` (2 registrations)
- Modify: `documents/config.py` OR new `documents/classify.py` — put the Python classify prompt/parse helpers in a NEW `documents/classify.py` (keeps config.py focused).
- Modify: `core/_native_loader.py` (2 symbols)
- Modify: `scripts/gen_documents_corpus.py` + `tests/parity/test_documents_parity.py`
- Test: `classify.rs` `#[cfg(test)]`, `tests/documents/test_classify.py`

**Classify prompt (fixed constant, byte-identical Rust + Python):**

```
You are shown a document. Classify it as exactly one of these types: invoice, po, statement, receipt. If it is none of these, answer "generic". Return ONLY JSON: {"doctype": "<one of: invoice|po|statement|receipt|generic>", "confidence": <0..1>}. No prose.
```

**Parse contract (`documents_parse_classify(text) -> {"doctype","confidence"}`):** strip a ```json fence if present (reuse the `parse.rs` fence-strip helper — `rfind`/rsplit, NOT `strip_suffix`); `json.loads`; require `doctype` a string in `{invoice,po,statement,receipt,generic}` (else `Err`); `confidence` coerced to f64 in `[0,1]` (missing → `Err`; out-of-range → clamp). Error parity by outcome.

- [ ] **Step 1:** Write failing Rust tests in `classify.rs`: `classify_prompt()` starts/ends with the exact constant; `parse_classify` on clean JSON, on a fenced blob, on unknown doctype (`Err`), on missing confidence (`Err`), on `confidence: 1.5` → clamped to `1.0`. Include a `byte_exact_check` mod asserting `classify_prompt()` equals a hardcoded Rust string literal (the exact prompt bytes — same style as `prompt.rs::byte_exact_check`; keep it identical to the Python `_CLASSIFY_PROMPT` constant you add in Step 5, the corpus `classify_prompt` row is what actually cross-checks them).
- [ ] **Step 2:** Run → FAIL. Implement `classify.rs` (mirror `prompt.rs` for the constant, `parse.rs` for fence-strip). Add `pub mod classify;`. Run → PASS.
- [ ] **Step 3:** Add shims `documents_classify_prompt() -> String` and `documents_parse_classify(text: &str) -> PyResult<String>` in `native/src/documents.rs`; register both in `lib.rs`. `cargo build -p goldenmatch-native`, grep `^error` clean.
- [ ] **Step 4:** Write failing `tests/documents/test_classify.py` for the Python `classify.py` helpers: `classify_prompt()` returns the constant; `parse_classify('{"doctype":"invoice","confidence":0.9}')` → `("invoice", 0.9)`; fenced input parses; unknown doctype raises `ValueError`; missing confidence raises. Run (pure) → FAIL.
- [ ] **Step 5:** Implement `documents/classify.py` with `classify_prompt() -> str` and `parse_classify(text) -> ClassifyResult` (dual-path `hasattr`-guarded on `documents_classify_prompt` / `documents_parse_classify`, else pure-Python mirror). `ClassifyResult` is added to `types.py` in Task 4 — for now define it inline in `classify.py` OR add the small dataclass here and import it in Task 4 (prefer: add `ClassifyResult` to `types.py` now, it's trivial). Add the 2 symbols to `_COMPONENT_SYMBOLS`. Run (pure) → PASS.
- [ ] **Step 6:** Add `classify_prompt` + `classify_parse` parity rows to the generator (clean, fenced, unknown-doctype error, missing-confidence error) and wire `"classify_prompt"`/`"classify_parse"` into `_KERNEL_SYMBOL`, `_run_pure`, `_run_native`. Regenerate corpus. Run parity `-k classify` → PASS.
- [ ] **Step 7:** Commit: `feat(documents): doctype classify kernel (prompt + parse + parity)`.

---

## Task 3: Structured-extract parse kernel

Adds `documents_parse_structured(text, template_json) -> {"header": {...}, "line_items": [...]}`, normalizing the VLM's `{"header": {...}, "line_items": [{...}, ...]}` response against a template. Reuses `normalize.rs` field-coercion discipline for BOTH the header (against `header_fields`) and each line item (against `line_item_fields`).

> **Signature is `(text, template_json)` — NOT `(text, doctype)`.** The kernel takes the FULL `DocTemplate` JSON (`{"doctype","header_fields","line_item_fields"}`, the same bytes `documents_template` emits) and parses it into the two field lists. This matches the spec and — critically — keeps native/pure parity for ANY template, including a custom `DocTemplate` a caller injects that isn't one of the four registry doctypes. (An earlier draft passed `doctype` and looked it up in the registry; that would make the native path `Err` on a custom template while the pure path succeeded — a parity break. Do NOT do that.)

**Files:**
- Create: `documents-core/src/extract_structured.rs`
- Modify: `documents-core/src/lib.rs`, `native/src/documents.rs`, `native/src/lib.rs`, `core/_native_loader.py`
- Modify: `documents/structured.py` (NEW — Python parse helper) OR fold into `vlm_backend`; prefer NEW `documents/structured.py`.
- Modify: `scripts/gen_documents_corpus.py`, `tests/parity/test_documents_parity.py`
- Test: `extract_structured.rs` `#[cfg(test)]`, `tests/documents/test_structured.py`

**Output shape** (each value str-coerced, missing→null, unknown keys dropped, schema-column order re-imposed — exactly `normalize_record`, applied twice):

```json
{"header": {"values": {<header col>: <str|null>}, "confidence": {<header col>: <0..1>}},
 "line_items": [{"values": {<item col>: ...}, "confidence": {...}}, ...]}
```

Edge cases the tests MUST cover: empty `line_items` (flat/receipt-style) → `"line_items": []`; a line item missing a field → null; extra keys dropped; `header` absent in the response → `Err` (malformed); a receipt template (empty `line_item_fields`) ignores any `line_items` in the response and returns `[]`.

- [ ] **Step 1:** Write failing Rust tests in `extract_structured.rs`: invoice template, response with header + 2 line items → correct normalized shape (assert via the `NormalizedRow`/`BTreeMap` values, NOT a raw-JSON column-order substring — the emitted JSON is alphabetical, order is re-imposed in Python); missing header field → null; extra key dropped; empty line_items → `[]`; receipt template + response with stray line_items → `[]`; malformed (no `header`) → `Err`.
- [ ] **Step 2:** Run → FAIL. Implement `extract_structured.rs`: `parse_structured(text: &str, template: &DocTemplate) -> Result<StructuredParsed, String>` — `serde_json::from_str`, pull the `header` object (`Err` if absent). The VLM response header may be either `{"values":{...},"confidence":{...}}` or a bare `{field: value}` map — support the same shape the flat extractor's records use (`{"values":..,"confidence":..}`); if `confidence` is absent, default each to 0.0. Call `normalize::normalize_record(header_values_json, header_conf_json, &header_schema)`; pull `line_items` array (default `[]`) → normalize each against `line_item_fields`; if `line_item_fields` is empty, force `[]`. Build a `TargetSchema` from the template's field vecs. Add `add pub mod extract_structured;`. Run → PASS.
  > `documents_parse_structured(text, template_json)` parses `template_json` into a `DocTemplate`. Add `pub fn template_from_json(s: &str) -> Result<DocTemplate, String>` to `templates.rs` (serde `Deserialize` on `DocTemplate` — add `Deserialize` to its derives). This is the ONLY new parse path; it deserializes the exact bytes `documents_template`/`template_json` emit, so round-trip is guaranteed.

- [ ] **Step 3:** Add shim `documents_parse_structured(text: &str, template_json: &str) -> PyResult<String>` (parse template via `templates::template_from_json`, then `parse_structured`); register in `lib.rs`; `cargo build`, grep clean. Add symbol to `_COMPONENT_SYMBOLS`.
- [ ] **Step 4:** Write failing `tests/documents/test_structured.py`: `parse_structured(text, template)` (Python) returns a `StructuredResult(header: ExtractedRow, line_items: list[ExtractedRow], error=None)` for a good invoice response; empty line items → `line_items == []`; malformed → `StructuredResult(error=...)` (Python wraps the `ValueError`, does NOT raise, since the flow records it in `report.errors`). Run (pure) → FAIL.
- [ ] **Step 5:** Implement `documents/structured.py::parse_structured(text, template: DocTemplate) -> StructuredResult`. Dual-path: native path calls `nm.documents_parse_structured(text, json.dumps(_template_to_dict(template)))` (`hasattr`-guarded on `documents_parse_structured`); pure-Python fallback reuses `ExtractedRow.from_partial` for the header (against `template.header`) + each item (against `template.line_items`), forcing `[]` items when `template.line_items.fields` is empty. Both legs must produce the same `StructuredResult`. `StructuredResult` dataclass added to `types.py`. `_doc_id`/source are stamped later by the flow (here source_file/page are placeholders). A malformed response → `StructuredResult(header=None, line_items=[], error=<msg>)` (Python wraps, does NOT raise). Run (pure) → PASS.
- [ ] **Step 6:** Add `parse_structured` parity rows (good invoice, empty items, receipt-ignores-items, malformed error) keyed by `{"text":..., "template": <DocTemplate dict>}` (NOT doctype — the kernel takes template JSON). Wire `"parse_structured"` into the harness: `_run_pure` builds a `DocTemplate` from `input_["template"]` and calls the pure `parse_structured`, reshaping to ordered `[col,val]` pairs (like the `normalize` kernel — header pairs + a list of per-item pair-lists); `_run_native` calls `nm.documents_parse_structured(input_["text"], json.dumps(input_["template"]))` and reshapes identically. Compare ordered pairs, not raw dicts. Regenerate corpus. Run parity `-k structured` → PASS.
- [ ] **Step 7:** Commit: `feat(documents): structured-extract parse kernel (header + line items + parity)`.

---

## Task 4: Structured collaborators (Classifier + TemplateExtractor + FallbackExtractor seams)

Adds the three new Protocols + their VLM and Fake implementations + `resolve_structured`. All VLM calls behind the injectable `Transport`. No frame assembly yet.

**Files:**
- Modify: `documents/types.py` (confirm `ClassifyResult`, `StructuredResult` present from Tasks 2/3; add `DocResult = ExtractResult | StructuredResult` type alias)
- Modify: `documents/extractor.py` (add `Classifier`, `TemplateExtractor`, `FallbackExtractor` Protocols + `FakeClassifier`, `FakeTemplateExtractor`, `FakeFallbackExtractor`)
- Create: `documents/vlm_classifier.py` (`VLMClassifier`), extend `documents/structured.py` (`VLMTemplateExtractor`), add `VLMFallbackExtractor` (in `vlm_classifier.py` or a new `documents/fallback.py`) — keep VLM classes near their parse helpers.
- Modify: `documents/config.py` (`resolve_structured`)
- Test: `tests/documents/test_structured_collaborators.py`

**Protocols** (per spec "Collaborator seams", + the fallback seam added in the plan review):

```python
@runtime_checkable
class Classifier(Protocol):
    def classify(self, pages: list[PageImage]) -> ClassifyResult: ...

@runtime_checkable
class TemplateExtractor(Protocol):
    def extract_structured(self, pages: list[PageImage], template: DocTemplate) -> StructuredResult: ...

@runtime_checkable
class FallbackExtractor(Protocol):
    # the "generic" path: suggest a schema from the doc, then extract against it (2 VLM calls)
    def suggest_and_extract(self, pages: list[PageImage]) -> ExtractResult: ...
```

- [ ] **Step 1:** Write failing tests: `FakeClassifier([ClassifyResult("invoice",0.9)]).classify(pages)` returns it; `FakeTemplateExtractor([StructuredResult(...)]).extract_structured(pages, t)` returns it; `FakeFallbackExtractor([ExtractResult(...)]).suggest_and_extract(pages)` returns it; `VLMClassifier(transport=scripted).classify(pages)` builds a payload with `classify_prompt()` + `image_blocks(pages)`, `temperature=0`, `max_tokens` small (e.g. 200), and parses the scripted response via `parse_classify`; `VLMTemplateExtractor(transport=scripted).extract_structured(pages, invoice_template)` builds a payload embedding the template's header+line-item fields and parses via `parse_structured`; `VLMFallbackExtractor(transport=scripted, model=...).suggest_and_extract(pages)` issues TWO scripted calls (suggest then extract) reusing `suggest_schema` + `VLMExtractor`; transport failure → `VLMClassifier` retries then raises `ValueError`, `VLMTemplateExtractor` returns `StructuredResult(error=...)` (batch-continues contract). Use a `scripted_transport(responses)` helper (mirror `test_vlm_backend.py`). Run (pure) → FAIL.
- [ ] **Step 2:** Run → FAIL (classes absent).
- [ ] **Step 3:** Implement the Protocols + Fakes in `extractor.py`; `VLMClassifier` in `vlm_classifier.py`; `VLMTemplateExtractor` in `structured.py` (build a structured instruction: reuse `extract_instruction`-style text but ask for `{"header": {...}, "line_items": [...]}` against header+item fields — a Python-side f-string is fine for v1 since its OUTPUT isn't parity-critical, only the PARSE is); `VLMFallbackExtractor` composing `suggest_schema(pages, transport, model)` + `VLMExtractor(transport, model).extract(pages, schema)`. `resolve_structured(backend, model) -> tuple[Classifier, TemplateExtractor, FallbackExtractor]` returns `(VLMClassifier(...), VLMTemplateExtractor(...), VLMFallbackExtractor(...))` all sharing one resolved `urllib_transport`; `backend != "vlm"` → `ValueError`.
- [ ] **Step 4:** Run (pure) → PASS.
- [ ] **Step 5:** Commit: `feat(documents): classifier + template-extractor + fallback collaborator seams`.

> Note: the structured-extract *instruction* string is Python-only and NOT in the parity corpus (only the PARSE is parity-locked). If you later want it byte-parity across TS, move it to core in a follow-up — out of scope here (spec: only the 5 named kernels are core).

---

## Task 5: Two-frame assemble + report + `ingest_documents` contract

The convergence point. Extends `assemble.py` to emit a header frame + optional line-item frame from a per-doc `DocResult` union, extends `IngestReport`, and rewires `ingest_documents` to own the per-doc dispatch (without the classifier flow yet — that's Task 6; here the dispatch takes an explicit template or flat schema).

**Files:**
- Modify: `documents/assemble.py` (new `assemble_structured` or extend `assemble`; add `_doc_id` derivation, `DOC_SIDECARS`)
- Modify: `documents/types.py` (`IngestReport` gains `line_items`, `doctypes`, `classify_confidence`, `vlm_calls`)
- Modify: `documents/__init__.py` (`ingest_documents` params + `_ingest_one` dispatch + path de-dup + exports)
- Modify: `documents/__init__.py` docstring + `documents/README.md` (exclude-list → `DOC_SIDECARS`)
- Test: `tests/documents/test_assemble_structured.py`, extend `tests/documents/test_ingest.py`

**`_doc_id`** = `record_fingerprint({"path": normalized_source_file})` (import from `goldenmatch.core._hashing`; use the path ONLY — see spec, NOT header values). Computed in `_ingest_one` (which has the path) and carried on the outcome so BOTH the frames and the flow-level report fields key on the same id. `ingest_documents` de-dups `paths` (last wins) before the loop. (Note: `record_fingerprint` drops `__`-double-underscore keys only; the single non-prefixed `"path"` key is used and survives — deterministic and byte-identical native vs pure since hashing is off under `auto`.)

**`DOC_SIDECARS`** = `["_source_file", "_source_page", "_extract_confidence", "_doc_id", "_doctype"]` (exported from `documents/__init__.py`).

**Per-doc outcome carrier (resolves the report-threading gap).** `_ingest_one` returns a `_DocOutcome` dataclass, NOT a bare tuple — a bare `(doctype, DocResult)` cannot carry `classify_confidence` or `vlm_calls`, which are flow facts assemble can't compute:

```python
@dataclass
class _DocOutcome:
    doc_id: str            # record_fingerprint of the path
    source_file: str
    doctype: str           # "invoice"|"po"|"statement"|"receipt"|"generic"
    confidence: float      # classifier confidence; 1.0 for pinned template / flat schema
    vlm_calls: int         # calls this doc cost (1 flat/pinned, 2 auto-hit, 3 auto-fallback)
    result: DocResult      # ExtractResult (flat) | StructuredResult
```

`assemble_structured(outcomes: list[_DocOutcome], *, drop_empty)` builds the two frames + `report.doctypes` + `report.errors` + `n_rows` (all derivable from the outcomes). `ingest_documents` then fills the FLOW fields from the same outcomes: `report.vlm_calls = sum(o.vlm_calls)`, `report.classify_confidence = {o.doc_id: o.confidence for o in outcomes}`.

- [ ] **Step 1:** Write failing `test_assemble_structured.py` (build `_DocOutcome`s directly, no flow):
  - single invoice outcome (`StructuredResult` header + 2 items) → header frame 1 row with `_doc_id`/`_doctype="invoice"`/sidecars; line-item frame 2 rows with matching `_doc_id` FK + `_line_no` 0,1; `report.doctypes[doc_id]=="invoice"`.
  - receipt outcome (no items) alone → `report.line_items is None`.
  - mixed batch (invoice + receipt outcomes) → header frame outer-union (receipt-only cols null on the invoice row and vice versa), `_doctype` correct per row; line-item frame only the invoice's 2 rows.
  - two outcomes with the SAME `doc_id` (same file dedup happens upstream, but assert assemble is last-wins-safe) → ONE header row.
  - flat `ExtractResult` outcome (`doctype="generic"`) in the mix → header frame row, no line items.
  - `StructuredResult(error=...)` outcome → `report.errors` gets `(source_file, msg)`, no rows.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `assemble_structured`. Header frame: accumulate header dicts (each = the header `ExtractedRow.values` + `_doc_id`, `_doctype`, `_source_file`, `_source_page`, `_extract_confidence`); build via `pl.DataFrame(records)` — but records have HETEROGENEOUS keys across doctypes, so a plain `pl.DataFrame(list_of_dicts)` raises on ragged keys. Use `pl.concat([pl.DataFrame([rec]) for rec in records], how="diagonal")` (diagonal fills missing cols with null — the deterministic union), OR pre-compute the ordered column union and pad each dict with `None` before one `pl.DataFrame(records)`. **Prefer the explicit pad-then-DataFrame approach** (avoids per-row DataFrame overhead and a `concat` dtype surprise): compute `header_cols = ordered union of header field names by first-appearance across outcomes`, pad every record with missing cols = `None`, `pl.DataFrame(records).select([pl.col(c).cast(pl.Utf8) for c in header_cols] + DOC_SIDECARS-with-casts)`. Line-item frame likewise (cols = union of line-item fields by first-appearance + `_doc_id`,`_line_no`,`_source_file`,`_source_page`,`_extract_confidence`); `None` if zero items total. Extend `IngestReport` (new fields default `line_items=None`, `doctypes=field(default_factory=dict)`, `classify_confidence=field(default_factory=dict)`, `vlm_calls=0`). Keep the OLD flat `assemble` untouched (still used by the pure Phase-2 path / other callers).
  > Column-order rule: header (and line-item) columns ordered by first-appearance across the batch (deterministic given input order), then the sidecars. Assert exact `df.columns` in a test so a polars-version reshuffle can't slip through.
- [ ] **Step 4:** Rewire `ingest_documents` (this task: explicit `schema=` OR `template=` only; `auto_classify` path raises `NotImplementedError` until Task 6). `_ingest_one(path, *, schema, template, template_extractor, ...)` loads pages, computes `doc_id`, runs flat `VLMExtractor` (schema, doctype `"generic"`, `vlm_calls=1`, `confidence=1.0`) or `VLMTemplateExtractor`/injected `template_extractor` (template, `vlm_calls=1`, `confidence=1.0`) → wraps in `_DocOutcome`. De-dup `paths` (last wins). Aggregate → `assemble_structured`, then fill `vlm_calls`/`classify_confidence` from the outcomes. Update docstring to `DOC_SIDECARS`. Export `DocTemplate`, `list_templates`, `DOC_SIDECARS`. Extend `test_ingest.py` with a `FakeTemplateExtractor`-driven e2e for `template="invoice"` asserting `report.vlm_calls==1`, `report.classify_confidence[id]==1.0`.
- [ ] **Step 5:** Run all `tests/documents/` (pure) → PASS. Commit: `feat(documents): two-frame structured assemble + ingest contract`.

---

## Task 6: Classifier flow (auto-classify default + routing + fallback)

Wires the classifier into `ingest_documents` so `auto_classify=True` (default) works end-to-end: classify → route on confidence → structured extract or generic fallback. Fills `report.doctypes`, `report.classify_confidence`, `report.vlm_calls`.

**Files:**
- Modify: `documents/__init__.py` (`_ingest_one` gains the classify→route branch; `resolve_structured` used lazily; new `classifier=`/`template_extractor=`/`fallback_extractor=` injection params)
- Test: extend `tests/documents/test_ingest.py`, `tests/documents/test_e2e.py`

**Generic-fallback injection seam (resolves offline-testability).** The real generic fallback is `suggest_schema(pages, transport=...)` then `VLMExtractor(transport).extract(...)` — 2 VLM calls, both needing a transport that a fake-driven test can't reach through the `extractor=`/`classifier=` seams (and injecting `extractor=` short-circuits the whole auto path). So add a dedicated seam: `fallback_extractor: Extractor | None`. When the auto path decides "generic," it calls `_generic_extract(pages)`:
- if `fallback_extractor` is injected → `fallback_extractor.extract(pages, <a placeholder empty schema is wrong>)` — instead, `fallback_extractor` is a `Callable[[list[PageImage]], ExtractResult]` OR a small `FallbackExtractor` protocol `suggest_and_extract(pages) -> ExtractResult`. **Use a `FallbackExtractor` protocol** with `suggest_and_extract(pages) -> ExtractResult`; the real `VLMFallbackExtractor(transport, model)` does suggest+extract (2 calls); `FakeFallbackExtractor([ExtractResult(...)])` is scripted for tests.
- if not injected → resolve the real `VLMFallbackExtractor` from `resolve_structured` (which now also returns it / its transport).

This keeps the low-confidence path fully offline-testable via `fallback_extractor=FakeFallbackExtractor([...])`, no live `suggest_schema`.

**Per-doc flow (`_ingest_one`)** — precedence `extractor`(flat) > `schema`(flat) > `template`(pinned) > `auto_classify`:
1. flat `extractor`/`schema` → `VLMExtractor.extract` → flat `_DocOutcome`, doctype `"generic"`, `vlm_calls=1`, `confidence=1.0`.
2. pinned `template` → `template_extractor.extract_structured` → `vlm_calls=1`, `confidence=1.0`.
3. auto: `classifier.classify(pages)` → 1 call; on classify EXCEPTION → generic fallback, that doc's `vlm_calls = 1 (failed classify) + 2 (fallback) = 3`... — simpler and honest: count only calls actually made. On classify success with `confidence >= classify_threshold` and `doctype in list_templates()` → `template_extractor.extract_structured(pages, get_template(doctype))`, `vlm_calls=2`, `confidence=<classifier value>`. Else (low-confidence, unknown doctype, or classify raised) → `_generic_extract(pages)` → flat `_DocOutcome`, doctype `"generic"`; `vlm_calls = (1 if classify succeeded else 0 attempted-but-failed→still 1 network call) + 2`. **Rule: `vlm_calls` = number of transport calls that were actually issued** (classify counts even if it raised, since the call went out). Document the exact per-branch count in a comment.

> Cost note: the generic-fallback branch is **3** VLM calls (classify + suggest + extract), exceeding the spec's headline "2/doc" for the happy structured path. That's expected — `report.vlm_calls` makes it visible. The happy path (classify-hit) is 2; pinned/flat is 1.

- [ ] **Step 1:** Write failing tests (all offline; inject `classifier=FakeClassifier`, `template_extractor=FakeTemplateExtractor`, `fallback_extractor=FakeFallbackExtractor`):
  - auto-classify high-confidence invoice → invoice template used, `vlm_calls == 2`, `doctypes[id]=="invoice"`, `classify_confidence[id]==0.9`.
  - low-confidence (`0.3 < threshold=0.6`) → `_generic_extract` path, `doctype=="generic"`, `vlm_calls == 3`, `classify_confidence[id]==0.3`.
  - `template="invoice"` override → classifier NOT called (`FakeClassifier` call count 0), `vlm_calls == 1`.
  - classify raises → generic fallback, batch continues (`vlm_calls == 3`).
  - one doc's structured extract returns `StructuredResult(error=...)` → `report.errors`, batch continues, OTHER docs still produce rows.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the branch in `_ingest_one` + `_generic_extract`; resolve `(classifier, template_extractor, fallback_extractor)` once via `resolve_structured` (extended to return the 3rd) only if the auto path is reached and they weren't injected. Thread `classify_threshold`. Fill `confidence` on each outcome (classifier value, or 1.0 for pinned/flat).
- [ ] **Step 4:** Run all `tests/documents/` (pure) → PASS. Add a `test_e2e.py` case: `ingest_documents([invoice, receipt], return_report=True)` with fakes → `df` (headers) feeds `dedupe_df(df, exclude_columns=DOC_SIDECARS)` clean; `report.line_items` has the invoice items; `report.doctypes`/`classify_confidence`/`vlm_calls` populated.
- [ ] **Step 5:** Commit: `feat(documents): auto-classify flow (routing + generic fallback + cost report)`.

---

## Task 7: TS/wasm parity leg

Bind the 5 new kernels into `documents-wasm` and replay the extended corpus in TS, exactly mirroring how documents-core's TS leg shipped (see the `documents-wasm` → `src/core/documentsWasm.ts` → `tests/parity/documents-core.parity.test.ts` chain and `scripts/build_documents_wasm.mjs`).

**Files:**
- Modify: `packages/rust/extensions/documents-wasm/src/lib.rs` (5 `#[wasm_bindgen]` exports)
- Modify: `packages/typescript/goldenmatch/src/core/documentsWasm.ts` (5 camelCase wrappers)
- Modify: `packages/typescript/goldenmatch/tests/parity/documents-core.parity.test.ts` (replay new kernels; reshape ordered `[col,val]` pairs for structured/normalize like the Python harness)
- Regenerate + commit the `_wasm` bindings/bytes (CI rebuilds transiently but never commits back — a branch missing them poisons main's ungated `turbo build/typecheck`).

- [ ] **Step 1:** Add the 5 `#[wasm_bindgen]` exports to `documents-wasm/src/lib.rs` mirroring the existing ones (`documents_template`, `documents_template_list`, `documents_classify_prompt`, `documents_parse_classify`, `documents_parse_structured`). Build: `node packages/typescript/goldenmatch/scripts/build_documents_wasm.mjs` (wasm-pack on PATH, wasm32 std in `stable`; see memory `project_document_image_ingest`). Verify the corpus was copied by the build.
- [ ] **Step 2:** Add the 5 camelCase wrappers to `documentsWasm.ts` (`documentsTemplate`, `documentsTemplateList`, `documentsClassifyPrompt`, `documentsParseClassify`, `documentsParseStructured`). Fail-loud, NO skip-gate (fingerprint-wasm precedent).
- [ ] **Step 3:** Extend the TS parity test to replay the new corpus kernels. For `parse_structured` and `template`, reshape to ordered `[col,val]` pairs where the corpus expects them (see the `normalize` reshape in the existing TS test + the Python `_run_native`). Parse-vs-JSON: `classify_prompt`/`parse_classify` and `parse_structured` return JSON (parse it); `template`/`template_list` return JSON.
- [ ] **Step 4:** Run TS build + parity locally IF the box has headroom, else rely on CI (box OOMs vitest/build — memory `feedback_box_memory_oom_ts`; prefer CI as the green gate). Commit the regenerated `_wasm` files.
- [ ] **Step 5:** Commit: `feat(documents): TS/wasm parity leg for template/classify/structured kernels`.

---

## Final verification (before finishing-a-development-branch)

- [ ] Full documents pytest suite green on the **pure** path (`GOLDENMATCH_NATIVE=0`).
- [ ] Parity `test_documents_parity.py` green (pure leg strict; native leg SKIPs locally, runs in CI).
- [ ] No regression in the pre-existing flat/Phase-2 tests (`test_ingest.py`, `test_assemble.py`, `test_suggest.py`, `test_vlm_backend.py`, `test_e2e.py`).
- [ ] Rust: `cargo test -p goldenmatch-documents-core` green; `cargo build -p goldenmatch-native` clean (grep `^error`).
- [ ] `_wasm` bindings/bytes committed.
- [ ] Push + PR; arm `gh pr merge <n> --auto --squash` (NO `--delete-branch`); CI is the gate for the native + TS legs. Do NOT poll from the laptop (memory `feedback_dont_poll_ci_arm_automerge`).

## Out of scope (flagged fast-follow — do NOT do here)

MCP/CLI/REST/A2A surfaces + docs-site guide learning the `template`/auto-classify option and line-item output. Tracked per the rollout-docs-sweep lesson; separate PR after this lands.
