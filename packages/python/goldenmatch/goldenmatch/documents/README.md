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
`OPENAI_API_KEY_PERSONAL` (or `OPENAI_API_KEY`). A local OCR backend is planned (Phase 3).

## CLI

```bash
# 1. propose a schema from a sample doc (review/edit the file after)
goldenmatch ingest-docs suggest-schema samples/form.pdf --out schema.json
# 2. ingest the pile against it
goldenmatch ingest-docs run inbox/*.pdf --schema schema.json --out records.csv
```

## MCP

- `documents_suggest_schema(sample_path)` → proposed schema JSON
- `documents_ingest(paths, schema, out_path?)` → `{records, report}` (records ready for dedupe)

## REST (goldenmatch[web])

```bash
# suggest a schema from a sample
curl -F file=@form.pdf localhost:8000/api/v1/documents/suggest-schema
# ingest a pile against a schema -> records + report JSON
curl -F files=@a.pdf -F files=@b.jpg -F schema='{"fields":[{"name":"full_name"},{"name":"email","kind":"email"}]}' \
     localhost:8000/api/v1/documents/ingest
```

Records carry `_source_file`/`_source_page`/`_extract_confidence`; pass them to
`dedupe_df(exclude_columns=...)`. Auth: set `GOLDENMATCH_WEB_TOKEN` and send `Authorization: Bearer`.
