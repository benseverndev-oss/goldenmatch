"""The extractor seams: Protocols plus scripted fakes for tests.

`Extractor` is the flat (schema-directed) seam. The three structured seams --
`Classifier`, `TemplateExtractor`, `FallbackExtractor` -- back the per-doctype
template flow: classify a doc, extract against a template, or fall back to the
generic suggest-then-extract path. Each has a scripted Fake so the whole flow is
offline-testable without a live VLM.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from goldenmatch.documents.types import (
    ClassifyResult,
    DocTemplate,
    ExtractResult,
    PageImage,
    StructuredResult,
    TargetSchema,
)


@runtime_checkable
class Extractor(Protocol):
    def extract(self, pages: list[PageImage], schema: TargetSchema) -> ExtractResult: ...


@runtime_checkable
class Classifier(Protocol):
    def classify(self, pages: list[PageImage]) -> ClassifyResult: ...


@runtime_checkable
class TemplateExtractor(Protocol):
    def extract_structured(
        self, pages: list[PageImage], template: DocTemplate
    ) -> StructuredResult: ...


@runtime_checkable
class FallbackExtractor(Protocol):
    # the "generic" path: suggest a schema from the doc, then extract against it
    # (2 VLM calls).
    def suggest_and_extract(self, pages: list[PageImage]) -> ExtractResult: ...


class FakeExtractor:
    """Returns pre-scripted results in order; for pipeline/e2e tests (no network)."""

    def __init__(self, scripted: list[ExtractResult]):
        self._scripted = list(scripted)
        self._i = 0

    def extract(self, pages: list[PageImage], schema: TargetSchema) -> ExtractResult:
        r = self._scripted[self._i]
        self._i += 1
        return r


class FakeClassifier:
    """Scripted `Classifier`; exposes `.calls` so tests can assert the classifier
    was (or was NOT, on the override path) invoked."""

    def __init__(self, scripted: list[ClassifyResult]):
        self._scripted = list(scripted)
        self._i = 0
        self.calls = 0

    def classify(self, pages: list[PageImage]) -> ClassifyResult:
        self.calls += 1
        r = self._scripted[self._i]
        self._i += 1
        return r


class FakeTemplateExtractor:
    """Scripted `TemplateExtractor`; exposes `.calls`."""

    def __init__(self, scripted: list[StructuredResult]):
        self._scripted = list(scripted)
        self._i = 0
        self.calls = 0

    def extract_structured(
        self, pages: list[PageImage], template: DocTemplate
    ) -> StructuredResult:
        self.calls += 1
        r = self._scripted[self._i]
        self._i += 1
        return r


class FakeFallbackExtractor:
    """Scripted `FallbackExtractor`; exposes `.calls`. The clean injectable that
    makes the generic-fallback path offline-testable in Task 6."""

    def __init__(self, scripted: list[ExtractResult]):
        self._scripted = list(scripted)
        self._i = 0
        self.calls = 0

    def suggest_and_extract(self, pages: list[PageImage]) -> ExtractResult:
        self.calls += 1
        r = self._scripted[self._i]
        self._i += 1
        return r
