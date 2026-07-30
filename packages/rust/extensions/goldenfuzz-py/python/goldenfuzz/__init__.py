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

The ``fuzz.*`` composite family is also byte-identical to ``rapidfuzz.fuzz`` and
returns a similarity in ``[0, 100]``:

    >>> gf.WRatio("this is a test", "this is a test!")
    95.0
    >>> gf.token_sort_ratio("fuzzy wuzzy was a bear", "wuzzy fuzzy was a bear")
    100.0

``WRatio`` / ``QRatio`` are exposed under both the rapidfuzz spelling and a
snake_case alias (``w_ratio`` / ``q_ratio``).
"""

from goldenfuzz._goldenfuzz import (
    BatchComparator,
    __version__,
    cdist,
    extract,
    indel,
    jaro_winkler,
    levenshtein,
    partial_ratio,
    partial_token_ratio,
    partial_token_set_ratio,
    partial_token_sort_ratio,
    q_ratio,
    ratio,
    token_ratio,
    token_set_ratio,
    token_sort_ratio,
    w_ratio,
)

# rapidfuzz spellings for the composite ratios (WRatio / QRatio).
WRatio = w_ratio
QRatio = q_ratio

__all__ = [
    "BatchComparator",
    "QRatio",
    "WRatio",
    "__version__",
    "cdist",
    "extract",
    "indel",
    "jaro_winkler",
    "levenshtein",
    "partial_ratio",
    "partial_token_ratio",
    "partial_token_set_ratio",
    "partial_token_sort_ratio",
    "q_ratio",
    "ratio",
    "token_ratio",
    "token_set_ratio",
    "token_sort_ratio",
    "w_ratio",
]
