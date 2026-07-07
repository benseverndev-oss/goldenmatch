# goldenmatch.documents

Turn a pile of PDFs/images into a records DataFrame GoldenMatch can dedupe.

```python
from goldenmatch import dedupe_df
from goldenmatch.documents import ingest_documents, TargetSchema, Field

schema = TargetSchema([
    Field("full_name"), Field("email", kind="email"),
    Field("address"), Field("phone", kind="phone"),
])
df = ingest_documents(["forms/*.pdf", "cards/img_01.jpg"], schema)  # backend="vlm", gpt-4o

clusters = dedupe_df(
    df,
    fuzzy={"full_name": 0.85}, exact=["email"],
    exclude_columns=["_source_file", "_source_page", "_extract_confidence"],
)
```

Install the extra: `pip install "goldenmatch[documents]"`. The VLM backend reads
`OPENAI_API_KEY_PERSONAL` (or `OPENAI_API_KEY`). A local OCR backend and MCP/CLI wrappers
are planned (Phases 2-3).
