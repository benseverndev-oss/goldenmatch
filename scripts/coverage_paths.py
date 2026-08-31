"""Canonical module names for coverage reports.

Coverage emits a different `filename` shape depending on how the report was
produced -- relative to the package (`core/scorer.py`), relative to the repo
root (`packages/python/goldenmatch/goldenmatch/core/scorer.py`), or absolute
(`/home/runner/work/.../goldenmatch/core/scorer.py`).

This mismatch is not hypothetical: the per-module floor table was written with
`goldenmatch/`-prefixed keys, CI's report used a different shape, and EVERY
floor silently missed. The gate warned twelve times and then printed
"All 15 module floors met" -- so from the day it was added it evaluated nothing.
Normalizing both sides is what makes the table match whatever shape CI emits.
"""
from __future__ import annotations

PKG = "goldenmatch/"


def normalize(filename: str) -> str:
    """Return a `goldenmatch/...`-rooted module path.

    Takes the segment from the LAST `goldenmatch/` (the repo nests
    `packages/python/goldenmatch/goldenmatch/`, so the last one is the package),
    or prepends it when the path is already package-relative.
    """
    name = (filename or "").replace("\\", "/")
    idx = name.rfind(PKG)
    if idx != -1:
        return name[idx:]
    return PKG + name.lstrip("./")
