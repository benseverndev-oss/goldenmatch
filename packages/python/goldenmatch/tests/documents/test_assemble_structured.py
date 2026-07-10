"""Two-frame structured assemble: header frame (entities) + linked line-item
frame (children by `_doc_id`). Build `_DocOutcome`s directly -- no flow."""
import polars as pl
from goldenmatch.documents.assemble import DOC_SIDECARS, assemble_structured
from goldenmatch.documents.templates import get_template
from goldenmatch.documents.types import (
    ExtractedRow,
    ExtractResult,
    StructuredResult,
    _DocOutcome,
)

INV = get_template("invoice")
REC = get_template("receipt")


def _erow(schema, vals, conf=None, pg=0):
    return ExtractedRow.from_partial(vals, conf or {}, schema, source_file="", source_page=pg)


def _invoice_outcome(doc_id="d1", src="inv.pdf", confidence=0.9):
    header = _erow(INV.header, {"invoice_number": "INV-1", "total_amount": "100"},
                   {"invoice_number": 0.9, "total_amount": 0.8})
    items = [
        _erow(INV.line_items, {"description": "Widget", "quantity": "2"},
              {"description": 0.7, "quantity": 0.7}),
        _erow(INV.line_items, {"description": "Gadget", "quantity": "1"},
              {"description": 0.6, "quantity": 0.6}),
    ]
    res = StructuredResult(header=header, line_items=items, error=None)
    return _DocOutcome(doc_id=doc_id, source_file=src, doctype="invoice",
                       confidence=confidence, vlm_calls=2, result=res)


def _receipt_outcome(doc_id="r1", src="rec.pdf"):
    header = _erow(REC.header, {"merchant_name": "Cafe", "total_amount": "9.50"},
                   {"merchant_name": 0.95, "total_amount": 0.9})
    res = StructuredResult(header=header, line_items=[], error=None)
    return _DocOutcome(doc_id=doc_id, source_file=src, doctype="receipt",
                       confidence=0.88, vlm_calls=2, result=res)


def _flat_outcome(doc_id="g1", src="misc.pdf"):
    schema = INV.header  # any schema; treat as generic flat
    row = ExtractedRow.from_partial({"invoice_number": "X", "vendor_name": "Acme"},
                                    {"invoice_number": 0.5}, schema,
                                    source_file="", source_page=0)
    res = ExtractResult(rows=[row], error=None)
    return _DocOutcome(doc_id=doc_id, source_file=src, doctype="generic",
                       confidence=1.0, vlm_calls=1, result=res)


def test_single_invoice_header_and_line_items():
    df, report = assemble_structured([_invoice_outcome()], drop_empty=True)
    assert df.height == 1
    doc_id = df["_doc_id"][0]
    assert df["_doctype"][0] == "invoice"
    assert df["invoice_number"][0] == "INV-1"
    assert df["_source_file"][0] == "inv.pdf"
    assert report.doctypes[doc_id] == "invoice"
    assert report.n_rows == 1
    li = report.line_items
    assert li is not None and li.height == 2
    assert li["_doc_id"].to_list() == [doc_id, doc_id]
    assert li["_line_no"].to_list() == [0, 1]
    assert li["description"].to_list() == ["Widget", "Gadget"]


def test_receipt_alone_has_no_line_item_frame():
    df, report = assemble_structured([_receipt_outcome()], drop_empty=True)
    assert df.height == 1
    assert df["_doctype"][0] == "receipt"
    assert report.line_items is None


def test_mixed_invoice_and_receipt_outer_union():
    df, report = assemble_structured([_invoice_outcome(), _receipt_outcome()], drop_empty=True)
    assert df.height == 2
    inv_row = df.filter(pl.col("_doctype") == "invoice")
    rec_row = df.filter(pl.col("_doctype") == "receipt")
    # receipt-only col null on the invoice row; invoice-only col null on receipt row
    assert inv_row["merchant_name"][0] is None
    assert rec_row["invoice_number"][0] is None
    assert rec_row["merchant_name"][0] == "Cafe"
    # line-item frame carries ONLY the invoice's 2 rows
    assert report.line_items is not None and report.line_items.height == 2


def test_same_doc_id_is_last_wins_one_header_row():
    a = _invoice_outcome(doc_id="dup", confidence=0.5)
    b = _invoice_outcome(doc_id="dup", confidence=0.99)
    df, report = assemble_structured([a, b], drop_empty=True)
    assert df.height == 1
    # last-wins: the surviving outcome is `b` (2 line items, one header)
    assert report.line_items is not None and report.line_items.height == 2


def test_flat_generic_outcome_contributes_header_no_items():
    df, report = assemble_structured([_flat_outcome()], drop_empty=True)
    assert df.height == 1
    assert df["_doctype"][0] == "generic"
    assert report.line_items is None


def test_error_outcome_recorded_no_rows():
    err = _DocOutcome(doc_id="e1", source_file="broken.pdf", doctype="invoice",
                      confidence=1.0, vlm_calls=1,
                      result=StructuredResult(header=None, line_items=[], error="bad json"))
    df, report = assemble_structured([err], drop_empty=True)
    assert df.height == 0
    assert report.errors == [("broken.pdf", "bad json")]


