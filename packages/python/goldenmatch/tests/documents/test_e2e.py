from goldenmatch import dedupe_df
from goldenmatch.documents import DOC_SIDECARS, ingest_documents
from goldenmatch.documents.extractor import (
    FakeClassifier,
    FakeExtractor,
    FakeFallbackExtractor,
    FakeTemplateExtractor,
)
from goldenmatch.documents.templates import get_template
from goldenmatch.documents.types import (
    ClassifyResult,
    ExtractedRow,
    ExtractResult,
    Field,
    StructuredResult,
    TargetSchema,
)
from PIL import Image

SCHEMA = TargetSchema([Field("full_name"), Field("email"), Field("city")])


def _img(p):
    Image.new("RGB", (60, 40), "white").save(p)


def _r(vals):
    return ExtractedRow.from_partial(vals, {}, SCHEMA, source_file="", source_page=0)


def test_extracted_frame_feeds_dedupe_df_and_finds_the_dupe(tmp_path):
    files = []
    # three docs; #1 and #3 are the same person with a typo -> should cluster
    scripted = [
        ExtractResult(rows=[_r({"full_name": "Ada Lovelace", "email": "ada@x.io", "city": "London"})]),
        ExtractResult(rows=[_r({"full_name": "Grace Hopper", "email": "grace@x.io", "city": "NYC"})]),
        ExtractResult(rows=[_r({"full_name": "Ada Lovelace", "email": "ada@x.io", "city": "Londonn"})]),
    ]
    for i in range(3):
        p = tmp_path / f"doc{i}.png"; _img(p); files.append(p)

    df = ingest_documents(files, SCHEMA, extractor=FakeExtractor(scripted))
    assert df.height == 3

    result = dedupe_df(
        df,
        exact=["email"],
        exclude_columns=DOC_SIDECARS,
        confidence_required=False,
        allow_red_config=True,
    )
    # `DedupeResult.total_clusters` (goldenmatch/_api.py ~line 217, backed by
    # `stats["total_clusters"]` ~line 1256) counts only clusters with size > 1 --
    # i.e. actual duplicate groups, not every distinct entity. With exact-on-email,
    # the two `ada@x.io` rows collapse into exactly one duplicate cluster of size 2;
    # Grace has no duplicate and is not counted here (she shows up in `unique` instead).
    assert result.total_clusters == 1
    dupe_clusters = [c for c in result.clusters.values() if c.get("size", 0) > 1]
    assert len(dupe_clusters) == 1
    assert dupe_clusters[0]["size"] == 2
    # Grace (no duplicate) surfaces in the unique table, not in a multi-member cluster.
    assert result.unique is not None and result.unique.height == 1
    assert result.unique["full_name"].to_list() == ["Grace Hopper"]


INV = get_template("invoice")
RCPT = get_template("receipt")


def _erow(schema, vals):
    return ExtractedRow.from_partial(vals, {}, schema, source_file="", source_page=0)


def test_auto_classify_e2e_two_frames_feed_dedupe(tmp_path):
    """Full auto-classify batch: an invoice (with line items) + a receipt (flat
    doctype) both classify confidently, extract against their templates, and the
    header frame feeds dedupe_df cleanly with DOC_SIDECARS excluded."""
    inv, rcpt = tmp_path / "inv.png", tmp_path / "rcpt.png"
    _img(inv); _img(rcpt)

    inv_header = _erow(INV.header, {"invoice_number": "INV-1", "vendor_name": "Acme"})
    inv_items = [_erow(INV.line_items, {"description": "Widget", "quantity": "2"}),
                 _erow(INV.line_items, {"description": "Gadget", "quantity": "1"})]
    rcpt_header = _erow(RCPT.header, {"merchant_name": "Cafe", "total_amount": "9.50"})

    cls = FakeClassifier([ClassifyResult("invoice", 0.92), ClassifyResult("receipt", 0.88)])
    te = FakeTemplateExtractor([
        StructuredResult(header=inv_header, line_items=inv_items, error=None),
        StructuredResult(header=rcpt_header, line_items=[], error=None),
    ])
    fb = FakeFallbackExtractor([])  # both classify as known doctypes -> unused

    df, report = ingest_documents([inv, rcpt], classifier=cls, template_extractor=te,
                                  fallback_extractor=fb, return_report=True)

    assert df.height == 2
    assert set(DOC_SIDECARS) <= set(df.columns)
    # match cols present alongside the sidecars
    assert "invoice_number" in df.columns and "merchant_name" in df.columns

    # header frame feeds the ER pipeline with the provenance sidecars excluded
    result = dedupe_df(df, exclude_columns=DOC_SIDECARS, confidence_required=False,
                       allow_red_config=True)
    assert result is not None

    # report is fully populated by the auto flow
    assert report.vlm_calls == 4  # 2 docs * (classify + structured extract)
    assert set(report.doctypes.values()) == {"invoice", "receipt"}
    assert set(report.classify_confidence) == set(report.doctypes)  # key-set alignment
    assert sorted(report.classify_confidence.values()) == [0.88, 0.92]

    # only the invoice contributes line items
    assert report.line_items is not None and report.line_items.height == 2
    inv_doc_id = next(d for d, t in report.doctypes.items() if t == "invoice")
    assert report.line_items["_doc_id"].to_list() == [inv_doc_id, inv_doc_id]
