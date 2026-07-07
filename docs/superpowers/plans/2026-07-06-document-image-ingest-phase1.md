# Document/Image Ingest (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `goldenmatch.documents.ingest_documents(paths, schema)` that turns a pile of PDFs/images into one Polars DataFrame the existing ER pipeline (`dedupe_df`) can consume.

**Architecture:** A new `goldenmatch/documents/` subpackage. One vision call per document does classify-and-extract (single-record → 1 row, table → N rows) behind a pluggable `Extractor` seam. Cloud VLM backend (default) uses stdlib `urllib` with an **injectable transport** so all parsing/retry/error paths test offline; a `FakeExtractor` drives the end-to-end test. Output = target-schema columns + `_source_file`/`_source_page`/`_extract_confidence` sidecars, handed to `dedupe_df(df, exclude_columns=[...])`.

**Tech Stack:** Python 3.11+, Polars, PyMuPDF (`fitz`) for PDF rasterization, Pillow for image normalization, stdlib `urllib` + base64 data-URIs for the OpenAI vision call. TDD with pytest.

**Spec:** `docs/superpowers/specs/2026-07-06-document-image-ingest-design.md`

---

## Conventions for every task

- **Worktree:** `D:/show_case/gm-docingest`. Package root: `packages/python/goldenmatch`.
- **Python:** use the main venv interpreter `D:/show_case/goldenmatch/.venv/Scripts/python.exe`.
- **Run tests from** `packages/python/goldenmatch` with this env so imports resolve to THIS worktree and Windows/polars/native quirks are avoided:

  ```bash
  cd packages/python/goldenmatch
  PY="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="D:/show_case/gm-docingest/packages/python/goldenmatch"
  export GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
  ```
  Then e.g. `"$PY" -m pytest tests/documents/test_assemble.py -q`.
