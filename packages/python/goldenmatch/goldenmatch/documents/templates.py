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
    def sch(items):
        return TargetSchema([Field(name=i["name"], kind=i["kind"], hint=i.get("hint"))
                             for i in items])
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
