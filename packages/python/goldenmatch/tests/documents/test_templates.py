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
    assert [f.name for f in t.line_items.fields] == ["description", "quantity", "unit_price", "line_total"]


def test_unknown_doctype_raises():
    import pytest
    with pytest.raises(ValueError):
        get_template("nope")
