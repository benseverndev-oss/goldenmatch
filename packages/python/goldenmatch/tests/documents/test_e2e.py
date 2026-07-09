from goldenmatch import dedupe_df
from goldenmatch.documents import DOC_SIDECARS, ingest_documents
from goldenmatch.documents.extractor import FakeExtractor
from goldenmatch.documents.types import (
    ExtractedRow,
    ExtractResult,
    Field,
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