def test_empty_batch_yields_empty_header_frame():
    df, report = assemble_structured([], drop_empty=True)
    assert df.height == 0
    assert df.columns == DOC_SIDECARS
    assert report.line_items is None


def _items(*descs):
    return [_erow(INV.line_items, {"description": d, "quantity": "1"}) for d in descs]


def test_header_none_with_items_is_discarded_and_recorded():
    o = _DocOutcome(doc_id="dn", source_file="noheader.pdf", doctype="invoice",
                    confidence=0.9, vlm_calls=2,
                    result=StructuredResult(header=None, line_items=_items("W", "G"), error=None))
    df, report = assemble_structured([o], drop_empty=True)
    assert df.height == 0
    assert report.line_items is None                 # no orphaned children
    assert "dn" not in report.doctypes
    assert len(report.errors) == 1
    assert report.errors[0][0] == "noheader.pdf" and "2 line item" in report.errors[0][1]


def test_null_header_dropped_discards_its_items():
    header = _erow(INV.header, {})  # all-null header
    o = _DocOutcome(doc_id="db", source_file="null.pdf", doctype="invoice",
                    confidence=0.9, vlm_calls=2,
                    result=StructuredResult(header=header, line_items=_items("W", "G"), error=None))
    df, report = assemble_structured([o], drop_empty=True)
    assert df.height == 0
    assert report.line_items is None
    assert report.doctypes == {}
    assert len(report.errors) == 1 and "2 line item" in report.errors[0][1]


def test_null_header_kept_when_not_drop_empty_keeps_items():
    header = _erow(INV.header, {})
    o = _DocOutcome(doc_id="dc", source_file="null2.pdf", doctype="invoice",
                    confidence=0.9, vlm_calls=2,
                    result=StructuredResult(header=header, line_items=_items("W", "G"), error=None))
    df, report = assemble_structured([o], drop_empty=False)
    assert df.height == 1                             # all-null header row IS emitted
    assert report.line_items is not None and report.line_items.height == 2
    assert report.line_items["_doc_id"].to_list() == ["dc", "dc"]
    assert report.errors == []


def test_mixed_good_and_null_header_produces_no_orphans():
    good = _invoice_outcome(doc_id="A", src="a.pdf")
    header = _erow(INV.header, {})
    bad = _DocOutcome(doc_id="B", source_file="b.pdf", doctype="invoice",
                      confidence=0.9, vlm_calls=2,
                      result=StructuredResult(header=header, line_items=_items("X"), error=None))
    df, report = assemble_structured([good, bad], drop_empty=True)
    assert set(df["_doc_id"].to_list()) == {"A"}      # only A's header survives
    li = report.line_items
    assert li is not None and set(li["_doc_id"].to_list()) == {"A"}  # no B orphan
    assert any("discarded" in m for _, m in report.errors)


def test_report_key_sets_are_aligned():
    good = _invoice_outcome(doc_id="A")
    err = _DocOutcome(doc_id="E", source_file="e.pdf", doctype="invoice",
                      confidence=1.0, vlm_calls=1,
                      result=StructuredResult(header=None, line_items=[], error="boom"))
    _, report = assemble_structured([good, err], drop_empty=True)
    assert set(report.classify_confidence) == set(report.doctypes) == {"A"}


def test_warning_surfaced_even_when_no_header_row():
    # classify raised -> generic fallback that produced NOTHING (empty rows). The
    # warning must still land in report.errors even though no header row is emitted.
    o = _DocOutcome(doc_id="w1", source_file="wf.pdf", doctype="generic",
                    confidence=0.0, vlm_calls=3,
                    result=ExtractResult(rows=[], error=None),
                    warning="classify failed, used generic fallback: boom")
    df, report = assemble_structured([o], drop_empty=True)
    assert df.height == 0
    assert "w1" not in report.doctypes
    assert any(f == "wf.pdf" and "classify failed" in m for f, m in report.errors)


def test_warning_surfaced_alongside_result_error():
    o = _DocOutcome(doc_id="w2", source_file="both.pdf", doctype="invoice",
                    confidence=0.0, vlm_calls=3,
                    result=StructuredResult(header=None, line_items=[], error="parse blew up"),
                    warning="classify failed, used generic fallback: boom")
    _, report = assemble_structured([o], drop_empty=True)
    msgs = [m for f, m in report.errors if f == "both.pdf"]
    assert any("classify failed" in m for m in msgs)   # warning not dropped
    assert any("parse blew up" in m for m in msgs)      # result error also recorded


def test_exact_columns_for_mixed_batch():
    df, _ = assemble_structured([_invoice_outcome(), _receipt_outcome()], drop_empty=True)
    expected = [
        # invoice header (first appearance)
        "invoice_number", "invoice_date", "vendor_name", "vendor_address",
        "buyer_name", "buyer_address", "total_amount", "currency",
        # receipt header (new cols only; total_amount already seen)
        "merchant_name", "merchant_address", "purchase_date", "payment_method",
    ] + DOC_SIDECARS
    assert df.columns == expected
