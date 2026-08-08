"""goldenphonetic: fast, byte-identical-to-jellyfish phonetic encoders.

Wraps the pyo3-free ``goldenphonetic-core`` Rust crate. Every encoder is
byte-identical to the corresponding function in the Python ``jellyfish`` package
(>= 1.0), with zero runtime dependencies:

    >>> import goldenphonetic as gp
    >>> gp.soundex("Robert")
    'R163'
    >>> gp.metaphone("Thompson")
    '0MPSN'
    >>> gp.nysiis("Catherine")
    'CATARAN'
    >>> gp.match_rating_codex("Byrne")
    'BYRN'
    >>> gp.match_rating_comparison("Byrne", "Boern")
    True

Error semantics mirror ``jellyfish`` exactly: ``match_rating_codex`` raises
``ValueError`` on non-alphabetic input, while ``match_rating_comparison`` never
raises — it returns ``None`` when the two codices can't be compared (a codex-length
difference of 3 or more, or either input rejected as non-alphabetic).

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``goldenphonetic/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(goldenphonetic.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/rust/extensions/goldenphonetic-py -- full docs.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: this directory is a compiled artefact. Which surface
owns a computation, which fallback is deliberate, and which parity is
contract-tested are all *decisions* -- documented, and not recoverable by
reading the binary or the Python fallback beside it.
"""

from goldenphonetic._goldenphonetic import (
    __version__,
    match_rating_codex,
    match_rating_comparison,
    metaphone,
    nysiis,
    soundex,
)

__all__ = [
    "__version__",
    "match_rating_codex",
    "match_rating_comparison",
    "metaphone",
    "nysiis",
    "soundex",
]
