from goldenmatch.documents.structured import parse_structured
from goldenmatch.documents.templates import get_template
from goldenmatch.documents.types import ExtractedRow, StructuredResult

INVOICE = get_template("invoice")
RECEIPT = get_template("receipt")


def test_header_plus_two_line_items():
    text = (
        '{"header": {"values": {"invoice_number": "INV-1", "total_amount": 42,'
        ' "currency": "USD"}, "confidence": {"invoice_number": 0.9}},'
        ' "line_items": ['
        '{"values": {"description": "Widget", "quantity": 2, "unit_price": 10,'
        ' "line_total": 20}, "confidence": {"description": 0.8}},'
        '{"values": {"description": "Gadget", "quantity": 1}}]}'
    )
    r = parse_structured(text, INVOICE)
    assert isinstance(r, StructuredResult)
    assert r.error is None
    assert isinstance(r.header, ExtractedRow)
    # header: str-coerced, missing -> null, column order re-imposed
    assert r.header.values["invoice_number"] == "INV-1"
    assert r.header.values["total_amount"] == "42"
    assert r.header.values["vendor_name"] is None
    assert list(r.header.values.keys()) == INVOICE.header.column_names()
    assert r.header.confidence["invoice_number"] == 0.9
    assert r.header.confidence["currency"] == 0.0
    # two line items
    assert len(r.line_items) == 2
    assert r.line_items[0].values["description"] == "Widget"
    assert r.line_items[0].values["quantity"] == "2"
    assert r.line_items[0].confidence["description"] == 0.8
    assert r.line_items[1].values["description"] == "Gadget"
    assert r.line_items[1].values["unit_price"] is None
    assert list(r.line_items[1].values.keys()) == INVOICE.line_items.column_names()


def test_missing_header_field_is_null():
    r = parse_structured('{"header": {"values": {"invoice_number": "INV-9"}}}', INVOICE)
    assert r.header.values["invoice_number"] == "INV-9"
    assert r.header.values["buyer_name"] is None
    assert r.line_items == []


def test_extra_key_dropped():
    r = parse_structured('{"header": {"invoice_number": "INV-3", "junk": "x"}}', INVOICE)
    assert r.header.values["invoice_number"] == "INV-3"
    assert "junk" not in r.header.values


def test_bare_header_shape_supported():
    r = parse_structured('{"header": {"invoice_number": "INV-B", "currency": "EUR"}}', INVOICE)
    assert r.header.values["invoice_number"] == "INV-B"
    assert r.header.values["currency"] == "EUR"
    assert r.header.confidence["invoice_number"] == 0.0


def test_empty_line_items():
    r = parse_structured('{"header": {"values": {"invoice_number": "INV-4"}}, "line_items": []}', INVOICE)
    assert r.line_items == []


def test_receipt_ignores_stray_line_items():
    text = '{"header": {"values": {"merchant_name": "Shop"}}, "line_items": [{"values": {"x": 1}}]}'
    r = parse_structured(text, RECEIPT)
    assert r.header.values["merchant_name"] == "Shop"
    assert r.line_items == []  # forced [] -- receipt has no line_item_fields


def test_malformed_no_header_wraps_error():
    r = parse_structured('{"line_items": []}', INVOICE)
    assert r.header is None
    assert r.line_items == []
    assert r.error is not None


def test_invalid_json_wraps_error():
    r = parse_structured("not json", INVOICE)
    assert r.header is None
    assert r.error is not None
