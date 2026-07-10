# Document/Image Ingest for GoldenMatch — design

**Goal.** Let GoldenMatch run on a pile of documents (PDFs + images), not just structured
CSV/DataFrame input. Turn a mixed pile — some files one record each (forms, cards, IDs),
some many records each (tables, directory pages) — into a single Polars DataFrame that drops
straight into the existing ER pipeline: `dedupe_df(df, ...)` for an in-memory frame
(`goldenmatch/_api.py:400`), or `dedupe(*files, ...)` for file paths.

**Non-goal.** No changes to blocking/scoring/clustering. This is purely a new front-end
extraction stage that produces the DataFrame the pipeline already consumes.

## Decisions (settled in brainstorming)

- **Input:** mixed documents — per-file record count unknown, classified and routed at extract time.
- **Engine:** pluggable `Extractor` interface. Cloud VLM backend is the accurate default; a
  local OCR backend (Phase 3) is the PII/offline option. Chosen per run.
- **Schema:** user-provided target schema. The extractor targets those columns in every
  document; missing → null. Deterministic and ER-ready across a heterogeneous pile.
- **Approach A (VLM-first, one call per document):** a single vision call does
  classify-and-extract together — single-record docs return one row, tables return many.
  Classification is implicit in the output; no separate classifier stage or cost.
- **Default model:** `gpt-4o` (vision) via the personal OpenAI key (`OPENAI_API_KEY_PERSONAL`,
  Infisical). Anthropic Claude vision is the alternate backend.
- **Phase 1 = Python API only.** MCP tool + CLI are Phase 2; local OCR backend + review-queue
  integration are Phase 3.

## Module layout — `goldenmatch/documents/`

| file | responsibility | depends on |
|---|---|---|
| `types.py` | `Field`, `TargetSchema`, `ExtractedRow`, `ExtractResult`, `IngestReport` | stdlib only |
| `loader.py` | `load_pages(path) -> list[PageImage]`: rasterize PDFs (PyMuPDF), normalize images | pymupdf, Pillow |
| `extractor.py` | `Extractor` Protocol + `extract(pages, schema) -> ExtractResult` | types |
| `vlm_backend.py` | `VLMExtractor`: schema-directed vision prompt, JSON parse + validate, confidence | types, http client |
| `ocr_backend.py` | `OCRExtractor` (Phase 3): same interface via local OCR + schema-map parser | types, tesseract |
| `assemble.py` | concat `ExtractResult`s → `pl.DataFrame` (schema cols + provenance sidecars) | polars, types |
| `config.py` | backend selection, model id, key source, retry limits | stdlib |
| `__init__.py` | public `ingest_documents(...)` | all of the above |

Each unit is single-purpose and offline-testable; the extractor is the only unit that touches
a network/vision backend, and it is behind a Protocol so the rest is pure.

## Core types

```python
@dataclass(frozen=True)
class Field:
    name: str                 # DataFrame column name
    kind: str = "text"        # text | email | phone | address | date | number
    hint: str | None = None   # natural-language guidance for the VLM

@dataclass(frozen=True)
class TargetSchema:
    fields: list[Field]

@dataclass(frozen=True)
class ExtractedRow:
    values: dict[str, str | None]       # aligned to schema.fields (missing -> None)
    confidence: dict[str, float]        # per-field 0..1
    source_file: str
    source_page: int | None             # page/region the row came from

@dataclass(frozen=True)
class ExtractResult:
    rows: list[ExtractedRow]
    error: str | None = None            # set when a doc failed; rows == [] then
```

## The seam

```python
class Extractor(Protocol):
    def extract(self, pages: list[PageImage], schema: TargetSchema) -> ExtractResult: ...
```

`VLMExtractor.extract`: build one message with the page image(s) + a schema-directed
instruction ("return a JSON array; one object per record you find; keys = these field names;
omit a key if not present; also return a per-field confidence"), call the vision model, parse
+ validate the JSON against the schema, coerce to `ExtractedRow`s. Multi-page single-record
docs send all pages in one call; obviously-separate pages can be extracted per page.

## Data flow

```
ingest_documents(paths, schema, backend="vlm", model="gpt-4o", drop_empty=True)
  for path in paths:
    pages   = loader.load_pages(path)           # rasterize/normalize
    result  = extractor.extract(pages, schema)  # 1 or N rows, or error
  df, report = assemble(results, schema)
  return df                       # default
  # return_report=True  -> returns the tuple (df, report: IngestReport)
```

Output DataFrame columns = `schema.fields` names + `_source_file`, `_source_page`,
`_extract_confidence`. `assemble` is the single place that collapses `ExtractedRow.confidence`
(per-field) into the row-level `_extract_confidence` (min over fields) — nobody else computes
it. `smart_ingest` is bypassed because we already emit a clean typed frame.

**Handoff contract (must be explicit in the example + E2E test).** Callers pass the frame to
`dedupe_df(df, exclude_columns=["_source_file", "_source_page", "_extract_confidence"])` so the
provenance/confidence sidecars are never treated as match/blocking fields by auto-config
(`_api.py:414`). The extracted schema columns are the only ER-visible fields.

## Error handling (batch-safe)

- Unreadable/corrupt file, malformed VLM JSON after a strict-instruction retry, or empty
  extraction → recorded in `IngestReport.errors`, batch continues (never crashes the run).
- `drop_empty=True` drops all-null rows; `False` keeps them flagged with 0 confidence.
- Missing/invalid backend key or unknown model → fail fast at config time, before processing.
- Rasterization always runs, so scanned PDFs with no text layer are handled like any image.

## Testing (offline-first, matches repo culture)

- `loader`, `assemble`, JSON-validation in `vlm_backend` — pure unit tests on tiny fixtures
  (a 2-page fixture PDF, a fixture image, recorded VLM JSON responses). No network in CI.
- `FakeExtractor` (returns canned `ExtractResult`s) drives an end-to-end test:
  fixtures → `ingest_documents` → DataFrame → real
  `dedupe_df(df, exclude_columns=["_source_file","_source_page","_extract_confidence"])` on a
  small synthetic set, proving the seam and the downstream hand-off.
- VLM backend HTTP is injected (a fake transport returns recorded responses) so parsing,
  retry, and error paths are all covered offline.
- One optional live smoke behind a key marker (`OPENAI_API_KEY_PERSONAL`), excluded from CI.

## Phase 1 deliverables (the usable slice)

1. `goldenmatch/documents/` with `types`, `loader`, `extractor`, `vlm_backend`, `assemble`,
   `config`, `__init__`.
2. `ingest_documents(paths, schema, backend="vlm", model="gpt-4o", drop_empty=True,
   return_report=False) -> pl.DataFrame`.
3. Full offline test suite + one gated live smoke.
4. A short README/example: pile of docs → schema → `ingest_documents` → `dedupe_df`.

Deferred: MCP tool + CLI (Phase 2); local OCR backend + confidence-driven review-queue
integration (Phase 3).

## Open risks

- **Multi-record table fidelity** is the hardest case (row/column alignment from a rendered
  page). Phase 1 relies on the VLM; if fidelity is poor on real tables, Phase 3's OCR+layout
  path or a table-specific prompt becomes the mitigation. Measure on real fixtures before
  promising table support.
- **Cost**: one vision call per document (multi-page = one call). Fine interactively; a bulk
  mode with batching/concurrency is a later concern, not Phase 1.
