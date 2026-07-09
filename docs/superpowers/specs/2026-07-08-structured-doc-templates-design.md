# Structured-Doc Templates — Design

**Status:** approved (brainstorming), pending spec review
**Date:** 2026-07-08
**Module:** `goldenmatch.documents` + `goldenmatch-documents-core`
**Depends on:** document-image-ingest (Phase 1/2 + documents-core), all merged.

## Goal

Layer per-doctype **templates** over the generic VLM document extractor so that
invoices, purchase orders, statements, and receipts extract into *typed,
structurally-aware* records — a header entity plus its repeating line items —
instead of a single flat schema the caller has to hand-write per doc.

This is the upstream half of a two-feature arc. It gives the downstream feature
(cross-doc entity resolution) well-typed material to resolve on: a known
`vendor`/`buyer`/header entity per document, with line items carried as linked
attributes rather than mixed into the match fields.

## Non-goals (explicit YAGNI / scope fences)

- **Cross-doc entity resolution** — the next, separate spec. This spec stops at
  producing frames; it does not wire them into the identity graph.
- **MCP / CLI / REST / A2A / docs surfaces** — out of scope. Those all call
  `ingest_documents`, whose default return stays backward-compatible, so they
  keep working unchanged. Surfacing templates on them is a flagged fast-follow
  (see "Surfaces" below), NOT part of this spec's ship gate.
- **Local OCR backend** — unrelated Phase-3 work.
- **New doctypes beyond the four named** — add later; the registry makes it a
  data edit.

## Decisions (locked in brainstorming)

1. **Approach: core-first.** The deterministic logic lives in
   `goldenmatch-documents-core` (the single source of truth), bound to Python via
   `_native` and to TS via wasm; pure-Python is the lossy fallback; a parity
   corpus keeps all legs byte-identical. Same seam documents-core already
   established.
2. **A template = schema + row semantics.** Not just a flat `TargetSchema`: it
   declares *header fields* vs *repeating line-item fields*.
3. **Record model: two frames + link key.** Header → one record (the entity);
   line items → child records in a separate frame, linked by a stable `_doc_id`.
4. **Selection: auto-classify by default.** A VLM classifier picks the doctype
   unless the caller pins `template=`. Low classifier confidence → fall back to
   the existing generic `suggest_schema` flat path.
5. **v1 doctypes: invoice, PO, statement, receipt.** Receipt is flat
   (header-only, empty line-item fields); invoice / PO / statement are header +
   line items.

## Record model + API surface

### Return contract

`ingest_documents` keeps a backward-compatible default (single header
`DataFrame`) and exposes line items via the report:

```python
df = ingest_documents(paths)                          # header frame only (shape unchanged)
df, report = ingest_documents(paths, return_report=True)
report.line_items         # pl.DataFrame | None   (child rows across all docs; None if no doc had any)
report.doctypes           # dict[_doc_id -> "invoice"|"po"|"statement"|"receipt"|"generic"]
report.classify_confidence  # dict[_doc_id -> float]   (classifier confidence per doc; 1.0 for pinned/flat)
report.vlm_calls          # int   (cost visibility: classify + extract per doc)
report.errors             # list[tuple[str, str]]   (existing (file, msg) list; unchanged mechanism)
```

New / changed `ingest_documents` params:

```python
ingest_documents(
    paths,
    schema: TargetSchema | None = None,   # explicit flat schema (today's path); mutually exclusive with template
    *,
    template: str | DocTemplate | None = None,  # pin a doctype; None => auto-classify
    auto_classify: bool = True,           # when schema and template are both None
    classify_threshold: float = 0.6,      # below => generic fallback
    backend: str = "vlm",
    model: str = "gpt-4o",
    extractor: Extractor | None = None,   # overrides everything: flat, today's behavior (tests / custom)
    classifier: Classifier | None = None, # overrides the classifier (tests / custom); None => resolve from backend
    template_extractor: TemplateExtractor | None = None,  # overrides structured extract (tests / custom)
    drop_empty: bool = True,
    return_report: bool = False,
)
```

