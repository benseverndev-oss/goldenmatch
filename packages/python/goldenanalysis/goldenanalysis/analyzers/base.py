"""The ``Analyzer`` protocol.

An analyzer is anything with an ``info`` descriptor and a ``run`` method. Concrete
analyzers are discovered by the registry via the ``goldenanalysis.analyzers``
entry-point group.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from goldenanalysis.models import AnalyzerInfo, AnalyzerInput, AnalyzerResult


@runtime_checkable
class Analyzer(Protocol):
    info: AnalyzerInfo

    def run(self, inp: AnalyzerInput) -> AnalyzerResult: ...
