"""goldenfuzz: fast, byte-identical-to-rapidfuzz fuzzy-string scorers.

Wraps the pyo3-free ``goldenfuzz-core`` Rust crate. Per-pair scorers are
byte-identical to rapidfuzz on jaro-winkler / levenshtein / indel (and faster on
short strings); ``extract`` / ``cdist`` / :class:`BatchComparator` provide the
one-vs-many API (the query bitmap is built once and reused across choices).

    >>> import goldenfuzz as gf
    >>> gf.jaro_winkler("jonathan", "jonathon")
    0.95...
    >>> gf.extract("jonathan smith", ["jon smith", "jane doe", "jonathan smith"],
    ...            scorer="jaro_winkler", limit=2)
    [(2, 1.0), (0, 0.9...)]
    >>> bc = gf.BatchComparator("acme corporation")
    >>> bc.jaro_winkler("acme corp")
    0.9...

Scorers accept the names ``jaro_winkler`` | ``levenshtein`` | ``indel`` (all
return normalized similarity in ``[0, 1]``).
"""

from goldenfuzz._goldenfuzz import (
    BatchComparator,
    __version__,
    cdist,
    extract,
    indel,
    jaro_winkler,
    levenshtein,
)

__all__ = [
    "BatchComparator",
    "__version__",
    "cdist",
    "extract",
    "indel",
    "jaro_winkler",
    "levenshtein",
]