- **Lint before each commit:** `"$PY" -m ruff check goldenmatch/documents tests/documents`.
- **Commits:** end the message with the two trailer lines used across this repo
  (`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and the
  `Claude-Session:` line). Use `git -c commit.gpgsign=false commit`.

---

## File structure (locked)

| path | responsibility |
|---|---|
| `goldenmatch/documents/__init__.py` | public `ingest_documents(...)`; re-export core types |
| `goldenmatch/documents/types.py` | `Field`, `TargetSchema`, `PageImage`, `ExtractedRow`, `ExtractResult`, `IngestReport` |
| `goldenmatch/documents/loader.py` | `load_pages(path) -> list[PageImage]` (PyMuPDF + Pillow) |
| `goldenmatch/documents/extractor.py` | `Extractor` Protocol |
| `goldenmatch/documents/vlm_backend.py` | `VLMExtractor` (OpenAI vision via injectable transport) |
| `goldenmatch/documents/assemble.py` | `assemble(results, schema, drop_empty) -> (pl.DataFrame, IngestReport)` |
| `goldenmatch/documents/config.py` | backend/model/key resolution, fail-fast validation |
| `tests/documents/test_*.py` | one test module per unit + `test_e2e.py` |

---

## Task 0: Scaffold package + dependency extra

**Files:**
- Create: `goldenmatch/documents/__init__.py` (empty for now), `tests/documents/__init__.py`
- Modify: `pyproject.toml` (add `documents` extra)

- [ ] **Step 1: Add the `documents` optional-dependency extra.** In `pyproject.toml`, under
  `[project.optional-dependencies]`, after the `vision = [...]` block, add:

  ```toml
  # Document/image ingest (goldenmatch.documents): rasterize PDFs to page images
  # for the VLM/OCR extractors. Pillow (also in `vision`) normalizes raster images.
  documents = [
      "pymupdf>=1.24",
      "Pillow>=10.0",
  ]
  ```

- [ ] **Step 2: Install the extra into the venv** (additive; pymupdf is a self-contained wheel):

  Run: `"$PY" -m pip install "pymupdf>=1.24"`
  Expected: installs `pymupdf` (Pillow already present).

- [ ] **Step 3: Create empty package + test package.**

  `goldenmatch/documents/__init__.py`:
  ```python
  """Document/image ingest: turn PDFs/images into a records DataFrame for GoldenMatch."""
  ```
  `tests/documents/__init__.py`: (empty file)

- [ ] **Step 4: Verify import.**
  Run: `"$PY" -c "import goldenmatch.documents"`  Expected: no error.

- [ ] **Step 5: Commit.**
  ```bash
  git add pyproject.toml goldenmatch/documents/__init__.py tests/documents/__init__.py
  git -c commit.gpgsign=false commit -m "feat(documents): scaffold package + documents extra"
  ```

---

## Task 1: Core types

**Files:**
- Create: `goldenmatch/documents/types.py`
- Test: `tests/documents/test_types.py`

- [ ] **Step 1: Write the failing test.** `tests/documents/test_types.py`:

  ```python
  from goldenmatch.documents.types import (
      ExtractedRow, ExtractResult, Field, IngestReport, PageImage, TargetSchema,
  )


  def test_target_schema_column_names_preserve_order():
      s = TargetSchema([Field("full_name"), Field("email", kind="email"), Field("city")])
      assert s.column_names() == ["full_name", "email", "city"]


  def test_extracted_row_defaults_missing_field_to_none_and_zero_conf():
      s = TargetSchema([Field("full_name"), Field("email")])
      row = ExtractedRow.from_partial(
          {"full_name": "Ada Lovelace"}, {"full_name": 0.9}, s,
          source_file="a.pdf", source_page=0)
      assert row.values == {"full_name": "Ada Lovelace", "email": None}
      assert row.confidence == {"full_name": 0.9, "email": 0.0}
      assert row.row_confidence() == 0.0  # min over fields


  def test_extract_result_error_has_no_rows():
      r = ExtractResult(rows=[], error="boom")
      assert r.rows == [] and r.error == "boom"
  ```

- [ ] **Step 2: Run to verify it fails.**
  Run: `"$PY" -m pytest tests/documents/test_types.py -q`  Expected: FAIL (module missing).

- [ ] **Step 3: Implement `types.py`.**

  ```python
  """Value types for document/image ingest. Stdlib only, offline-testable."""
  from __future__ import annotations

  from dataclasses import dataclass, field


  @dataclass(frozen=True)
  class Field:
      name: str
      kind: str = "text"          # text | email | phone | address | date | number
      hint: str | None = None     # natural-language guidance for the VLM


  @dataclass(frozen=True)
  class TargetSchema:
      fields: list[Field]

      def column_names(self) -> list[str]:
          return [f.name for f in self.fields]


  @dataclass(frozen=True)
  class PageImage:
      png_bytes: bytes            # normalized PNG
      width: int
      height: int
      index: int                  # 0-based page index within the source file


  @dataclass(frozen=True)
  class ExtractedRow:
      values: dict[str, str | None]
      confidence: dict[str, float]
      source_file: str
      source_page: int | None

      @classmethod
      def from_partial(cls, values, confidence, schema: TargetSchema,
                       *, source_file: str, source_page: int | None) -> "ExtractedRow":
          cols = schema.column_names()
          v = {c: values.get(c) for c in cols}
          conf = {c: float(confidence.get(c, 0.0)) for c in cols}
          return cls(values=v, confidence=conf,
                     source_file=source_file, source_page=source_page)

      def row_confidence(self) -> float:
          return min(self.confidence.values()) if self.confidence else 0.0


  @dataclass(frozen=True)
  class ExtractResult:
      rows: list[ExtractedRow] = field(default_factory=list)
      error: str | None = None


  @dataclass
  class IngestReport:
      n_files: int = 0
      n_rows: int = 0
      errors: list[tuple[str, str]] = field(default_factory=list)  # (file, message)
  ```

- [ ] **Step 4: Run to verify pass.**
  Run: `"$PY" -m pytest tests/documents/test_types.py -q`  Expected: PASS (3 tests).

- [ ] **Step 5: Commit.**
  ```bash
  git add goldenmatch/documents/types.py tests/documents/test_types.py
  git -c commit.gpgsign=false commit -m "feat(documents): core value types"
  ```

---

## Task 2: Loader (PDF/image → normalized page images)

**Files:**
- Create: `goldenmatch/documents/loader.py`
- Test: `tests/documents/test_loader.py`

Tests generate fixtures programmatically (a 2-page PDF via `fitz`, a PNG via Pillow) into a
`tmp_path`, so no binary fixtures are committed.

- [ ] **Step 1: Write the failing test.** `tests/documents/test_loader.py`:

  ```python
  import fitz  # pymupdf
  from PIL import Image

  from goldenmatch.documents.loader import load_pages


  def _make_pdf(path, n_pages):
      doc = fitz.open()
      for i in range(n_pages):
          page = doc.new_page(width=200, height=200)
          page.insert_text((20, 40), f"page {i}")
      doc.save(str(path))
      doc.close()


  def test_load_pdf_yields_one_pageimage_per_page(tmp_path):
      p = tmp_path / "two.pdf"
      _make_pdf(p, 2)
      pages = load_pages(p)
      assert len(pages) == 2
      assert [pg.index for pg in pages] == [0, 1]
      assert all(pg.png_bytes[:8] == b"\x89PNG\r\n\x1a\n" for pg in pages)
      assert all(pg.width > 0 and pg.height > 0 for pg in pages)


  def test_load_image_yields_single_page(tmp_path):
      p = tmp_path / "card.png"
      Image.new("RGB", (120, 80), "white").save(p)
      pages = load_pages(p)
      assert len(pages) == 1
      assert pages[0].index == 0
      assert pages[0].png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


  def test_load_unsupported_extension_raises(tmp_path):
      p = tmp_path / "x.txt"
      p.write_text("hi")
      import pytest
      with pytest.raises(ValueError, match="unsupported"):
          load_pages(p)
  ```

- [ ] **Step 2: Run to verify it fails.**
  Run: `"$PY" -m pytest tests/documents/test_loader.py -q`  Expected: FAIL (module missing).

- [ ] **Step 3: Implement `loader.py`.**

  ```python
  """Load a document file into normalized PNG page images."""
  from __future__ import annotations

  import io
  from pathlib import Path

  from PIL import Image

  from goldenmatch.documents.types import PageImage

  _PDF = {".pdf"}
  _IMG = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
  _PDF_DPI = 200  # rasterization DPI; enough for text, bounded for cost


  def _png(img: Image.Image) -> PageImage:
      if img.mode not in ("RGB", "L"):
          img = img.convert("RGB")
      buf = io.BytesIO()
      img.save(buf, format="PNG")
      return PageImage(png_bytes=buf.getvalue(), width=img.width, height=img.height, index=0)


  def load_pages(path: str | Path) -> list[PageImage]:
      path = Path(path)
      ext = path.suffix.lower()
      if ext in _IMG:
          with Image.open(path) as img:
              img.load()
              return [_png(img)]
      if ext in _PDF:
          import fitz  # imported lazily so the extra is only needed for PDFs
          out: list[PageImage] = []
          with fitz.open(str(path)) as doc:
              zoom = _PDF_DPI / 72.0
              mat = fitz.Matrix(zoom, zoom)
              for i, page in enumerate(doc):
                  pix = page.get_pixmap(matrix=mat, alpha=False)
                  img = Image.open(io.BytesIO(pix.tobytes("png")))
                  pg = _png(img)
                  out.append(PageImage(pg.png_bytes, pg.width, pg.height, index=i))
          return out
      raise ValueError(f"unsupported file type: {ext!r} (path={path})")
  ```

- [ ] **Step 4: Run to verify pass.**
  Run: `"$PY" -m pytest tests/documents/test_loader.py -q`  Expected: PASS (3 tests).

- [ ] **Step 5: Commit.**
  ```bash
  git add goldenmatch/documents/loader.py tests/documents/test_loader.py
  git -c commit.gpgsign=false commit -m "feat(documents): loader rasterizes PDFs/images to PNG pages"
  ```

---

## Task 3: Extractor protocol + FakeExtractor

**Files:**
- Create: `goldenmatch/documents/extractor.py`
- Test: `tests/documents/test_extractor.py`

- [ ] **Step 1: Write the failing test.** `tests/documents/test_extractor.py`:

  ```python
  from goldenmatch.documents.extractor import Extractor, FakeExtractor
  from goldenmatch.documents.types import ExtractResult, Field, PageImage, TargetSchema


  def test_fake_extractor_returns_scripted_result_and_satisfies_protocol():
      schema = TargetSchema([Field("full_name")])
      canned = ExtractResult(rows=[])
      fake = FakeExtractor([canned])
      assert isinstance(fake, Extractor)
      out = fake.extract([PageImage(b"x", 1, 1, 0)], schema)
      assert out is canned
  ```

- [ ] **Step 2: Run to verify it fails.**
  Run: `"$PY" -m pytest tests/documents/test_extractor.py -q`  Expected: FAIL (module missing).

- [ ] **Step 3: Implement `extractor.py`.**

  ```python
  """The extractor seam: a Protocol plus a scripted fake for tests."""
  from __future__ import annotations

  from typing import Protocol, runtime_checkable

  from goldenmatch.documents.types import ExtractResult, PageImage, TargetSchema


  @runtime_checkable
  class Extractor(Protocol):
      def extract(self, pages: list[PageImage], schema: TargetSchema) -> ExtractResult: ...


  class FakeExtractor:
      """Returns pre-scripted results in order; for pipeline/e2e tests (no network)."""

      def __init__(self, scripted: list[ExtractResult]):
          self._scripted = list(scripted)
          self._i = 0

      def extract(self, pages: list[PageImage], schema: TargetSchema) -> ExtractResult:
          r = self._scripted[self._i]
          self._i += 1
          return r
  ```

- [ ] **Step 4: Run to verify pass.**
  Run: `"$PY" -m pytest tests/documents/test_extractor.py -q`  Expected: PASS.

- [ ] **Step 5: Commit.**
  ```bash
  git add goldenmatch/documents/extractor.py tests/documents/test_extractor.py
  git -c commit.gpgsign=false commit -m "feat(documents): Extractor protocol + FakeExtractor"
  ```

---

## Task 4: Assemble (results → DataFrame + report)

**Files:**
- Create: `goldenmatch/documents/assemble.py`
- Test: `tests/documents/test_assemble.py`

- [ ] **Step 1: Write the failing test.** `tests/documents/test_assemble.py`:

  ```python
  import polars as pl

  from goldenmatch.documents.assemble import assemble
  from goldenmatch.documents.types import (
      ExtractedRow, ExtractResult, Field, TargetSchema,
  )

  SCHEMA = TargetSchema([Field("full_name"), Field("email")])


  def _row(vals, conf, f="a.pdf", pg=0):
      return ExtractedRow.from_partial(vals, conf, SCHEMA, source_file=f, source_page=pg)


  def test_assemble_builds_frame_with_schema_and_sidecar_columns():
      results = [
          ExtractResult(rows=[_row({"full_name": "Ada", "email": "ada@x.io"},
                                   {"full_name": 0.9, "email": 0.8})]),
          ExtractResult(rows=[  # a 2-row "table" doc
              _row({"full_name": "Bo", "email": "bo@x.io"}, {"full_name": 0.7, "email": 0.7}, f="t.pdf", pg=1),
              _row({"full_name": "Cy", "email": None}, {"full_name": 0.6, "email": 0.0}, f="t.pdf", pg=1),
          ]),
      ]
      df, report = assemble(results, SCHEMA, drop_empty=True)
      assert df.columns == ["full_name", "email", "_source_file", "_source_page", "_extract_confidence"]
      assert df.height == 3
      assert df["_source_file"].to_list() == ["a.pdf", "t.pdf", "t.pdf"]
      # row_confidence is the min over fields
      assert df.filter(pl.col("full_name") == "Cy")["_extract_confidence"][0] == 0.0
      assert report.n_files == 2 and report.n_rows == 3 and report.errors == []


  def test_assemble_records_errors_and_continues():
      results = [ExtractResult(rows=[], error="bad json"),
                 ExtractResult(rows=[_row({"full_name": "Ada", "email": "a@x.io"},
                                          {"full_name": 0.9, "email": 0.9}, f="ok.png")])]
      df, report = assemble(results, SCHEMA, drop_empty=True, files=["bad.pdf", "ok.png"])
      assert df.height == 1
      assert report.errors == [("bad.pdf", "bad json")]


  def test_assemble_drop_empty_removes_all_null_rows():
      results = [ExtractResult(rows=[_row({"full_name": None, "email": None}, {})])]
      df, _ = assemble(results, SCHEMA, drop_empty=True)
      assert df.height == 0


  def test_assemble_empty_input_yields_empty_typed_frame():
      df, report = assemble([], SCHEMA, drop_empty=True)
      assert df.columns == ["full_name", "email", "_source_file", "_source_page", "_extract_confidence"]
      assert df.height == 0 and report.n_rows == 0
  ```

- [ ] **Step 2: Run to verify it fails.**
  Run: `"$PY" -m pytest tests/documents/test_assemble.py -q`  Expected: FAIL (module missing).

- [ ] **Step 3: Implement `assemble.py`.**

  ```python
  """Collapse per-document ExtractResults into one records DataFrame + a report."""
  from __future__ import annotations

  import polars as pl

  from goldenmatch.documents.types import (
      ExtractResult, IngestReport, TargetSchema,
  )

  SIDECARS = ["_source_file", "_source_page", "_extract_confidence"]


  def _empty_frame(schema: TargetSchema) -> pl.DataFrame:
      cols = {c: pl.Series(c, [], dtype=pl.Utf8) for c in schema.column_names()}
      cols["_source_file"] = pl.Series("_source_file", [], dtype=pl.Utf8)
      cols["_source_page"] = pl.Series("_source_page", [], dtype=pl.Int64)
      cols["_extract_confidence"] = pl.Series("_extract_confidence", [], dtype=pl.Float64)
      return pl.DataFrame(cols)


  def assemble(results: list[ExtractResult], schema: TargetSchema, *,
               drop_empty: bool = True,
               files: list[str] | None = None) -> tuple[pl.DataFrame, IngestReport]:
      """`files` (optional) aligns to `results` positionally so an errored doc can be named
      in the report even though it produced no rows."""
      report = IngestReport(n_files=len(results))
      cols = schema.column_names()
      records: list[dict] = []
      for idx, res in enumerate(results):
          if res.error is not None:
              fname = files[idx] if files and idx < len(files) else "<unknown>"
              report.errors.append((fname, res.error))
              continue
          for row in res.rows:
              if drop_empty and all(v is None for v in row.values.values()):
                  continue
              rec = dict(row.values)
              rec["_source_file"] = row.source_file
              rec["_source_page"] = row.source_page
              rec["_extract_confidence"] = row.row_confidence()
              records.append(rec)

      if not records:
          report.n_rows = 0
          return _empty_frame(schema), report

      df = pl.DataFrame(records)
      # enforce column order (schema cols first, then sidecars) and string typing on cols
      df = df.select([pl.col(c).cast(pl.Utf8) for c in cols] +
                     [pl.col("_source_file").cast(pl.Utf8),
                      pl.col("_source_page").cast(pl.Int64),
                      pl.col("_extract_confidence").cast(pl.Float64)])
      report.n_rows = df.height
      return df, report
  ```

- [ ] **Step 4: Run to verify pass.**
  Run: `"$PY" -m pytest tests/documents/test_assemble.py -q`  Expected: PASS (4 tests).

- [ ] **Step 5: Commit.**
  ```bash
  git add goldenmatch/documents/assemble.py tests/documents/test_assemble.py
  git -c commit.gpgsign=false commit -m "feat(documents): assemble results into records DataFrame + report"
  ```

---

## Task 5: VLM backend (OpenAI vision via injectable transport)

**Files:**
- Create: `goldenmatch/documents/vlm_backend.py`
- Test: `tests/documents/test_vlm_backend.py`

The backend never imports `openai`; it POSTs to the chat-completions endpoint via a
`transport(payload: dict) -> dict` callable (default = a real `urllib` transport). Tests pass a
fake transport that returns recorded JSON, so parsing/retry/validation are all offline.

- [ ] **Step 1: Write the failing test.** `tests/documents/test_vlm_backend.py`:

  ```python
  import json

  from goldenmatch.documents.types import Field, PageImage, TargetSchema
  from goldenmatch.documents.vlm_backend import VLMExtractor

  SCHEMA = TargetSchema([Field("full_name"), Field("email")])
  PAGES = [PageImage(b"\x89PNG\r\n\x1a\n0", 10, 10, 0)]


  def _content(rows):
      # what the model is told to return: {"records": [{"values":..., "confidence":...}]}
      return {"choices": [{"message": {"content": json.dumps({"records": rows})}}]}


  def test_extracts_single_record():
      rows = [{"values": {"full_name": "Ada", "email": "ada@x.io"},
               "confidence": {"full_name": 0.95, "email": 0.9}}]
      calls = []
      fake = lambda payload: (calls.append(payload), _content(rows))[1]
      out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake).extract(PAGES, SCHEMA)
      assert out.error is None and len(out.rows) == 1
      assert out.rows[0].values == {"full_name": "Ada", "email": "ada@x.io"}
      assert out.rows[0].confidence["full_name"] == 0.95
      # payload carried a data-URI image and the model id
      assert calls[0]["model"] == "gpt-4o"
      assert "image_url" in json.dumps(calls[0])


  def test_extracts_multiple_records_from_a_table():
      rows = [{"values": {"full_name": "Bo", "email": "bo@x.io"}, "confidence": {}},
              {"values": {"full_name": "Cy", "email": "cy@x.io"}, "confidence": {}}]
      fake = lambda payload: _content(rows)
      out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake).extract(PAGES, SCHEMA)
      assert len(out.rows) == 2
      assert out.rows[1].values["full_name"] == "Cy"
      assert out.rows[1].confidence["email"] == 0.0  # missing conf -> 0.0


  def test_unknown_keys_dropped_missing_fields_nulled():
      rows = [{"values": {"full_name": "Ada", "junk": "x"}, "confidence": {}}]
      fake = lambda payload: _content(rows)
      out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake).extract(PAGES, SCHEMA)
      assert out.rows[0].values == {"full_name": "Ada", "email": None}


  def test_malformed_json_retries_then_errors():
      calls = {"n": 0}
      def fake(payload):
          calls["n"] += 1
          return {"choices": [{"message": {"content": "not json at all"}}]}
      out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake,
                         max_retries=2).extract(PAGES, SCHEMA)
      assert out.rows == [] and out.error is not None
      assert calls["n"] == 2  # retried the configured number of times
  ```

- [ ] **Step 2: Run to verify it fails.**
  Run: `"$PY" -m pytest tests/documents/test_vlm_backend.py -q`  Expected: FAIL (module missing).

- [ ] **Step 3: Implement `vlm_backend.py`.**

  ```python
  """Cloud VLM extractor: one OpenAI vision call per document, schema-directed.

  Network I/O is a `transport(payload: dict) -> dict` callable so the class tests offline.
  """
  from __future__ import annotations

  import base64
  import json
  from collections.abc import Callable

  from goldenmatch.documents.types import (
      ExtractedRow, ExtractResult, PageImage, TargetSchema,
  )

  _ENDPOINT = "https://api.openai.com/v1/chat/completions"
  Transport = Callable[[dict], dict]


  def _urllib_transport(api_key: str) -> Transport:
      import urllib.request

      def send(payload: dict) -> dict:
          body = json.dumps(payload).encode()
          req = urllib.request.Request(
              _ENDPOINT, data=body,
              headers={"Authorization": f"Bearer {api_key}",
                       "Content-Type": "application/json"})
          with urllib.request.urlopen(req, timeout=120) as r:
              return json.loads(r.read())

      return send


  def _instruction(schema: TargetSchema) -> str:
      lines = [f'- "{f.name}" ({f.kind})' + (f": {f.hint}" if f.hint else "")
               for f in schema.fields]
      cols = ", ".join(schema.column_names())
      return (
          "Extract every record present in the attached document image(s).\n"
          "A form/card/ID is ONE record; a table/list is MANY records (one per row).\n"
          "Target fields:\n" + "\n".join(lines) + "\n\n"
          "Return ONLY a JSON object of the form:\n"
          '{"records": [{"values": {<field>: <string or null>, ...}, '
          '"confidence": {<field>: <0..1>, ...}}, ...]}\n'
          f"Use exactly these field keys: {cols}. Omit a field if absent. No prose."
      )


  class VLMExtractor:
      def __init__(self, *, api_key: str, model: str = "gpt-4o",
                   transport: Transport | None = None, max_retries: int = 2):
          self._model = model
          self._max_retries = max_retries
          self._send = transport or _urllib_transport(api_key)

      def _payload(self, pages: list[PageImage], schema: TargetSchema) -> dict:
          content: list[dict] = [{"type": "text", "text": _instruction(schema)}]
          for pg in pages:
              b64 = base64.b64encode(pg.png_bytes).decode()
              content.append({"type": "image_url",
                              "image_url": {"url": f"data:image/png;base64,{b64}"}})
          return {"model": self._model, "temperature": 0, "max_tokens": 2000,
                  "messages": [{"role": "user", "content": content}]}

      def extract(self, pages: list[PageImage], schema: TargetSchema) -> ExtractResult:
          payload = self._payload(pages, schema)
          src = pages[0].index if pages else None
          fname = ""  # assemble tags the real filename; backend only knows the page
          last_err = "no response"
          for _ in range(self._max_retries):
              try:
                  resp = self._send(payload)
                  text = resp["choices"][0]["message"]["content"]
                  data = json.loads(_strip_fence(text))
                  rows = [
                      ExtractedRow.from_partial(
                          rec.get("values", {}), rec.get("confidence", {}), schema,
                          source_file=fname, source_page=src)
                      for rec in data.get("records", [])
                  ]
                  return ExtractResult(rows=rows)
              except (KeyError, ValueError, TypeError) as e:
                  last_err = f"{type(e).__name__}: {e}"
          return ExtractResult(rows=[], error=last_err)


  def _strip_fence(text: str) -> str:
      t = text.strip()
      if t.startswith("```"):
          t = t.split("\n", 1)[1] if "\n" in t else t
          if t.endswith("```"):
              t = t[: -3]
      return t.strip()
  ```

  Note: `source_file` is set by `assemble` (it knows the path); the backend leaves it `""`.
  Update `assemble` is NOT needed — `ingest_documents` (Task 6) stamps the filename onto rows
  before assembling. See Task 6 Step 3.

- [ ] **Step 4: Run to verify pass.**
  Run: `"$PY" -m pytest tests/documents/test_vlm_backend.py -q`  Expected: PASS (4 tests).

- [ ] **Step 5: Commit.**
  ```bash
  git add goldenmatch/documents/vlm_backend.py tests/documents/test_vlm_backend.py
  git -c commit.gpgsign=false commit -m "feat(documents): VLM extractor (OpenAI vision, injectable transport)"
  ```

---

## Task 6: Config + public `ingest_documents`

**Files:**
- Create: `goldenmatch/documents/config.py`
- Modify: `goldenmatch/documents/__init__.py`
- Test: `tests/documents/test_ingest.py`

- [ ] **Step 1: Write the failing test.** `tests/documents/test_ingest.py`:

  ```python
  import fitz
  import pytest
  from PIL import Image

  from goldenmatch.documents import ingest_documents
  from goldenmatch.documents.extractor import FakeExtractor
  from goldenmatch.documents.types import (
      ExtractedRow, ExtractResult, Field, TargetSchema,
  )

  SCHEMA = TargetSchema([Field("full_name"), Field("email")])


  def _img(path):
      Image.new("RGB", (60, 40), "white").save(path)


  def _rows(schema, pairs, f, pg=0):
      return [ExtractedRow.from_partial(v, c, schema, source_file="", source_page=pg)
              for (v, c) in pairs]


  def test_ingest_stamps_filenames_and_returns_frame(tmp_path):
      a, b = tmp_path / "a.png", tmp_path / "b.png"
      _img(a); _img(b)
      fake = FakeExtractor([
          ExtractResult(rows=_rows(SCHEMA, [({"full_name": "Ada", "email": "ada@x.io"},
                                             {"full_name": 0.9, "email": 0.9})], "a")),
          ExtractResult(rows=_rows(SCHEMA, [({"full_name": "Bo", "email": "bo@x.io"},
                                             {"full_name": 0.8, "email": 0.8})], "b")),
      ])
      df = ingest_documents([a, b], SCHEMA, extractor=fake)
      assert df.height == 2
      assert set(df["_source_file"].to_list()) == {str(a), str(b)}
      assert df.columns[:2] == ["full_name", "email"]


  def test_return_report_returns_tuple(tmp_path):
      a = tmp_path / "a.png"; _img(a)
      fake = FakeExtractor([ExtractResult(rows=_rows(SCHEMA,
                            [({"full_name": "Ada", "email": "a@x.io"}, {})], "a"))])
      df, report = ingest_documents([a], SCHEMA, extractor=fake, return_report=True)
      assert df.height == 1 and report.n_files == 1 and report.n_rows == 1


  def test_missing_key_for_vlm_backend_fails_fast(tmp_path, monkeypatch):
      a = tmp_path / "a.png"; _img(a)
      monkeypatch.delenv("OPENAI_API_KEY_PERSONAL", raising=False)
      monkeypatch.delenv("OPENAI_API_KEY", raising=False)
      with pytest.raises(ValueError, match="API key"):
          ingest_documents([a], SCHEMA, backend="vlm")


  def test_unknown_backend_fails_fast(tmp_path):
      a = tmp_path / "a.png"; _img(a)
      with pytest.raises(ValueError, match="unknown backend"):
          ingest_documents([a], SCHEMA, backend="nope")
  ```

- [ ] **Step 2: Run to verify it fails.**
  Run: `"$PY" -m pytest tests/documents/test_ingest.py -q`  Expected: FAIL (import error).

- [ ] **Step 3: Implement `config.py` then `ingest_documents`.**

  `goldenmatch/documents/config.py`:
  ```python
  """Backend resolution + fail-fast validation for document ingest."""
  from __future__ import annotations

  import os

  from goldenmatch.documents.extractor import Extractor
  from goldenmatch.documents.vlm_backend import VLMExtractor

  # Personal key first (see the openai-api-key memory: OPENAI_API_KEY may be work-scoped).
  _KEY_ENV_ORDER = ("OPENAI_API_KEY_PERSONAL", "OPENAI_API_KEY")


  def resolve_extractor(backend: str, model: str) -> Extractor:
      if backend != "vlm":
          raise ValueError(f"unknown backend: {backend!r} (Phase 1 supports 'vlm')")
      key = next((os.environ[e] for e in _KEY_ENV_ORDER if os.environ.get(e)), None)
      if not key:
          raise ValueError(
              "no OpenAI API key found; set OPENAI_API_KEY_PERSONAL "
              "(or OPENAI_API_KEY) for the 'vlm' backend")
      return VLMExtractor(api_key=key, model=model)
  ```

  Replace `goldenmatch/documents/__init__.py` with:
  ```python
  """Document/image ingest: turn PDFs/images into a records DataFrame for GoldenMatch."""
  from __future__ import annotations

  from dataclasses import replace
  from pathlib import Path

  import polars as pl

  from goldenmatch.documents.assemble import assemble
  from goldenmatch.documents.config import resolve_extractor
  from goldenmatch.documents.extractor import Extractor
  from goldenmatch.documents.loader import load_pages
  from goldenmatch.documents.types import (
      ExtractResult, Field, IngestReport, TargetSchema,
  )

  __all__ = ["ingest_documents", "TargetSchema", "Field", "IngestReport"]


  def ingest_documents(paths, schema: TargetSchema, *, backend: str = "vlm",
                       model: str = "gpt-4o", extractor: Extractor | None = None,
                       drop_empty: bool = True, return_report: bool = False):
      """Extract records from a pile of documents into one Polars DataFrame.

      Hand the result to the ER pipeline as:
          dedupe_df(df, exclude_columns=["_source_file", "_source_page", "_extract_confidence"])
      so the provenance/confidence sidecars are never treated as match fields.

      `extractor` overrides `backend`/`model` (used for tests / custom backends).
      Returns the DataFrame, or `(df, IngestReport)` when `return_report=True`.
      """
      ex = extractor or resolve_extractor(backend, model)
      files = [str(Path(p)) for p in paths]
      results: list[ExtractResult] = []
      for fpath in files:
          try:
              pages = load_pages(fpath)
              res = ex.extract(pages, schema)
          except Exception as e:  # loader/backend hard failure -> recorded, batch continues
              res = ExtractResult(rows=[], error=f"{type(e).__name__}: {e}")
          # stamp the real filename onto each row (backend only knew the page index)
          res = ExtractResult(
              rows=[replace(r, source_file=fpath) for r in res.rows], error=res.error)
          results.append(res)

      df, report = assemble(results, schema, drop_empty=drop_empty, files=files)
      return (df, report) if return_report else df
  ```

- [ ] **Step 4: Run to verify pass.**
  Run: `"$PY" -m pytest tests/documents/test_ingest.py -q`  Expected: PASS (4 tests).

- [ ] **Step 5: Commit.**
  ```bash
  git add goldenmatch/documents/config.py goldenmatch/documents/__init__.py tests/documents/test_ingest.py
  git -c commit.gpgsign=false commit -m "feat(documents): config + ingest_documents public API"
  ```

---

## Task 7: End-to-end seam test (→ real `dedupe_df`)

**Files:**
- Test: `tests/documents/test_e2e.py`

Proves the whole point: fixtures → `ingest_documents` (FakeExtractor) → DataFrame →
`dedupe_df(df, exclude_columns=[...])` finds the duplicate.

- [ ] **Step 1: Write the failing test.** `tests/documents/test_e2e.py`:

  ```python
  from PIL import Image

  from goldenmatch import dedupe_df
  from goldenmatch.documents import ingest_documents
  from goldenmatch.documents.assemble import SIDECARS
  from goldenmatch.documents.extractor import FakeExtractor
  from goldenmatch.documents.types import (
      ExtractedRow, ExtractResult, Field, TargetSchema,
  )

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
          exclude_columns=SIDECARS,
          confidence_required=False,
          allow_red_config=True,
      )
      # the two Ada rows collapse to one cluster; Grace stays separate
      # (exact-on-email guarantees the match regardless of auto-config)
      assert result is not None
  ```

  NOTE for the implementer: inspect the real `dedupe_df` return type (`packages/python/
  goldenmatch/goldenmatch/_api.py:400`) and assert on its actual cluster/summary shape — e.g.
  that the two `ada@x.io` rows share a cluster id. Replace the placeholder `assert result is
  not None` with a concrete cluster-count assertion once you see the return object. Keep
  `exact=["email"]` so the assertion does not depend on fuzzy auto-config.

- [ ] **Step 2: Run to verify it fails.**
  Run: `"$PY" -m pytest tests/documents/test_e2e.py -q`  Expected: FAIL (assertion or import).

- [ ] **Step 3: Make it pass.** Adjust the final assertion to the real `dedupe_df` return
  shape (read `_api.py:400` and the objects it returns). Confirm the two `ada@x.io` rows land
  in one cluster and Grace is separate. Do NOT weaken the test to `is not None` — assert the
  cluster outcome.

- [ ] **Step 4: Run the full documents suite.**
  Run: `"$PY" -m pytest tests/documents -q`  Expected: PASS (all modules).

- [ ] **Step 5: Commit.**
  ```bash
  git add tests/documents/test_e2e.py
  git -c commit.gpgsign=false commit -m "test(documents): e2e seam ingest_documents -> dedupe_df"
  ```

---

## Task 8: README example + gated live smoke

**Files:**
- Create: `goldenmatch/documents/README.md`
- Test: `tests/documents/test_live_smoke.py`

- [ ] **Step 1: Write the live smoke (skipped without a key).** `tests/documents/test_live_smoke.py`:

  ```python
  import os

  import pytest
  from PIL import Image, ImageDraw

  from goldenmatch.documents import ingest_documents
  from goldenmatch.documents.types import Field, TargetSchema

  pytestmark = pytest.mark.skipif(
      not os.environ.get("OPENAI_API_KEY_PERSONAL"),
      reason="live VLM smoke; set OPENAI_API_KEY_PERSONAL to run")


  def test_live_extracts_a_synthetic_card(tmp_path):
      p = tmp_path / "card.png"
      img = Image.new("RGB", (400, 200), "white")
      d = ImageDraw.Draw(img)
      d.text((20, 40), "Ada Lovelace", fill="black")
      d.text((20, 80), "ada@analytical.io", fill="black")
      img.save(p)
      schema = TargetSchema([Field("full_name"), Field("email", kind="email")])
      df = ingest_documents([p], schema, backend="vlm", model="gpt-4o")
      assert df.height >= 1
      assert "ada@analytical.io" in " ".join(df["email"].to_list())
  ```

- [ ] **Step 2: Run it (skips without key).**
  Run: `"$PY" -m pytest tests/documents/test_live_smoke.py -q`  Expected: SKIPPED.
  Optional live check (uses your key via Infisical):
  `infisical.cmd run --projectId a99885f0-c5af-4ae1-9dc8-255cc60aa129 --env dev --path / -- "$PY" -m pytest tests/documents/test_live_smoke.py -q`
  Expected: PASS (1 real VLM call). See the openai-api-key memory for scope.

- [ ] **Step 3: Write `goldenmatch/documents/README.md`** — a short usage example:

  ````markdown
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
  ````

- [ ] **Step 4: Full suite + lint.**
  Run: `"$PY" -m pytest tests/documents -q` (live smoke SKIPPED) and
  `"$PY" -m ruff check goldenmatch/documents tests/documents`  Expected: PASS / clean.

- [ ] **Step 5: Commit.**
  ```bash
  git add goldenmatch/documents/README.md tests/documents/test_live_smoke.py
  git -c commit.gpgsign=false commit -m "docs(documents): README example + gated live VLM smoke"
  ```

---

## Done-when

- `tests/documents/` all green (live smoke skipped without a key), ruff clean.
- `ingest_documents(paths, schema)` returns a DataFrame whose schema columns + 3 sidecars feed
  `dedupe_df(df, exclude_columns=[...])`, proven by `test_e2e.py`.
- `pip install "goldenmatch[documents]"` pulls `pymupdf` + `Pillow`.
- Deferred to Phase 2/3 (not in this plan): MCP tool, CLI, local OCR backend, review-queue
  integration, bulk/batched concurrency.
