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
