"""NCVR (North Carolina Voter Registration) eval-only stub loader (Task 7 of
the multi-source ER data pipeline). NCVR is a real public-record dataset, but
the actual fetch/parse implementation is deferred to sub-project 3 -- this
stub exists purely so its `sources.yaml` entry validates and shows up in the
model-card manifest now. CPU/box-safe: stdlib only, no torch/transformers/
network (there is no fetch path to guard yet -- it just raises).
"""

from __future__ import annotations

from typing import Any

from sources.base import Row

_NOT_IMPLEMENTED_MSG = "NCVR eval loader is deferred to sub-project 3"


class NcvrSource:
    """PairSource stub for the NCVR public-record voter-registration
    dataset. `eval_only = True` -- this source must never enter a training
    corpus, even once implemented. `splits()`/`fetch()` both raise
    NotImplementedError; the real loader lands in sub-project 3.
    """

    eval_only = True
    license = "public-record"
    attribution = "NC State Board of Elections (public record)"

    def __init__(self, name: str = "ncvr", **kwargs: Any) -> None:
        self.name = name
        self.kwargs = kwargs

    def splits(self) -> dict[str, list[Row]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def fetch(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
