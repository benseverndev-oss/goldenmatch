#!/usr/bin/env python3
"""Generate the documents parity corpus from the PURE-PYTHON kernels.

Writes `tests/parity/documents_corpus.jsonl`: one JSON object per line,
`{"kernel": ..., "input": ..., "expected": ...}`. `test_documents_parity.py`
replays every row against both the pure-Python reference (must always match,
by construction) and the native kernel (when importable), locking Rust ==
Python.

Forces GOLDENMATCH_NATIVE=0 before calling any kernel so the corpus is always
generated from the pure-Python reference, regardless of the machine's native
wheel state.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["GOLDENMATCH_NATIVE"] = "0"
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tests" / "parity" / "documents_corpus.jsonl"

sys.path.insert(0, str(REPO))

from goldenmatch.documents._openai import parse_message_text  # noqa: E402
from goldenmatch.documents.classify import _pure_parse, _pure_prompt  # noqa: E402
from goldenmatch.documents.schema_io import schema_from_dict, schema_to_dict  # noqa: E402
from goldenmatch.documents.structured import _pure_structured_parsed  # noqa: E402
from goldenmatch.documents.suggest import _PROMPT  # noqa: E402
from goldenmatch.documents.templates import (  # noqa: E402
    _ORDER,
    _pure_template,
    _template_to_dict,
)
from goldenmatch.documents.types import DocTemplate, ExtractedRow  # noqa: E402
from goldenmatch.documents.vlm_backend import _instruction  # noqa: E402

rows: list[dict] = []


def add(kernel: str, input_: object, expected: dict) -> None:
    rows.append({"kernel": kernel, "input": input_, "expected": expected})


# --- schema ------------------------------------------------------------
schema_cases = [
    {"fields": [{"name": "full_name"}]},
    {"fields": [{"name": "full_name"}, {"name": "email", "kind": "email", "hint": "work"}]},
    {"fields": [{"name": "a", "hint": ""}]},  # empty-string hint
    {"fields": [{"name": "phone", "kind": "phone"}, {"name": "dob", "kind": "date", "hint": "MM/DD/YYYY"}]},
]
for c in schema_cases:
    try:
        s = schema_from_dict(c)
        add("schema", c, {"ok": schema_to_dict(s)})
    except ValueError:
        add("schema", c, {"error": True})

schema_error_cases = [
    {"nope": 1},
    {"fields": []},
    {"fields": ["full_name"]},
    {"fields": [{"kind": "text"}]},
    {"fields": [{"name": 123}]},  # non-string name: rejected by native + pure
    "not a dict",
]
for c in schema_error_cases:
    add("schema", c, {"error": True})

# --- parse ---------------------------------------------------------------
parse_cases = [
    {"choices": [{"message": {"content": "hello"}}]},
    {"choices": [{"message": {"content": '```json\n{"a":1}\n```'}}]},
    {"choices": [{"message": {"content": "```abc"}}]},  # no-newline fence edge case
    {"choices": [{"message": {"content": '```json\n{"a":1}\n``` trailing text after fence'}}]},
    {"choices": [{"message": {"content": "  padded  "}}]},
]
for c in parse_cases:
    try:
        text = parse_message_text(c)
        add("parse", c, {"ok": text})
    except ValueError:
        add("parse", c, {"error": True})

parse_error_cases = [
    {"choices": []},
    {},
    {"choices": [{"message": {"content": 123}}]},
    {"choices": [123]},  # non-dict choice: rejected by native + pure
]
for c in parse_error_cases:
    add("parse", c, {"error": True})

# truncation: shared exact message across both legs -> assert substring
add(
    "parse",
    {"choices": [{"finish_reason": "length", "message": {"content": "x"}}]},
    {"error": True, "substring": "truncated"},
)

# --- prompt: extract_instruction -----------------------------------------
prompt_extract_cases = [
    {"fields": [{"name": "full_name"}, {"name": "email", "kind": "email", "hint": "work"}]},
    {"fields": [{"name": "a", "hint": ""}]},  # falsy empty-string hint
    {"fields": [{"name": "single"}]},
]
for c in prompt_extract_cases:
    s = schema_from_dict(c)
    add("prompt_extract", c, {"ok": _instruction(s)})

# --- prompt: suggest prompt (fixed constant, no input) --------------------
add("prompt_suggest", None, {"ok": _PROMPT})

# --- normalize -------------------------------------------------------------
norm_schema = {"fields": [
    {"name": "a"}, {"name": "n", "kind": "number"}, {"name": "b"}, {"name": "missing"},
]}
norm_cases = [
    {"values": {"a": "Ada", "n": 90210, "b": True, "junk": "x"}, "confidence": {"a": 0.9}},
    {"values": {"a": None, "n": 3.14159, "b": False}, "confidence": {}},
    {"values": {"a": "x", "n": 0.1, "b": "y", "missing": None}, "confidence": {"a": 1.0, "n": 0.5, "b": 0.0}},
    {"values": {"n": 1e20}, "confidence": {}},
    {"values": {"n": 0.30000000000000004}, "confidence": {}},
    {"values": {}, "confidence": {}},
]
for c in norm_cases:
    ts = schema_from_dict(norm_schema)
    row = ExtractedRow.from_partial(c["values"], c["confidence"], ts, source_file="", source_page=0)
    cols = ts.column_names()
    add(
        "normalize",
        {"values": c["values"], "confidence": c["confidence"], "schema": norm_schema},
        {"ok": {
            "values": [[col, row.values[col]] for col in cols],
            "confidence": [[col, row.confidence[col]] for col in cols],
        }},
    )

# --- template --------------------------------------------------------------
# expected `ok` = the same JSON dict the native shim (documents_template) emits:
# {"doctype", "header_fields":[{name,kind,hint},...], "line_item_fields":[...]}.
# Generated from _PURE (via get_template under GOLDENMATCH_NATIVE=0) so pure and
# corpus can't disagree at generation time. `_template_to_dict` is imported from
# templates.py (single serializer shared with structured.py + the parity harness).
for name in _ORDER:
    add("template", {"doctype": name}, {"ok": _template_to_dict(_pure_template(name))})
add("template", {"doctype": "nope"}, {"error": True, "substring": "unknown doctype"})

# template_list: cross-checks native template_list() (vec in templates.rs) vs the
# pure _ORDER, so a doctype added to one but not the other can't ship silently.
add("template_list", {}, {"ok": list(_ORDER)})

# --- classify: prompt (fixed constant, no input) --------------------------
add("classify_prompt", None, {"ok": _pure_prompt()})

# --- classify: parse -------------------------------------------------------
# ok = {"doctype", "confidence"} (the same dict the native shim's JSON decodes to).
classify_parse_ok = [
    '{"doctype":"invoice","confidence":0.9}',                 # clean
    '```json\n{"doctype":"receipt","confidence":0.5}\n```',   # fenced
    '{"doctype":"generic","confidence":0.2}',                 # generic escape hatch
    '{"doctype":"po","confidence":1.5}',                      # out-of-range -> clamp to 1.0
    '{"doctype":"po","confidence":-0.3}',                     # out-of-range -> clamp to 0.0
    '{"doctype":"po","confidence":1}',                        # integer coerces to 1.0
    '{"doctype":"po","confidence":0}',                        # direct 0 -> 0.0 (not via clamp)
]
for text in classify_parse_ok:
    r = _pure_parse(text)
    add("classify_parse", text, {"ok": {"doctype": r.doctype, "confidence": r.confidence}})

# Coercion + strictness boundaries the native leg (CI-only) must match. serde
# `as_f64()` returns None for null/string/bool -> Err; serde_json REJECTS the bare
# NaN token (Python json.loads accepts it) -> Err. The `input` here is the raw
# TEXT the kernel parses (a JSON string value in the jsonl), so a literal `NaN`
# token rides inside that string and is parsed at kernel time on both legs.
classify_parse_err = [
    '{"doctype":"nope","confidence":0.9}',      # unknown doctype
    '{"doctype":"invoice"}',                    # missing confidence
    '{"doctype":"po","confidence":null}',       # present-but-null -> not a number
    '{"doctype":"po","confidence":"0.9"}',      # string -> not a number
    '{"doctype":"po","confidence":true}',       # bool -> not a number
    '{"doctype": "po", "confidence": NaN}',     # non-finite: pure clamps unless guarded, serde rejects
]
for text in classify_parse_err:
    add("classify_parse", text, {"error": True})

# --- parse_structured ------------------------------------------------------
# Keyed by {"text":..., "template": <DocTemplate dict>} (NOT doctype -- the kernel
# takes the FULL template JSON). `ok` = header + per-item ordered [col,val] pairs
# (mirrors the `normalize` reshape); column order is re-imposed downstream so the
# raw alphabetical native map isn't asserted directly. Generated from the pure-only
# kernel so pure and corpus can't disagree at generation time.
def _structured_ok(text: str, template: DocTemplate) -> dict:
    hv, hc, items = _pure_structured_parsed(text, template)
    hcols = template.header.column_names()
    icols = template.line_items.column_names()
    return {
        "header": {
            "values": [[c, hv[c]] for c in hcols],
            "confidence": [[c, hc[c]] for c in hcols],
        },
        "line_items": [
            {
                "values": [[c, iv[c]] for c in icols],
                "confidence": [[c, ic[c]] for c in icols],
            }
            for iv, ic in items
        ],
    }


_invoice_tpl = _pure_template("invoice")
_receipt_tpl = _pure_template("receipt")
structured_ok_cases = [
    # header wrapped shape + 2 line items
    (
        '{"header": {"values": {"invoice_number": "INV-1", "total_amount": 42,'
        ' "currency": "USD"}, "confidence": {"invoice_number": 0.9}},'
        ' "line_items": [{"values": {"description": "Widget", "quantity": 2,'
        ' "unit_price": 10, "line_total": 20}, "confidence": {"description": 0.8}},'
        ' {"values": {"description": "Gadget", "quantity": 1}}]}',
        _invoice_tpl,
    ),
    # bare header shape + missing field -> null + extra key dropped, no items
    ('{"header": {"invoice_number": "INV-B", "currency": "EUR", "junk": "x"}}', _invoice_tpl),
    # explicitly empty line_items -> []
    ('{"header": {"values": {"invoice_number": "INV-4"}}, "line_items": []}', _invoice_tpl),
    # receipt template ignores stray line_items -> []
    ('{"header": {"values": {"merchant_name": "Shop"}}, "line_items": [{"values": {"x": 1}}]}', _receipt_tpl),
    # --- coercion edges: pin native (CI-only) == pure against the Rust reference ---
    # confidence null / string / bool on header fields -> 0.0 (as_f64().unwrap_or(0.0))
    (
        '{"header": {"values": {"invoice_number": "X"},'
        ' "confidence": {"invoice_number": null, "total_amount": "0.9", "currency": true}}}',
        _invoice_tpl,
    ),
    # container VALUES -> serde compact JSON (NOT Python repr)
    ('{"header": {"values": {"invoice_number": [1, 2, 3], "vendor_name": {"a": 1}}}}', _invoice_tpl),
    # header `values` present-but-non-object -> treated as bare map (all fields null)
    ('{"header": {"values": 5}}', _invoice_tpl),
    # header null (key present) -> all-null header row, no error
    ('{"header": null}', _invoice_tpl),
    # bare-scalar line item -> one all-null item row
    ('{"header": {"values": {"invoice_number": "X"}}, "line_items": [5]}', _invoice_tpl),
]
for text, tpl in structured_ok_cases:
    add(
        "parse_structured",
        {"text": text, "template": _template_to_dict(tpl)},
        {"ok": _structured_ok(text, tpl)},
    )

# malformed: missing header / invalid JSON -> Err (both legs)
structured_err_cases = [
    ('{"line_items": []}', _invoice_tpl),      # no header
    ("not json", _invoice_tpl),                # invalid JSON
    ("[1, 2, 3]", _invoice_tpl),               # JSON but not an object
]
for text, tpl in structured_err_cases:
    add(
        "parse_structured",
        {"text": text, "template": _template_to_dict(tpl)},
        {"error": True},
    )

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, sort_keys=True) + "\n")

print(f"wrote {len(rows)} rows to {OUT}")
