"""SP-B1 substrate config surface.

A `SubstrateConfig` is a frozen, validated value object over the goldengraph substrate levers.
`apply()` materializes it into the `GOLDENGRAPH_*` process env around a build (working WITH the
existing call-time env reads); `for_profile()` picks a sane default from cheap corpus signals.

Pure: stdlib only. MUST NOT import .llm / .embed / .chunk_extract / .ingest -- keep the LLM/build
path out of this module. See docs/superpowers/specs/2026-07-02-substrate-config-surface-design.md.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass

_XDOC_KEYS = ("", "name", "name_ci", "name_ci_type")
_EXTRACTORS = ("api", "rebel", "gliner")

#: Every GOLDENGRAPH_* var this config OWNS: 12 field vars + the SCHEMA_DISCOVER leak-guard. Ambient
#: SCHEMA_DISCOVER=1 makes ingest_corpus discover schema and ignore RELATION_VOCAB, silently defeating
#: the has_known_schema rule -- so the config forces it off. (The ~18 other substrate GOLDENGRAPH_*
#: vars are NOT managed; none of them defeats a for_profile rule. See the spec's Residual note.)
MANAGED_ENV_VARS: tuple[str, ...] = (
    "GOLDENGRAPH_XDOC_KEY",
    "GOLDENGRAPH_CHUNK_EXTRACT",
    "GOLDENGRAPH_CHUNK_SENTENCES",
    "GOLDENGRAPH_CHUNK_OVERLAP",
    "GOLDENGRAPH_ENTITY_TYPE_CANON",
    "GOLDENGRAPH_ENTITY_TYPE_VOCAB",
    "GOLDENGRAPH_SCHEMA_CANON",
    "GOLDENGRAPH_RELATION_VOCAB",
    "GOLDENGRAPH_EXTRACTOR",
    "GOLDENGRAPH_RELATION_REPROMPT",
    "GOLDENGRAPH_REBEL_FUSE",
    "GOLDENGRAPH_EXTRACT_RECALL",
    "GOLDENGRAPH_SCHEMA_DISCOVER",  # leak-guard, not a field
)


@dataclass(frozen=True)
class SubstrateConfig:
    """Immutable substrate-builder configuration. Defaults reproduce today's engine behavior (a
    default config materializes to a no-op env). Refuted levers are present but default off and are
    never selected by `for_profile`."""
    xdoc_key: str = ""                       # "" | name | name_ci | name_ci_type  ("" = (name,typ))
    chunk_extract: bool = False
    chunk_sentences: int = 6
    chunk_overlap: int = 2
    entity_type_canon: bool = False
    entity_type_vocab: tuple[str, ...] = ()  # () = engine default 4-type
    schema_canon: bool = False
    relation_vocab: tuple[str, ...] = ()
    extractor: str = "api"                   # api | rebel | gliner
    relation_reprompt: bool = False          # REFUTED (#1360)
    rebel_fuse: bool = False                 # REFUTED (#1357)
    extract_recall: bool = False             # REFUTED (#1348)

    def __post_init__(self) -> None:
        if self.xdoc_key not in _XDOC_KEYS:
            raise ValueError(f"xdoc_key must be one of {_XDOC_KEYS}, got {self.xdoc_key!r}")
        if self.extractor not in _EXTRACTORS:
            raise ValueError(f"extractor must be one of {_EXTRACTORS}, got {self.extractor!r}")
        if self.chunk_sentences < 1:
            raise ValueError(f"chunk_sentences must be >= 1, got {self.chunk_sentences}")
        if not (0 <= self.chunk_overlap < self.chunk_sentences):
            raise ValueError(
                f"chunk_overlap must be in [0, chunk_sentences), got {self.chunk_overlap} "
                f"(chunk_sentences={self.chunk_sentences})"
            )

    def to_env(self) -> dict[str, str]:
        """Total map over MANAGED_ENV_VARS: applying it fully determines those keys (leak-proof over
        the managed set). Bool -> '1'/'0'; empty xdoc_key/vocabs -> ''; tuples -> csv; SCHEMA_DISCOVER
        forced '0'."""
        def b(x: bool) -> str:
            return "1" if x else "0"

        return {
            "GOLDENGRAPH_XDOC_KEY": self.xdoc_key,
            "GOLDENGRAPH_CHUNK_EXTRACT": b(self.chunk_extract),
            "GOLDENGRAPH_CHUNK_SENTENCES": str(self.chunk_sentences),
            "GOLDENGRAPH_CHUNK_OVERLAP": str(self.chunk_overlap),
            "GOLDENGRAPH_ENTITY_TYPE_CANON": b(self.entity_type_canon),
            "GOLDENGRAPH_ENTITY_TYPE_VOCAB": ",".join(self.entity_type_vocab),
            "GOLDENGRAPH_SCHEMA_CANON": b(self.schema_canon),
            "GOLDENGRAPH_RELATION_VOCAB": ",".join(self.relation_vocab),
            "GOLDENGRAPH_EXTRACTOR": self.extractor,
            "GOLDENGRAPH_RELATION_REPROMPT": b(self.relation_reprompt),
            "GOLDENGRAPH_REBEL_FUSE": b(self.rebel_fuse),
            "GOLDENGRAPH_EXTRACT_RECALL": b(self.extract_recall),
            "GOLDENGRAPH_SCHEMA_DISCOVER": "0",
        }

    @contextmanager
    def apply(self):
        """Set the MANAGED_ENV_VARS from `to_env()` for the duration, then restore the prior process
        env exactly (delete keys that were absent, restore prior values). Env is process-global: set
        the config ONCE before ingest_corpus fans per-doc work out to threads (which inherit the env).
        NOT safe for two different configs building concurrently in one process."""
        env = self.to_env()
        sentinel = object()
        prior = {k: os.environ.get(k, sentinel) for k in MANAGED_ENV_VARS}
        try:
            os.environ.update(env)
            yield
        finally:
            for k, v in prior.items():
                if v is sentinel:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


@dataclass(frozen=True)
class CorpusProfile:
    """Cheap raw-text signals (no LLM, no build) that drive `for_profile`."""
    n_docs: int
    mean_sentences_per_doc: float
    mean_chars_per_doc: float


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _count_sentences(text: str) -> int:
    """Sentence count via a local [.!?]-boundary split (config.py stays free of the LLM path, so we do
    NOT reuse chunk_extract's splitter -- importing it drags in .llm)."""
    t = text.strip()
    if not t:
        return 0
    return len([s for s in _SENT_SPLIT.split(t) if s.strip()])


def profile_corpus(docs) -> CorpusProfile:
    """Derive a CorpusProfile from raw document texts. Empty corpus -> zeros."""
    docs = list(docs)
    n = len(docs)
    if n == 0:
        return CorpusProfile(n_docs=0, mean_sentences_per_doc=0.0, mean_chars_per_doc=0.0)
    total_sents = sum(_count_sentences(d) for d in docs)
    total_chars = sum(len(d) for d in docs)
    return CorpusProfile(
        n_docs=n,
        mean_sentences_per_doc=total_sents / n,
        mean_chars_per_doc=total_chars / n,
    )


#: Dense multi-sentence docs benefit from chunked extraction; short docs get a no-op + 4-10x cost.
#: Threshold from the wiki finding (leads ~20 sentences, chunking won at (6,2), #1350). Env-overridable.
CHUNK_MIN_SENTENCES: int = int(os.environ.get("GOLDENGRAPH_AUTOCFG_CHUNK_MIN_SENTENCES", "8") or "8")


def for_profile(profile: CorpusProfile, *, has_known_schema: bool = False,
                expect_homographs: bool = False, relation_vocab: tuple[str, ...] = ()) -> SubstrateConfig:
    """Deterministic rule table over a CorpusProfile -> a sane-default SubstrateConfig. Encodes the
    arc's MEASURED findings. Precedence: base name_ci -> homograph override -> chunk + schema (orthogonal).
    Refuted levers are never selected."""
    # base: name_ci is the near-universal relational win (L0/L1/L2, #1331/#1340/#1341)
    xdoc_key = "name_ci"
    entity_type_canon = False
    # homograph override: name_ci_type + type-canon (homograph-safe, ~0.06 recall cost, #1335/#1336)
    if expect_homographs:
        xdoc_key = "name_ci_type"
        entity_type_canon = True
    # chunking: only on dense multi-sentence docs (#1350)
    chunk = profile.mean_sentences_per_doc >= CHUNK_MIN_SENTENCES
    # known schema: closed-vocab predicate canonicalization (SCHEMA_CANON arc)
    schema_canon = bool(has_known_schema)
    vocab = tuple(relation_vocab) if has_known_schema else ()
    return SubstrateConfig(
        xdoc_key=xdoc_key,
        chunk_extract=chunk,
        entity_type_canon=entity_type_canon,
        schema_canon=schema_canon,
        relation_vocab=vocab,
    )