Precedence: `extractor` (flat) > `schema` (flat, today's behavior) > `template`
(pinned structured) > `auto_classify` (default). Passing both `schema` and
`template` raises `ValueError`. `classifier` / `template_extractor` are injection
seams for the structured path (mirroring how `extractor` injects the flat path);
`None` resolves them from `backend`/`model`.

### Ownership of the flow

`ingest_documents` **owns the per-doc branch** — it is not pushed into a single
extractor. Today's uniform `for fpath: ex.extract(...)` loop becomes a per-doc
dispatch (`_ingest_one`, below) that picks flat vs structured and calls the right
collaborator. This is what lets the flat `Extractor`, the `Classifier`, and the
`TemplateExtractor` stay three small single-purpose seams instead of one
overloaded `extract()`.

### Collaborator seams (resolves the return-type question)

The flat `Extractor` protocol is **unchanged** (`extract(pages, schema) ->
ExtractResult`). Two NEW protocols carry the structured path — a
`TemplateExtractor` does **not** try to satisfy the flat `Extractor` (it can't:
it returns header + line items, not a flat `ExtractResult`):

```python
@runtime_checkable
class Classifier(Protocol):
    def classify(self, pages: list[PageImage]) -> ClassifyResult: ...
    # ClassifyResult(doctype: str, confidence: float)

@runtime_checkable
class TemplateExtractor(Protocol):
    def extract_structured(self, pages: list[PageImage],
                           template: DocTemplate) -> StructuredResult: ...
    # StructuredResult(header: ExtractedRow | None,
    #                  line_items: list[ExtractedRow], error: str | None)
```

Concrete: `VLMClassifier` / `VLMTemplateExtractor` (behind the injectable
`Transport`) + `FakeClassifier` / `FakeTemplateExtractor` (scripted, for offline
tests). `assemble` consumes a per-doc `DocResult` union: a flat `ExtractResult`
(generic/receipt) **or** a `StructuredResult` tagged with `_doc_id` + `_doctype`.

### Resolver

`resolve_extractor(backend, model)` stays (flat path, unchanged signature). A new
sibling builds the structured collaborators:

```python
def resolve_structured(backend: str, model: str
                       ) -> tuple[Classifier, TemplateExtractor]:
    # backend != "vlm" -> ValueError, same as resolve_extractor
```

`ingest_documents` calls `resolve_extractor` for the flat path and
`resolve_structured` only when the structured path is actually reached (so a
flat-schema call still needs no classifier).

### The two frames

**Header frame** — one row per document:
- columns = the doctype's header fields (declared order)
- sidecars: `_doc_id`, `_doctype`, `_source_file`, `_source_page`,
  `_extract_confidence` (= min confidence over header fields)
- this is what flows to `dedupe_df(..., exclude_columns=[all sidecars])` and,
  later, the identity graph.

**Line-item frame** — one row per line item:
- columns = the doctype's line-item fields (declared order)
- sidecars: `_doc_id` (FK to header), `_line_no` (0-based within doc),
  `_source_file`, `_source_page`, `_extract_confidence` (= min over that item's
  fields)
- receipts / generic docs contribute nothing.
- if **no** doc in the batch produced line items, `report.line_items is None`
  (not an empty frame) — pure-receipt batches feel exactly like today.

### `_doc_id` (load-bearing)

Content-hash of the **`source_file` path only** (normalized/absolute), via the
existing `goldenmatch.core._hashing.record_fingerprint` helper. Requirements:
- **stable across re-runs** — hash, not random UUID. Crucially, the key is the
  *input path*, NOT the VLM-extracted header values. (An earlier draft hashed
  header values too; that breaks idempotency — any VLM non-determinism above
  temp=0 would mint a new `_doc_id` for the same file. The path is the only
  re-run-stable, input-derived key.)
- unique per file. **v1 assumes one file = one doc.** Multiple logical documents
  packed into one file (e.g. a scanned batch of separate receipts on successive
  pages) is a **non-goal** — noted below.
- **duplicate input paths collapse:** `ingest_documents` de-duplicates the input
  `paths` list before processing (last occurrence wins), so the same file passed
  twice yields one header row, not two rows sharing a `_doc_id` (which would make
  the line-item FK join fan out). This keeps "stable" and "unique" from
  colliding.
- stamped once onto the header row and every child line-item row (the join key).
- `_doc_id` is a **sidecar** (excluded from match fields) but MUST survive into
  the output frames — it is the ER join key the next feature consumes.

### Sidecar exclude list (breaking-ish, call out loudly)

The header frame now carries **five** sidecars (`_source_file`, `_source_page`,
`_extract_confidence`, `_doc_id`, `_doctype`) vs the three today. Any caller
handing the frame to `dedupe_df` must exclude all five, and `_doc_id` must
survive in the output (it's the ER join key) while never being a match field. The
`ingest_documents` docstring example and the module README hard-code the
three-item list today — both get updated in this spec. Provide a helper constant
`DOC_SIDECARS: list[str]` exported from `goldenmatch.documents` so callers write
`dedupe_df(df, exclude_columns=DOC_SIDECARS)` instead of a hand-copied literal
that silently rots when a sidecar is added.

### Mixed batches

Each doc is classified and extracted independently. Header rows are outer-unioned
on the superset of header columns (missing → null, reusing today's assemble union
logic); line-item rows likewise. `_doctype` keeps them separable downstream.

## Core layout (`goldenmatch-documents-core`)

New modules / symbols, following the existing kernel pattern (serialize structs
for stable key order; empty-string hint is falsy; fence-strip via `rfind`/rsplit;
error-parity by outcome, not message):

- `templates.rs`
  - static definitions: each `DocTemplate = { doctype, header_fields: [Field],
    line_item_fields: [Field] }`, `Field = { name, kind, hint }`. Receipt has
    empty `line_item_fields`.
  - `documents_template(doctype) -> JSON`
  - `documents_template_list() -> JSON`
- `classify.rs`
  - `documents_classify_prompt() -> String` (doctype-classification instruction;
    same pattern as `documents_suggest_prompt`)
  - `documents_parse_classify(text) -> JSON` → `{doctype, confidence}`
    (fence-strip + validation; reuses envelope-parse helpers; unknown doctype or
    malformed → parity error)
- `extract_structured.rs`
  - `documents_parse_structured(text, template_json) -> JSON`: turns the VLM
    `{header: {...}, line_items: [{...}]}` response into normalized
    `{header, line_items}`, each field coerced/ordered against the template using
    the same normalize discipline as `documents_normalize_record`
    (schema-order re-imposed, empty-hint falsy, float `str()` parity).

Each kernel is wired to Python through **two** native touch points (NOT just the
core crate — this is the seam a first-time builder misses):
1. `native/src/documents.rs` — a `#[pyfunction]` shim per symbol (same pattern as
   the existing `documents_normalize_record` shim) that calls into the pyo3-free
   `goldenmatch-documents-core` crate.
2. `native/src/lib.rs` — a `wrap_pyfunction!` registration per shim in the module
   init.

Then the 5 names are added to `_COMPONENT_SYMBOLS["documents"]` in
`core/_native_loader.py`. Note that this tuple edit is **cosmetic** — `_has_symbol`
is "any symbol present" and documents is already native-capable via its floor
symbols. Real wheel-skew safety (the #688 lesson) comes from **each new Python
call site `hasattr`-guarding its own specific symbol** (as `types.py` already
does for `documents_normalize_record`) and falling back to pure-Python — so a
published wheel missing a new symbol degrades instead of `AttributeError`-ing.
Every new call site in this spec MUST carry that guard.

## Python layout (`goldenmatch/documents/`)

- `types.py` — add `DocTemplate(doctype, header: TargetSchema, line_items:
  TargetSchema)`, `StructuredResult(header: ExtractedRow | None, line_items:
  list[ExtractedRow], error: str | None)`, and `ClassifyResult(doctype: str,
  confidence: float)`.
- `templates.py` — thin accessor over core: `get_template(doctype) ->
  DocTemplate`, `list_templates() -> list[str]`. Carries a **pure-Python literal
  mirror** of the four definitions as the lossy fallback when native is off
  (`hasattr`-guarded). The mirror is what the parity corpus guards against drift.
- `extractor.py` — flat `Extractor` protocol + `VLMExtractor`/`FakeExtractor`
  stay unchanged. Add the NEW `Classifier` + `TemplateExtractor` protocols and
  their `VLM*` / `Fake*` implementations (see "Collaborator seams" above). A
  `TemplateExtractor` does NOT implement the flat `Extractor` protocol.
- `config.py` — `resolve_extractor(backend, model)` unchanged (flat); add
  `resolve_structured(backend, model) -> (Classifier, TemplateExtractor)`.
- `assemble.py` — extend to build the two frames from a per-doc `DocResult` union
  (see below); flat path untouched.
- `__init__.py` — extend `ingest_documents` per the API above; own the per-doc
  `_ingest_one` dispatch; export `DocTemplate`, `list_templates`, `DOC_SIDECARS`.

### Intentional duplication

Template definitions live in three places (Rust authoritative + Python literal
fallback + eventually TS). This is deliberate — the Python/TS copies are the
lossy-fallback contract, and the parity corpus fails the build on drift. Adding a
field to a template is a three-place edit; accepted, consistent with the rest of
the module.

## Classifier + extraction flow

All VLM calls are Python, behind the injectable `Transport` seam
(offline-testable, same as `suggest_schema`).

Per document, auto-classify default:

1. **Load** pages (`load_pages`).
2. **Classify** — one VLM call with `documents_classify_prompt()` + page images
   → `documents_parse_classify` → `{doctype, confidence}`.
3. **Route on confidence:**
   - `confidence >= classify_threshold` and doctype in the v1 set → pick that
     template.
   - otherwise → **generic fallback**: `suggest_schema` → flat single-frame
     extract (today's Phase-2 behavior). `_doctype = "generic"`, no line items.
4. **Structured extract** — one VLM call against the chosen template →
   `{header, line_items}` → `documents_parse_structured`.
5. **Assemble** into the two frames.

**Override path:** `template=` (or `DocTemplate` / `TargetSchema`) → skip 2–3,
go straight to structured extract. Zero classifier cost, fully deterministic;
also how tests pin behavior.

**Cost:** auto-classify = **2 VLM calls/doc** (classify + extract) vs 1 today;
pinning `template=` = 1. The classifier prompt is short / low `max_tokens`.
`report.vlm_calls` surfaces the count so cost is visible, not hidden.

**Failure handling** (matches existing batch semantics):
- classify failure → fall back to generic (don't fail the doc).
- structured-extract failure → recorded via the **existing** mechanism:
  `StructuredResult.error` is set and `report.errors.append((file, msg))` (the
  same `errors: list[tuple[str, str]]` list assemble already maintains — NOT a
  new frame row); batch continues; that doc produces no header/line-item rows.

## Two-frame assemble

Extends `assemble.py`; the flat path (generic / receipt) stays as-is.

Input: per-doc `DocResult` results, each either a flat `ExtractResult`
(generic/receipt) or a `StructuredResult` tagged with `_doc_id` + `_doctype`.

- **`_doc_id`** — computed once (content-hash as above), stamped on header +
  every child.
- **Header frame** — one header row per doc; mixed doctypes outer-unioned on the
  superset of header columns (missing → null). `drop_empty` applies to header
  match fields only (all-null header dropped, same rule as today).
- **Line-item frame** — one row per item with `_doc_id` FK + `_line_no`; none for
  receipts/generic; `None` (not empty frame) if the whole batch had zero items.
- **Column ordering** re-imposed from the template (fields in declared order,
  then sidecars) — deterministic, diff-stable, same discipline as
  `documents_normalize_record`.
- **Report additions:** `line_items: pl.DataFrame | None`, `doctypes: dict`,
  `classify_confidence: dict`, `vlm_calls: int`.

This is where the flat and structured paths converge → highest-risk unit for
column-order / union bugs → heaviest test coverage.

## Testing

All offline (injectable transport + `FakeExtractor`; no network, no API key).

1. **Core kernels (Rust unit):** template lookup exactness; classify-parse on
   clean/fenced/malformed/missing-confidence; structured-parse coercion +
   ordering, empty line-items, extra/missing fields.
2. **Parity corpus:** extend `tests/parity/documents_corpus.jsonl` with rows for
   `template`, `parse_classify`, `parse_structured`. Replayed by Rust,
   pure-Python, and (last task) TS — byte-identical or build fails. Drift guard
   on the three-place duplication.
3. **Python dual-path:** every documents test under native **and**
   `GOLDENMATCH_NATIVE=0`; `templates.py` mirror must equal core output.
4. **Assemble (heaviest):** single invoice (header + N items, `_doc_id`/`_line_no`
   correct); receipt (flat, `line_items is None`); mixed batch (all four →
   correct union, `_doctype` tags, per-doc `_doc_id`); re-ingest same file →
   identical `_doc_id` (idempotency); doc with zero items in an otherwise
   structured batch.
5. **Flow:** auto-classify picks right template (scripted classifier transport);
   low-confidence → generic fallback; `template=` override skips classify (assert
   call count == 1); classify failure → generic, batch continues; extract failure
   → recorded in `report.errors`, batch continues.
6. **E2E:** `ingest_documents` → header frame → `dedupe_df(exclude_columns=[...])`
   clean; `return_report=True` surfaces line items + doctypes + `vlm_calls`.

**Ship gate:** full documents suite green on native **and** pure paths; parity
corpus green on Rust + Python legs before TS; no regression in existing
flat/Phase-2 tests.

## Task staging (build order)

Each core task = crate module **+** `native/src/documents.rs` shim(s) **+**
`native/src/lib.rs` registration **+** `_COMPONENT_SYMBOLS` edit **+** the Python
accessor with its `hasattr` guard **+** parity rows. "Core `X.rs`" below is
shorthand for that whole vertical slice.

1. Core `templates.rs` (+ shims/registration) + Python `templates.py` accessor &
   pure mirror + `DocTemplate` type + parity rows.
2. Core `classify.rs` (prompt + parse, + shims/registration) + parity rows.
3. Core `extract_structured.rs` (parse structured, + shim/registration) + parity
   rows.
4. Python `StructuredResult` / `ClassifyResult` types + `Classifier` &
   `TemplateExtractor` protocols + `VLM*`/`Fake*` impls + `resolve_structured`.
5. Python two-frame `assemble` (DocResult union) + `IngestReport` extensions
   (`line_items`, `doctypes`, `classify_confidence`, `vlm_calls`) +
   `ingest_documents` return contract / params + `_ingest_one` dispatch + path
   de-dup + `DOC_SIDECARS` export + docstring/README exclude-list update.
6. Python flow (auto-classify default, threshold routing, generic fallback,
   overrides) + all flow/assemble/e2e tests.
7. TS/wasm: bind the 5 new kernels + replay the extended corpus (parity leg) —
   last, mirroring how documents-core shipped.

## Surfaces (fast-follow, not this spec)

Flagged per the rollout-docs-sweep lesson so it is not silently skipped:
MCP tool params, CLI `ingest-docs`, REST endpoint, A2A skill, and the docs-site
guide should learn the `template` / auto-classify option and the line-item output
in a follow-up. Tracked, out of this spec's gate.

## Risks / notes

- **Cost doubling** on the default path (2 VLM calls/doc). Mitigated by cheap
  classifier + `report.vlm_calls` visibility + `template=` escape hatch.
- **Classifier determinism** — handled by the injectable transport seam; all
  tests pin via scripted transports, never live.
- **exFAT Rust build friction** (known): invoke `cargo.exe` / `rustc.exe` by
  absolute path; `RUSTUP_HOME=D:/.rustup`.
- **Three-place template duplication** — intentional, corpus-guarded.
