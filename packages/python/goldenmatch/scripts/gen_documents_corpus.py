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
from goldenmatch.documents.suggest import _PROMPT  # noqa: E402
from goldenmatch.documents.templates import _ORDER, _pure_template  # noqa: E402
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
# corpus can't disagree at generation time.
def _template_to_dict(t: DocTemplate) -> dict:
    def fields(schema):
        return [{"name": f.name, "kind": f.kind, "hint": f.hint} for f in schema.fields]
    return {"doctype": t.doctype, "header_fields": fields(t.header),
            "line_item_fields": fields(t.line_items)}


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
]
for text in classify_parse_ok:
    r = _pure_parse(text)
    add("classify_parse", text, {"ok": {"doctype": r.doctype, "confidence": r.confidence}})

classify_parse_err = [
    '{"doctype":"nope","confidence":0.9}',  # unknown doctype
    '{"doctype":"invoice"}',                # missing confidence
]
for text in classify_parse_err:
    add("classify_parse", text, {"error": True})

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, sort_keys=True) + "\n")

print(f"wrote {len(rows)} rows to {OUT}")
