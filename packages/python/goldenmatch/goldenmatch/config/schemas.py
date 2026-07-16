"""Pydantic models for GoldenMatch configuration validation."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

if TYPE_CHECKING:
    from goldenmatch.core.autoconfig_verify import PreflightReport

# ── Valid enums ─────────────────────────────────────────────────────────────

VALID_SIMPLE_TRANSFORMS = frozenset({
    "lowercase", "uppercase", "strip", "strip_all", "soundex", "metaphone",
    "digits_only", "alpha_only", "normalize_whitespace",
    "token_sort", "first_token", "last_token",
})

VALID_SCORERS = frozenset({
    "exact", "jaro_winkler", "levenshtein", "token_sort", "soundex_match",
    "embedding", "record_embedding", "ensemble",
    "dice", "jaccard", "qgram",
    # Hamming similarity over a hex perceptual hash (image pHash) -- the
    # multimodal-ER crawl-tier media-as-evidence comparator (ADR 0022).
    "phash",
    # Offset-aligned bit-error-rate over a hex audio fingerprint (ADR 0022).
    "audio_fp",
    # Rotation-aligned similarity over a hex radial-variance profile -- the
    # geometric (rotation/crop-aware) image comparator, vs photometric `phash`
    # (ADR 0022 finding 1).
    "radial",
    # Free deterministic equality scorers (1.0/0.0): initialism collapse
    # ("IBM" <-> "International Business Machines") and alias canonicalization
    # ("Acme Inc" <-> "Acme Incorporated", "Bob" <-> "Robert").
    "initialism_match", "alias_match",
})

VALID_STRATEGIES = frozenset({
    "most_recent", "source_priority", "most_complete", "majority_vote", "first_non_null",
    # v1.18 additions (#golden-strategies)
    "longest_value", "unanimous_or_null", "confidence_majority",
})

_SUBSTRING_RE = re.compile(r"^substring:\d+:\d+$")
# v1.18.1: custom: prefix for plugin-backed golden strategies.
_CUSTOM_STRATEGY_RE = re.compile(r"^custom:[a-z_][a-z0-9_]*$")
_QGRAM_RE = re.compile(r"^qgram:\d+$")
_BLOOM_FILTER_RE = re.compile(r"^bloom_filter:\d+:\d+:\d+$")


# ── FieldTransform ──────────────────────────────────────────────────────────


class FieldTransform(BaseModel):
    transform: str

    @model_validator(mode="after")
    def _validate_transform(self) -> FieldTransform:
        t = self.transform
        if t in VALID_SIMPLE_TRANSFORMS:
            return self
        if _SUBSTRING_RE.match(t):
            return self
        if _QGRAM_RE.match(t):
            return self
        if t == "bloom_filter" or _BLOOM_FILTER_RE.match(t):
            return self
        # Plugin transform fallback — mirrors the MatchkeyField scorer
        # validator (line ~104). A registered plugin transform is a valid
        # transform name even if it isn't in VALID_SIMPLE_TRANSFORMS.
        from goldenmatch.plugins.registry import PluginRegistry

        if PluginRegistry.instance().has_transform(t):
            return self
        raise ValueError(
            f"Invalid transform '{t}'. Must be one of {sorted(VALID_SIMPLE_TRANSFORMS)}, "
            f"a registered plugin transform, or 'substring:<start>:<end>'."
        )


# ── MatchkeyField ──────────────────────────────────────────────────────────


class MatchkeyField(BaseModel):
    field: str | None = None
    column: str | None = None
    transforms: list[str] = Field(default_factory=list)
    scorer: str | None = None
    weight: float | None = None
    model: str | None = None  # for embedding scorer
    columns: list[str] | None = None  # for record_embedding scorer
    column_weights: dict[str, float] | None = None  # per-field weights for record_embedding
    levels: int = Field(default=2, ge=2)  # comparison levels for probabilistic: 2=agree/disagree, 3=agree/partial/disagree
    partial_threshold: float = 0.8  # score >= this = partial agree (when levels=3)
    # Probabilistic-only: term-frequency (Winkler) weight adjustment. When True,
    # an exact agreement on a *rare* value carries more match weight than on a
    # *common* one (matching on "Zelinski" is stronger evidence than on
    # "Smith"). Off by default — only meaningful for skewed-frequency
    # categorical fields (names, cities). Applied by the vectorized FS scorer
    # using per-value frequencies computed at EM-train time.
    tf_adjustment: bool = False
    # #1207 PR2a: per-dataset value->relative-frequency table for
    # name_freq_weighted_jw; when present the scorer downweights agreements on
    # high-frequency values across the whole JW range (data-driven), else falls
    # back to static census IDF in the borderline zone.
    tf_freqs: dict[str, float] | None = None
    # Workbench-only hint: which kind of MatchkeyConfig to wrap this field
    # in when /preview / /run translate the flat row list into engine
    # MatchkeyConfigs. Optional + None-default so engine-internal callers
    # that build MatchkeyField directly remain unaffected; preview's
    # _build_config falls back to its scorer-based heuristic when absent.
    type: Literal["exact", "weighted", "probabilistic"] | None = None
    # Probabilistic-only: EM iterations cap. Mirrors MatchkeyConfig.em_iterations
    # so the workbench can tune training stability without surfacing the full
    # MatchkeyConfig shape. Read by _build_config when type == "probabilistic".
    # `None` (not 20) is the default so `model_dump(exclude_none=True)` doesn't
    # leak the value into saved YAML for non-probabilistic matchkeys; the
    # workbench → engine translation in web/preview.py coerces None → 20.
    em_iterations: int | None = None
    # N-level custom banding (Splink-converter Stage 1). Descending similarity
    # cutoffs; level index = count of satisfied thresholds (0 = disagree,
    # levels-1 = top agree). None => legacy banding (partial_threshold for
    # 2/3 levels, even k/N spacing for N>3). Length must be levels-1.
    level_thresholds: list[float] | None = None

    @model_validator(mode="after")
    def _resolve_field_column(self) -> MatchkeyField:
        # level_thresholds validation runs FIRST (before the record_embedding
        # early return below) so record_embedding fields don't silently accept
        # garbage thresholds. Depends only on level_thresholds + levels.
        if self.level_thresholds is not None:
            if len(self.level_thresholds) != self.levels - 1:
                raise ValueError(
                    f"level_thresholds must have levels-1={self.levels - 1} entries, "
                    f"got {len(self.level_thresholds)}."
                )
            if any(not (0.0 < t <= 1.0) for t in self.level_thresholds):
                raise ValueError("level_thresholds values must be in (0, 1].")
            if any(a <= b for a, b in zip(self.level_thresholds, self.level_thresholds[1:])):
                raise ValueError("level_thresholds must be strictly descending.")
        # record_embedding uses columns (plural), not field
        if self.scorer == "record_embedding":
            if not self.columns:
                raise ValueError(
                    "record_embedding scorer requires 'columns' (list of column names)."
                )
            self.field = "__record__"
            return self
        # Allow 'column' as alias for 'field'
        if self.field is None and self.column is not None:
            self.field = self.column
        elif self.field is None and self.column is None:
            raise ValueError("MatchkeyField requires 'field' or 'column'.")
        for t in self.transforms:
            FieldTransform(transform=t)  # reuse validation
        if self.scorer is not None and self.scorer not in VALID_SCORERS:
            # Check plugin registry before rejecting
            from goldenmatch.plugins.registry import PluginRegistry
            registry = PluginRegistry.instance()
            if not registry.has_scorer(self.scorer):
                raise ValueError(
                    f"Invalid scorer '{self.scorer}'. Must be one of {sorted(VALID_SCORERS)} "
                    f"or a registered plugin scorer."
                )
        return self

    # ── Typed accessors ──
    # ``field``, ``scorer``, ``weight`` are Optional at the schema level for
    # serialization round-trip, but the MatchkeyConfig validator guarantees
    # they're non-None for fuzzy/weighted matchkeys. These accessors narrow
    # the type for code paths that have already gone through that validator.
    @property
    def resolved_field(self) -> str:
        """``field`` narrowed to ``str`` after MatchkeyField validation."""
        if self.field is None:
            raise ValueError(
                "MatchkeyField.resolved_field accessed before field/column resolved."
            )
        return self.field

    @property
    def fuzzy_scorer(self) -> str:
        """``scorer`` narrowed to ``str`` for fields inside a weighted/probabilistic matchkey."""
        if self.scorer is None:
            raise ValueError(
                f"MatchkeyField (field={self.field!r}): fuzzy_scorer accessed but scorer is None. "
                "Only fields inside weighted/probabilistic matchkeys are guaranteed to have a scorer."
            )
        return self.scorer

    @property
    def fuzzy_weight(self) -> float:
        """``weight`` narrowed to ``float`` for fields inside a weighted matchkey."""
        if self.weight is None:
            raise ValueError(
                f"MatchkeyField (field={self.field!r}): fuzzy_weight accessed but weight is None. "
                "Only fields inside weighted matchkeys are guaranteed to have a weight."
            )
        return self.weight


class NegativeEvidenceField(BaseModel):
    """v1.11: a field whose disagreement subtracts from a weighted matchkey's
    score. Mirrors MatchkeyField's shape so transforms can normalize before
    scoring (e.g., transforms=['digits_only'] + scorer='exact' for phone).

    Spec: docs/superpowers/specs/2026-05-08-autoconfig-negative-evidence-and-clustered-identity-design.md
    """
    model_config = ConfigDict(extra="forbid")

    field: str
    transforms: list[str] = Field(default_factory=list)
    scorer: str
    threshold: float = Field(ge=0.0, le=1.0)
    # Weighted/exact only: flat 0-1 penalty subtracted from the score when
    # this field disagrees. REQUIRED for weighted/exact (enforced by
    # ``MatchkeyConfig._validate_weighted``, not here); REJECTED for
    # probabilistic matchkeys, which use EM-learned weights instead (or the
    # ``penalty_bits`` override below).
    penalty: float | None = Field(default=None, ge=0.0, le=1.0)
    # Probabilistic-only: fixed LLR override in log2 units. When set, the NE
    # dimension skips EM and contributes -abs(penalty_bits) when FIRED (both
    # values present and scorer similarity STRICTLY below threshold), else 0.
    # Absent => the weight is EM-learned (see core/probabilistic.py).
    # Rejected on weighted/exact matchkeys (they use `penalty`).
    penalty_bits: float | None = None
    # When set, ``field`` is a SYNTHESIZED column the pipeline materializes
    # before scoring by space-joining ``derive_from`` (e.g. a person full name
    # from ['first_name', 'last_name']). Lets an NE score a composite the raw
    # frame doesn't carry -- used by the facility full-name NE lever so a
    # token_sort on the whole name can tell distinct colleagues apart from
    # nickname/typo duplicates. None => ``field`` must already exist.
    derive_from: list[str] | None = None

    @model_validator(mode="after")
    def _validate_transforms_and_scorer(self) -> NegativeEvidenceField:
        if self.derive_from is not None and len(self.derive_from) < 2:
            raise ValueError(
                "derive_from must name at least 2 source columns to concatenate"
            )
        for t in self.transforms:
            if t not in VALID_SIMPLE_TRANSFORMS:
                raise ValueError(
                    f"Invalid transform '{t}'. Must be one of "
                    f"{sorted(VALID_SIMPLE_TRANSFORMS)}"
                )
        if self.scorer not in VALID_SCORERS:
            raise ValueError(
                f"Invalid scorer '{self.scorer}'. Must be one of "
                f"{sorted(VALID_SCORERS)}"
            )
        return self

    @property
    def flat_penalty(self) -> float:
        """``penalty`` narrowed to ``float`` for weighted/exact matchkeys.

        Raises ``ValueError`` if unset — ``MatchkeyConfig._validate_weighted``
        guarantees this never fires on a Pydantic-validated weighted/exact
        matchkey's NE entries.
        """
        if self.penalty is None:
            raise ValueError(
                f"NegativeEvidenceField (field={self.field!r}): flat_penalty accessed "
                "but penalty is None. Only weighted/exact matchkeys are guaranteed to "
                "have a penalty."
            )
        return self.penalty


class RulesPayload(BaseModel):
    """Web-UI-facing wrapper around the matchkey + threshold portions of config.

    ``standardization`` mirrors ``StandardizationConfig.rules`` (column →
    list of standardizer names). Optional so existing payloads from the
    workbench keep validating without modification. Validation against
    ``VALID_STANDARDIZERS`` happens here so the UI gets a 422 with the
    exact column rather than a deeper engine error at preview time.

    ``blocking`` accepts a ``BlockingConfig`` literal so the workbench can
    pin a strategy + keys without having to invent a parallel wire shape.
    Absent (``None``) means "let the engine pick" — the workbench's
    historical default of ``auto_suggest=True`` with no static keys.
    """
    threshold: float = Field(ge=0.0, le=1.0)
    matchkeys: list[MatchkeyField]
    standardization: dict[str, list[str]] | None = None
    blocking: BlockingConfig | None = None

    @model_validator(mode="after")
    def _validate_standardizers(self) -> RulesPayload:
        if self.standardization:
            for column, std_names in self.standardization.items():
                for name in std_names:
                    if name not in VALID_STANDARDIZERS:
                        raise ValueError(
                            f"Invalid standardizer '{name}' for column '{column}'. "
                            f"Valid: {sorted(VALID_STANDARDIZERS)}"
                        )
        return self


# ── MatchkeyConfig ──────────────────────────────────────────────────────────


_VALID_MK_TYPES = ("exact", "weighted", "probabilistic")


class MatchkeyConfig(BaseModel):
    """A matchkey: rule for declaring two records 'the same' on a field/field-set.

    Per-type field invariants (enforced by ``_validate_weighted`` after init):

    - ``type == "exact"``: ``fields`` populated; ``threshold`` optional (binary
      emit at 1.0 when no negative_evidence). No per-field scorer/weight.
    - ``type == "weighted"``: ``threshold`` REQUIRED (non-None); every field
      has ``scorer`` AND ``weight`` REQUIRED (non-None).
    - ``type == "probabilistic"``: every field has ``scorer`` REQUIRED. EM
      learns the weights at runtime.

    The Pydantic fields stay ``Optional`` at the schema level (so YAML
    round-trips and ``model_dump(exclude_none=True)`` continue to work) but
    callers in fuzzy/weighted code paths can use the typed-accessor
    properties (``fuzzy_threshold``, plus ``MatchkeyField.fuzzy_scorer`` /
    ``fuzzy_weight``) to consume them as ``float`` / ``str`` without
    re-asserting non-None at every call site. The accessors assert the
    invariant — if it fires, a caller has bypassed the validator (e.g. by
    mutating fields post-construction) and the crash points at the bug.
    """

    name: str
    type: Literal["exact", "weighted", "probabilistic"] | None = None
    comparison: str | None = None
    fields: list[MatchkeyField]
    threshold: float | None = None
    auto_threshold: bool = False
    rerank: bool = False
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_band: float = 0.1
    # v1.11: negative evidence fields — default-None for v1.10 cache compat.
    #
    # Valid on all three matchkey types. ``weighted``/``exact`` use a flat
    # ``penalty`` (0-1, unchanged since v1.11). ``probabilistic`` (Fellegi-
    # Sunter) uses EM-learned NE weights (Formulation B), with an optional
    # ``penalty_bits`` fixed override. See
    # ``docs/superpowers/specs/2026-07-14-fs-negative-evidence-design.md``.
    negative_evidence: list[NegativeEvidenceField] | None = None
    # Fellegi-Sunter EM parameters
    em_iterations: int = 20
    convergence_threshold: float = 0.001
    link_threshold: float | None = None  # auto-computed if None
    review_threshold: float | None = None  # auto-computed if None
    # Probabilistic-only: persisted EM model (Splink-style train-once -> reuse).
    # When set and the file exists, the trained EMResult is loaded and EM is
    # skipped; when set and absent, EM runs and the result is saved there.
    # Ignored for non-probabilistic matchkeys. See core/probabilistic.py
    # load_or_train_em.
    model_path: str | None = None

    @model_validator(mode="after")
    def _validate_weighted(self) -> MatchkeyConfig:
        # Allow 'comparison' as alias for 'type'
        if self.type is None and self.comparison is not None:
            if self.comparison in _VALID_MK_TYPES:
                self.type = self.comparison
            else:
                raise ValueError(
                    f"Invalid comparison '{self.comparison}'. Must be one of {_VALID_MK_TYPES}."
                )
        elif self.type is None:
            raise ValueError("MatchkeyConfig requires 'type' or 'comparison'.")
        if self.type == "weighted":
            if self.threshold is None:
                raise ValueError("Weighted matchkeys require a 'threshold'.")
            for f in self.fields:
                if f.scorer is None or f.weight is None:
                    raise ValueError(
                        f"All fields in a weighted matchkey must have 'scorer' and 'weight'. "
                        f"Field '{f.field}' is missing one or both."
                    )
        elif self.type == "probabilistic":
            for f in self.fields:
                if f.scorer is None:
                    raise ValueError(
                        f"All fields in a probabilistic matchkey must have 'scorer'. "
                        f"Field '{f.field}' is missing it."
                    )
        if self.type in ("weighted", "exact"):
            for ne in self.negative_evidence or []:
                if ne.penalty is None:
                    raise ValueError(
                        f"Matchkey '{self.name}' (type={self.type!r}): negative_evidence "
                        f"field '{ne.field}' requires 'penalty' for weighted/exact matchkeys."
                    )
                if ne.penalty_bits is not None:
                    raise ValueError(
                        f"Matchkey '{self.name}' (type={self.type!r}): negative_evidence "
                        f"field '{ne.field}' sets 'penalty_bits', which is only valid on "
                        "probabilistic matchkeys. Use 'penalty' instead."
                    )
        elif self.type == "probabilistic":
            for ne in self.negative_evidence or []:
                if ne.penalty is not None:
                    raise ValueError(
                        f"Matchkey '{self.name}' (type={self.type!r}): negative_evidence "
                        f"field '{ne.field}' sets 'penalty', but probabilistic matchkeys use "
                        "EM-learned NE weights; set penalty_bits to override."
                    )
        return self

    # ── Typed accessors ──
    # The Pydantic field-level types stay Optional so YAML serialization keeps
    # working unchanged, but downstream code paths that have already gone
    # through the weighted/fuzzy branch can use these to drop the Optional
    # without re-asserting at every call site. Each accessor enforces the
    # invariant the validator promised — if a property raises, a caller has
    # mutated the model after construction (or skipped validation) and the
    # crash points at the bug.
    @property
    def fuzzy_threshold(self) -> float:
        """``threshold`` narrowed to ``float`` for weighted matchkeys.

        Raises ``ValueError`` if the matchkey is not weighted or threshold
        is unset — the validator guarantees this never fires on a Pydantic-
        validated weighted matchkey.
        """
        if self.threshold is None:
            raise ValueError(
                f"MatchkeyConfig '{self.name}' (type={self.type!r}): "
                "fuzzy_threshold accessed but threshold is None. "
                "Only weighted matchkeys are guaranteed to have a threshold."
            )
        return self.threshold


# ── BlockingKeyConfig / BlockingConfig ──────────────────────────────────────


class BlockingKeyConfig(BaseModel):
    fields: list[str]
    transforms: list[str] = Field(default_factory=list)
    # Per-field transform chains (#1826). A field present here uses ITS chain
    # for the block-key derivation; fields absent keep the key-level
    # ``transforms`` chain. This is what lets a mixed Splink rule
    # (``l.last=r.last AND SUBSTR(l.first,1,1)=SUBSTR(r.first,1,1)``) map
    # exactly instead of widening every field to the substring (the
    # 388K-mega-block footgun).
    field_transforms: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_fields_nonempty(self) -> BlockingKeyConfig:
        if not self.fields:
            raise ValueError("Blocking key must have at least one field.")
        unknown = [f for f in self.field_transforms if f not in self.fields]
        if unknown:
            raise ValueError(
                f"field_transforms references field(s) not in this key's "
                f"fields: {unknown} (fields: {self.fields})"
            )
        return self


class SortKeyField(BaseModel):
    column: str
    transforms: list[str] = Field(default_factory=list)


class CanopyConfig(BaseModel):
    fields: list[str]
    loose_threshold: float = 0.3
    tight_threshold: float = 0.7
    max_canopy_size: int = 500


class LSHKeyConfig(BaseModel):
    """MinHash/LSH blocking on a text column (#1081).

    Provide either ``threshold`` (the band/row split is then chosen by
    ``optimal_bands``) or an explicit ``num_bands`` (which must divide
    ``num_perms``). If both are set, ``num_bands`` wins (``threshold`` is
    ignored). Shingle ``mode`` is char- or word-grams of size ``k``.
    """

    column: str
    mode: Literal["char", "word"] = "char"
    k: int = 3
    num_perms: int = 128
    seed: int = 0
    threshold: float | None = None
    num_bands: int | None = None

    @model_validator(mode="after")
    def _validate(self) -> LSHKeyConfig:
        if self.k < 1:
            raise ValueError("LSHKeyConfig 'k' must be >= 1.")
        if self.num_perms < 1:
            raise ValueError("LSHKeyConfig 'num_perms' must be >= 1.")
        if self.threshold is None and self.num_bands is None:
            raise ValueError("LSHKeyConfig requires either 'threshold' or 'num_bands'.")
        if self.threshold is not None and not 0.0 < self.threshold < 1.0:
            raise ValueError("LSHKeyConfig 'threshold' must be in (0, 1).")
        if self.num_bands is not None and (
            self.num_bands < 1 or self.num_perms % self.num_bands != 0
        ):
            raise ValueError("LSHKeyConfig 'num_perms' must be divisible by 'num_bands'.")
        return self


class SimHashKeyConfig(BaseModel):
    """SimHash/LSH blocking on a text column via dense embeddings (#1082).

    The text column is embedded (via ``model``, default the in-house ER
    embedder), then each embedding is SimHash-projected through ``num_planes``
    random hyperplanes and banded into LSH buckets. Records whose embeddings are
    cosine-near collide in a band, so this is the *semantic* near-duplicate
    blocker (complementing the lexical MinHash/LSH ``LSHKeyConfig``).

    Provide either ``threshold`` (the band/row split is then chosen by
    ``optimal_bands``) or an explicit ``num_bands`` (which must divide
    ``num_planes``). If both are set, ``num_bands`` wins (``threshold`` is
    ignored).
    """

    column: str
    num_planes: int = 256
    seed: int = 0
    threshold: float | None = None
    num_bands: int | None = None
    model: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> SimHashKeyConfig:
        if self.num_planes < 1:
            raise ValueError("SimHashKeyConfig 'num_planes' must be >= 1.")
        if self.threshold is None and self.num_bands is None:
            raise ValueError("SimHashKeyConfig requires either 'threshold' or 'num_bands'.")
        if self.threshold is not None and not 0.0 < self.threshold < 1.0:
            raise ValueError("SimHashKeyConfig 'threshold' must be in (0, 1).")
        if self.num_bands is not None and (
            self.num_bands < 1 or self.num_planes % self.num_bands != 0
        ):
            raise ValueError("SimHashKeyConfig 'num_planes' must be divisible by 'num_bands'.")
        return self


class PerceptualKeyConfig(BaseModel):
    """Banded hamming-LSH blocking over a column of perceptual hashes (ADR 0022).

    The ``column`` holds fixed-width hex perceptual hashes (e.g. a 16-char / 64-bit
    image pHash, produced upstream by ``core.perceptual.phash_image``). The
    ``hash_bits`` are split into ``num_bands`` contiguous bit-bands; two hashes
    within a small hamming distance share at least one identical band with high
    probability, so they collide into a candidate block. More bands -> higher
    recall and more candidate pairs. This is the *media* near-duplicate blocker,
    complementing the lexical (``lsh``) and semantic (``simhash``) paths.

    The ``num_bands`` default of 16 is recall-driven, not arbitrary: at the 0.85
    image-pHash threshold (a 0.15 hamming radius) the bench suite measured 16 bands
    at 0.97 blocking recall vs only 0.72 for the old default of 8 (ADR 0022). The
    zero-config path derives the count from the scorer threshold via
    ``core.perceptual_blocker.recommend_num_bands``; this static default is the
    knob for an explicit config.
    """

    column: str
    num_bands: int = 16
    hash_bits: int = 64

    @model_validator(mode="after")
    def _validate(self) -> PerceptualKeyConfig:
        if self.num_bands < 1 or self.hash_bits < 1 or self.hash_bits % self.num_bands != 0:
            raise ValueError(
                "PerceptualKeyConfig 'hash_bits' must be a positive multiple of 'num_bands'."
            )
        return self


class ThroughputConfig(BaseModel):
    """Opt-in sketch-then-verify throughput tier (#1083).

    A high-recall, low-cost dedup posture: LSH/sketch blocking + a light
    sketch-distance verify instead of per-field fuzzy/FS scoring. ``recall_target``
    is the primary knob; ``similarity_threshold`` overrides the default near-dup
    similarity (Jaccard 0.8 lexical / cosine 0.85 semantic, chosen by metric).
    """

    enabled: bool = False
    recall_target: float = Field(default=0.95, gt=0.0, lt=1.0)
    similarity_threshold: float | None = Field(default=None, gt=0.0, lt=1.0)


class BlockingConfig(BaseModel):
    keys: list[BlockingKeyConfig] = []
    max_block_size: int = 5000
    skip_oversized: bool = False
    strategy: Literal["static", "adaptive", "sorted_neighborhood", "multi_pass", "ann", "canopy", "ann_pairs", "learned", "lsh", "simhash", "perceptual"] = "static"
    learned_sample_size: int = 5000
    learned_min_recall: float = 0.95
    learned_min_reduction: float = 0.90
    learned_predicate_depth: int = 2
    learned_cache_path: str | None = None  # persist for reuse
    auto_suggest: bool = False
    auto_select: bool = False
    sub_block_keys: list[BlockingKeyConfig] | None = None
    window_size: int = 20
    sort_key: list[SortKeyField] | None = None
    passes: list[BlockingKeyConfig] | None = None
    union_mode: bool = True
    max_total_comparisons: int | None = None
    ann_column: str | None = None
    ann_model: str = "all-MiniLM-L6-v2"
    ann_top_k: int = 20
    canopy: CanopyConfig | None = None
    lsh: LSHKeyConfig | None = None
    simhash: SimHashKeyConfig | None = None
    perceptual: PerceptualKeyConfig | None = None

    @model_validator(mode="after")
    def _validate_keys_or_passes(self) -> BlockingConfig:
        """Ensure at least keys or passes is provided for strategies that need them."""
        if self.auto_suggest:
            return self  # auto_suggest discovers keys at runtime
        # Strategies that don't need keys: ann, ann_pairs, canopy, learned,
        # sorted_neighborhood (uses sort_key instead). "lsh" carries its own
        # LSHKeyConfig and is validated positively below.
        needs_keys = self.strategy in ("static", "adaptive")
        needs_passes = self.strategy == "multi_pass"
        if needs_keys and not self.keys and not self.sub_block_keys:
            raise ValueError(
                f"BlockingConfig with strategy='{self.strategy}' requires 'keys'."
            )
        if needs_passes and not self.keys and not self.passes:
            raise ValueError(
                "BlockingConfig with strategy='multi_pass' requires 'keys' or 'passes'."
            )
        if self.strategy == "lsh":
            if self.lsh is None:
                raise ValueError("BlockingConfig with strategy='lsh' requires 'lsh'.")
            if self.keys or self.passes:
                raise ValueError(
                    "BlockingConfig with strategy='lsh' must not set 'keys'/'passes' "
                    "(it uses the 'lsh' config block)."
                )
        if self.strategy == "simhash":
            if self.simhash is None:
                raise ValueError("BlockingConfig with strategy='simhash' requires 'simhash'.")
            if self.keys or self.passes:
                raise ValueError(
                    "BlockingConfig with strategy='simhash' must not set 'keys'/'passes' "
                    "(it uses the 'simhash' config block)."
                )
        return self


# ── GoldenFieldRule / GoldenGroupRule / GoldenRulesConfig ────────────────────


class GoldenFieldRule(BaseModel):
    strategy: str
    date_column: str | None = None
    source_priority: list[str] | None = None
    when: str | None = None       # predicate over already-resolved fields
    validate_with: str | None = Field(default=None, alias="validate")  # candidate-filter name (goldenflow validator)

    @model_validator(mode="after")
    def _validate_strategy(self) -> GoldenFieldRule:
        # v1.18.1: accept `custom:<name>` for plugin-backed strategies.
        # Existence of the plugin is checked at dispatch time
        # (`core/golden.py::merge_field`), not here -- the rule may load
        # before plugins are discovered. See spec:
        # docs/superpowers/specs/2026-05-22-golden-strategy-plugin-slot-design.md
        if self.strategy.startswith("custom:"):
            if not _CUSTOM_STRATEGY_RE.match(self.strategy):
                raise ValueError(
                    f"Invalid custom strategy name '{self.strategy}'. "
                    "Must match 'custom:<lowercase_snake_case>'."
                )
            return self
        if self.strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy '{self.strategy}'. Must be one of {sorted(VALID_STRATEGIES)} "
                "or 'custom:<name>' for plugin-backed strategies."
            )
        if self.strategy == "most_recent" and not self.date_column:
            raise ValueError("Strategy 'most_recent' requires 'date_column'.")
        if self.strategy == "source_priority" and not self.source_priority:
            raise ValueError("Strategy 'source_priority' requires 'source_priority' list.")
        return self


_GROUP_STRATEGIES = frozenset({"most_complete", "source_priority", "most_recent", "anchor"})


class GoldenGroupRule(BaseModel):
    name: str
    columns: list[str]
    category: str | None = None
    strategy: str = "most_complete"
    date_column: str | None = None
    source_priority: list[str] | None = None
    anchor: str | None = None
    allow_fill: bool = False

    @model_validator(mode="after")
    def _validate_group(self) -> GoldenGroupRule:
        if len(self.columns) < 2:
            raise ValueError(f"GoldenGroupRule '{self.name}' needs >= 2 columns.")
        for col in self.columns:
            if col.startswith("__"):
                raise ValueError(
                    f"Group '{self.name}' column '{col}' is reserved (internal '__' prefix)."
                )
        if self.strategy not in _GROUP_STRATEGIES:
            raise ValueError(
                f"Invalid group strategy '{self.strategy}'. Must be one of {sorted(_GROUP_STRATEGIES)}."
            )
        if self.strategy == "most_recent" and not self.date_column:
            raise ValueError(f"Group '{self.name}' strategy 'most_recent' requires 'date_column'.")
        if self.strategy == "source_priority" and not self.source_priority:
            raise ValueError(f"Group '{self.name}' strategy 'source_priority' requires 'source_priority'.")
        if self.strategy == "anchor":
            if not self.anchor:
                raise ValueError(f"Group '{self.name}' strategy 'anchor' requires 'anchor'.")
            if self.anchor not in self.columns:
                raise ValueError(f"Group '{self.name}' anchor '{self.anchor}' must be one of its columns.")
        elif self.anchor is not None:
            raise ValueError(
                f"Group '{self.name}' sets 'anchor' but strategy is '{self.strategy}' "
                "(anchor only valid with strategy 'anchor')."
            )
        return self


class GoldenRulesConfig(BaseModel):
    default_strategy: str | None = None
    default: GoldenFieldRule | None = None
    field_rules: dict[str, GoldenFieldRule | list[GoldenFieldRule]] = Field(default_factory=dict)
    field_groups: list[GoldenGroupRule] = Field(default_factory=list)
    field_group_detection: bool = False
    max_cluster_size: int = 100
    auto_split: bool = True
    quality_weighting: bool = True
    weak_cluster_threshold: float = 0.3
    # #726: cap on cumulative auto-split edge-work. None => auto-scaled
    # max(5_000_000, n_rows * 5). Raise this (or env
    # GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET) if a loud "clusters left oversized"
    # warning fires on a legitimately dense dataset. Precedence: this field >
    # env > auto-scaled.
    split_edge_budget: int | None = None
    # v1.18: post-cluster golden-rules refinement. When True, after
    # clustering the pipeline runs `refine_golden_rules` against the
    # cluster output + column profiles to pick per-field strategies
    # informed by within-cluster spread, per-source completeness, etc.
    # Default False to preserve existing behavior; opt-in for v1.18 users.
    # Spec: docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md
    adaptive: bool = False

    # v1.20.x (#430): LLM fallback for ambiguous fields. When True and
    # the heuristic refiner returns None for a field (no rule fires),
    # dispatch one LLM call per field to pick a strategy. Cached by
    # (dataset, field). BudgetTracker integration via the existing
    # `BudgetConfig`-attached scorer config (set on `match_settings`).
    # Soft-fails: no API key / budget exhausted / invalid response
    # -> falls back to the base default_strategy.
    use_llm_for_ambiguous: bool = False

    # v1.18.2 (#429): per-cluster strategy overrides. Maps cluster_id
    # -> {field_name -> GoldenFieldRule}. When a cluster_id appears
    # here, those field rules supersede the top-level `field_rules`
    # for that cluster ONLY. Default None (no overrides).
    #
    # Set by the post-cluster `GoldenRulesRefiner` based on cluster
    # health (weak clusters get unanimous_or_null; oversized clusters
    # get confidence_majority; size-2 clusters get unanimous_or_null).
    # Users can also set this manually for surgical per-cluster control.
    #
    # When non-None, the polars-native fast path is disabled (per-
    # cluster rules force the slow path that calls merge_field per
    # cluster). Spec:
    # docs/superpowers/specs/2026-05-22-golden-rules-intelligence-layer-2-design.md §3
    cluster_overrides: dict[int, dict[str, GoldenFieldRule]] | None = None

    @model_validator(mode="after")
    def _validate_default(self) -> GoldenRulesConfig:
        # Resolve default_strategy from either field
        if self.default is not None and self.default_strategy is None:
            self.default_strategy = self.default.strategy
        if self.default_strategy is None:
            raise ValueError("GoldenRulesConfig requires 'default_strategy' or 'default'.")
        if self.default_strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"Invalid default_strategy '{self.default_strategy}'."
            )
        return self

    @model_validator(mode="after")
    def _validate_survivorship(self) -> GoldenRulesConfig:
        # Detect overlapping field_groups columns.
        seen: set[str] = set()
        for g in self.field_groups:
            for col in g.columns:
                if col in seen:
                    raise ValueError(f"Column '{col}' appears in more than one field group.")
                seen.add(col)
        group_cols = seen
        # Validate field_rules: no overlap with group columns; list-form clause ordering.
        for col, rule in self.field_rules.items():
            if col in group_cols:
                raise ValueError(f"Column '{col}' is in a field group and cannot also have a field_rule.")
            if isinstance(rule, list):
                defaults = [i for i, r in enumerate(rule) if r.when is None]
                if len(defaults) != 1:
                    raise ValueError(f"field_rules['{col}'] needs exactly one default (when-less) clause.")
                if defaults[0] != len(rule) - 1:
                    raise ValueError(f"field_rules['{col}'] default clause must be last.")
        # NOTE: cycle detection over `when:` field references is intentionally
        # NOT performed here -- it is enforced later in Phase E
        # `build_resolution_order` which has access to the full column graph.
        return self


# ── StandardizationConfig ──────────────────────────────────────────────────

VALID_STANDARDIZERS = frozenset({
    "email", "name_proper", "name_upper", "name_lower",
    "phone", "zip5", "address", "state", "strip", "trim_whitespace",
})


class ValidationRuleConfig(BaseModel):
    column: str
    rule_type: Literal["regex", "min_length", "max_length", "not_null", "in_set", "format"]
    params: dict = Field(default_factory=dict)
    action: Literal["null", "quarantine", "flag"] = "flag"


class ValidationConfig(BaseModel):
    rules: list[ValidationRuleConfig] = Field(default_factory=list)
    auto_fix: bool = True  # whether to run auto-fix before validation


class QualityConfig(BaseModel):
    """GoldenCheck integration config for enhanced data quality."""
    enabled: bool = True       # auto-detected: True if goldencheck installed
    mode: str = "announced"    # "silent" | "announced" | "disabled"
    fix_mode: str = "safe"     # "safe" | "moderate" | "none"
    domain: str | None = None  # "healthcare" | "finance" | "ecommerce"

    # Auto-config column-exclusion overrides (#404). The exclusion
    # detectors in `core.quality_exclusions` identify columns that are
    # statistically attractive but counter-productive for matching
    # (audit timestamps, foreign-system IDs, sentinel values, etc).
    # These two fields let the user override the auto-detection:
    #
    #   - autoconfig_force_exclude: extra columns to always exclude
    #     regardless of detector output. Useful when you know a column
    #     is bad for matching but the detectors don't catch it.
    #   - autoconfig_force_include: columns to RESCUE from any
    #     auto-detection. Useful for legitimate hash columns used in
    #     PPRL, etc. force_include wins on conflict.
    autoconfig_force_exclude: list[str] = Field(default_factory=list)
    autoconfig_force_include: list[str] = Field(default_factory=list)


class TransformConfig(BaseModel):
    """GoldenFlow integration config for data transformation."""
    enabled: bool = True       # auto-detected: True if goldenflow installed
    mode: Literal["silent", "announced", "disabled"] = "announced"


class StandardizationConfig(BaseModel):
    rules: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_standardizers(self) -> StandardizationConfig:
        for column, std_names in self.rules.items():
            for name in std_names:
                if name not in VALID_STANDARDIZERS:
                    raise ValueError(
                        f"Invalid standardizer '{name}' for column '{column}'. "
                        f"Valid: {sorted(VALID_STANDARDIZERS)}"
                    )
        return self


# ── InputFileConfig / InputConfig ───────────────────────────────────────────


class InputFileConfig(BaseModel):
    path: str
    id_column: str | None = None
    source_label: str | None = None
    source_name: str | None = None
    column_map: dict[str, str] | None = None
    delimiter: str = ","
    encoding: str = "utf8"
    sheet: str | None = None
    parse_mode: str = "auto"  # auto, delimited, fixed_width, key_value, block, entity_extract
    header_row: int | None = None
    has_header: bool | None = None
    skip_rows: list[int] | None = None


class InputConfig(BaseModel):
    files: list[InputFileConfig] = Field(default_factory=list)
    file_a: InputFileConfig | None = None
    file_b: InputFileConfig | None = None


# ── OutputConfig ────────────────────────────────────────────────────────────


class OutputConfig(BaseModel):
    path: str | None = None
    format: str | None = None
    directory: str | None = None
    run_name: str | None = None
    # When True, the lineage sidecar gains a `golden_records` section with
    # per-field provenance (value + source_row_id of the winning record).
    # Default off: at large scale this materializes one provenance object per
    # cluster + a large JSON sidecar. The vectorized batch builder makes it
    # feasible (per-field source_row_id, no per-row candidate list).
    lineage_provenance: bool = False


# ── LLM Budget / Scorer Config ────────────────────────────────────────────


class BudgetConfig(BaseModel):
    max_cost_usd: float | None = None
    max_calls: int | None = None
    escalation_model: str | None = None
    escalation_band: list[float] = Field(default_factory=lambda: [0.80, 0.90])
    escalation_budget_pct: float = 20
    warn_at_pct: float = 80


class LLMScorerConfig(BaseModel):
    enabled: bool = False
    provider: str | None = None  # "openai" or "anthropic", auto-detected if None
    model: str | None = None  # e.g. "gpt-4o-mini", auto-detected if None
    auto_threshold: float = 0.95  # auto-accept pairs above this
    candidate_lo: float = 0.75  # lower bound of LLM scoring range
    candidate_hi: float = 0.95  # upper bound (same as auto_threshold)
    batch_size: int = 75
    max_workers: int = 5  # concurrent LLM requests
    calibration_sample_size: int = 100  # pairs per calibration round
    calibration_max_rounds: int = 5  # max calibration iterations
    calibration_convergence_delta: float = 0.01  # stop when threshold shift < this
    budget: BudgetConfig | None = None
    mode: str = "pairwise"  # "pairwise" (legacy) or "cluster" (in-context LLM clustering)
    cluster_max_size: int = 100  # max records per LLM cluster block
    cluster_min_size: int = 5  # below this, fall back to pairwise


# ── Domain Extraction Config ──────────────────────────────────────────────


class DomainConfig(BaseModel):
    enabled: bool = False
    mode: str | None = None  # "product", "person", "bibliographic", "company", "auto"
    confidence_threshold: float = 0.3  # below this, route to LLM
    llm_validation: bool = True  # whether to use LLM for low-confidence extractions
    budget: BudgetConfig | None = None  # reuses budget config


# ── Learning Memory Config ─────────────────────────────────────────────────


class LearningConfig(BaseModel):
    """Learning Memory learning parameters."""
    threshold_min_corrections: int = 10
    weights_min_corrections: int = 50


class MemoryConfig(BaseModel):
    """Learning Memory configuration."""
    enabled: bool = True
    backend: str = "sqlite"
    path: str = ".goldenmatch/memory.db"
    connection: str | None = None
    trust: dict[str, float] = Field(default_factory=lambda: {"human": 1.0, "agent": 0.5})
    learning: LearningConfig = Field(default_factory=LearningConfig)
    reanchor: bool = True
    dataset: str | None = None
    table_prefix: str = ""

    @field_validator("dataset")
    @classmethod
    def _reject_empty_dataset(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("MemoryConfig.dataset must be non-empty (or None)")
        return stripped

    @field_validator("table_prefix")
    @classmethod
    def _validate_table_prefix(cls, v: str) -> str:
        import re
        if v and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError("table_prefix must match ^[A-Za-z_][A-Za-z0-9_]*$")
        return v


# ── MatchSettingsConfig ─────────────────────────────────────────────────────


class ChannelStitchConfig(BaseModel):
    """Cross-device / channel stitching configuration (#1110, epic #1108).

    Drives ``goldenmatch.identity.stitching.stitch_frame``: which columns are
    deterministic device keys, how records map to channels, and the per-channel
    trust weights used to downweight cross-channel probabilistic matches. Config
    plumbing only -- attaching it does not change resolution on its own; a caller
    (or a future pipeline hook) passes it to ``stitch_frame``.
    """

    enabled: bool = False
    # Columns whose shared non-null value is a near-certain same-person signal.
    # Empty -> stitching.DEFAULT_DEVICE_KEYS.
    device_keys: list[str] = Field(default_factory=list)
    # Column carrying an explicit channel label per record.
    channel_column: str = "channel"
    # Exact ``__source__`` -> channel overrides (beats the substring hints).
    channel_map: dict[str, str] = Field(default_factory=dict)
    # Per-channel trust weight in (0, 1]. Empty -> stitching.DEFAULT_CHANNEL_TRUST.
    channel_trust: dict[str, float] = Field(default_factory=dict)
    # Scale probabilistic match scores by the channels' trust factor.
    adjust_cross_channel: bool = True
    # Drop probabilistic stitch edges below this (post-adjustment) weight.
    prob_threshold: float = 0.0


class SurvivorshipConfig(BaseModel):
    """Golden-record survivorship configuration (#1111, epic #1108).

    Drives ``goldenmatch.identity.survivorship.build_golden_with_provenance``:
    which merge strategy wins each field, the column carrying a per-record
    timestamp (for ``most_recent`` + provenance), and whether to learn per-field
    strategies from steward ``FIELD_CORRECT`` corrections. Config plumbing only.
    """

    # Per-field merge strategy overrides (column -> strategy name). Unlisted
    # columns use ``default_strategy``.
    field_strategies: dict[str, str] = Field(default_factory=dict)
    default_strategy: str = "most_complete"
    # Column carrying a per-record timestamp (enables most_recent + per-cell
    # timestamp provenance).
    timestamp_column: str | None = None
    # Fold learned per-field strategies (from FIELD_CORRECT corrections) into
    # ``field_strategies``. Consumed by a caller/learning pass, not on its own.
    learn_from_corrections: bool = False


class StabilizationConfig(BaseModel):
    """Cross-run entity stabilization -- Identity v3 (#1112, epic #1108).

    Drives ``goldenmatch.identity.stabilize.stabilize_identities``: how many
    distinct runs of cross-entity overlap trigger an auto-consolidation, which
    survivor wins, and a minimum edge score. Config plumbing only.
    """

    # Distinct runs of cross-entity overlap evidence before a pair consolidates.
    min_runs: int = 3
    # Survivor selection: most_records | oldest | newest | lowest_id.
    winner_strategy: str = "most_records"
    # Minimum max-edge score for a pair to count as overlap.
    min_score: float = 0.0

    @field_validator("winner_strategy")
    @classmethod
    def _check_winner_strategy(cls, v: str) -> str:
        allowed = {"most_records", "oldest", "newest", "lowest_id"}
        if v not in allowed:
            raise ValueError(
                f"winner_strategy must be one of {sorted(allowed)}"
            )
        return v


class MediationConfig(BaseModel):
    """Conflict mediation workflow -- Identity v3 (#1113, epic #1108).

    Drives ``goldenmatch.identity.mediation``. ``auto_apply`` is the default for
    whether a ``distinct`` verdict actually splits the record out (vs. only
    recording the verdict). Config plumbing only.
    """

    auto_apply: bool = True


class IdentityConfig(BaseModel):
    """Identity Graph configuration.

    Spec: ``docs/superpowers/specs/2026-05-12-identity-graph-design.md``
    """
    enabled: bool = False
    backend: str = "sqlite"
    path: str = ".goldenmatch/identity.db"
    connection: str | None = None
    dataset: str | None = None
    source_pk_column: str | None = None
    emit_singletons: bool = True
    # v2.1: when a cluster's confidence drops below this, the resolver flags the
    # bottleneck pair as a ``conflicts_with`` edge so a steward sees it for
    # review. 0.6 mirrors the existing ``weak_cluster_threshold`` family. Set
    # to 0 to disable auto-detection.
    weak_confidence_threshold: float = 0.6
    # #1110: cross-device / channel stitching (CDP/MDM epic #1108). None ->
    # stitching is not configured (the default; identity resolution is
    # unchanged).
    stitching: ChannelStitchConfig | None = None
    # #1111: golden-record survivorship (strategies + per-cell provenance).
    # None -> default flat golden record (unchanged).
    survivorship: SurvivorshipConfig | None = None
    # #1112: cross-run entity stabilization (Identity v3). None -> no stabilize
    # pass configured (the default).
    stabilization: StabilizationConfig | None = None
    # #1113: conflict mediation workflow. None -> not configured (default).
    mediation: MediationConfig | None = None

    @field_validator("dataset")
    @classmethod
    def _reject_empty_dataset(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("IdentityConfig.dataset must be non-empty (or None)")
        return stripped


class MatchSettingsConfig(BaseModel):
    matchkeys: list[MatchkeyConfig]


# ── GoldenMatchConfig (top-level) ──────────────────────────────────────────


class DistributedRoutingConfig(BaseModel):
    """Per-stage distributed-routing pins. ``auto`` lets the planner decide;
    an explicit value pins the stage and is surfaced by the linter."""

    scoring: Literal["auto", "distributed", "in_process"] = "auto"
    clustering: Literal["auto", "distributed_wcc", "in_memory_scipy"] = "auto"
    golden: Literal["auto", "distributed", "in_process"] = "auto"


class SemanticBlockingConfig(BaseModel):
    """Opt-in semantic candidate-generation (recall-lever) config. Carries the
    knobs for the additional blocking keys that union extra candidate pairs into
    the blocking stage: ANN nearest-neighbors, initialism expansion, and alias
    table lookups. This is config plumbing only -- it is attached to
    ``GoldenMatchConfig.semantic_blocking`` and CONSUMED downstream; constructing
    it does not change behavior on its own."""

    keys: list[Literal["ann", "initialism", "alias"]] = Field(
        default_factory=lambda: ["ann", "initialism", "alias"],
        description=(
            "Which semantic blocking keys to union into candidate generation: "
            "'ann' (embedding nearest-neighbors), 'initialism' (initialism "
            "expansion), 'alias' (alias-table lookups)."
        ),
    )
    ann_model: str = Field(
        default="inhouse",
        description="Embedding model id for the ANN key (e.g. 'inhouse').",
    )
    ann_top_k: int = Field(
        default=20,
        description="Number of ANN neighbors retrieved per record for candidate generation.",
    )
    ann_threshold: float = Field(
        default=0.5,
        description="Minimum ANN similarity for a neighbor to become a candidate pair.",
    )
    alias_tables: list[Literal["given_names", "business"]] = Field(
        default_factory=lambda: ["given_names", "business"],
        description="Which alias tables the 'alias' key consults.",
    )


class GoldenMatchConfig(BaseModel):
    input: InputConfig | None = None
    output: OutputConfig = Field(default_factory=lambda: OutputConfig())
    match_settings: MatchSettingsConfig | None = None
    matchkeys: list[MatchkeyConfig] | None = None
    blocking: BlockingConfig | None = None
    golden_rules: GoldenRulesConfig | None = None
    standardization: StandardizationConfig | None = None
    validation: ValidationConfig | None = None
    quality: QualityConfig | None = None
    transform: TransformConfig | None = None
    llm_boost: bool = False
    llm_scorer: LLMScorerConfig | None = None
    llm_auto: bool = False
    domain: DomainConfig | None = None
    backend: str | None = None  # None (default Polars), "ray", "duckdb"
    distributed_routing: DistributedRoutingConfig | None = None
    semantic_blocking: SemanticBlockingConfig | None = None
    allow_slow_path: bool = False
    # Execution mode. "standard" (default) = the in-memory/Ray pipeline,
    # bit-identical artifacts. "scale" = the DataFusion spine (out-of-core,
    # deterministic + semantically correct but NOT bit-identical to standard;
    # MAX dedup, reduced feature surface). The spine entry
    # (backends/datafusion_spine.run_spine) enforces the scale-mode feature
    # gate; this field is the opt-in signal.
    mode: Literal["standard", "scale"] = "standard"
    planning_effort: Literal["fast", "normal", "thinking", "einstein"] = Field(
        default="normal",
        description=(
            "Auto-config planning-effort tier (spec 2026-06-06 §Phase 0). "
            "Controls how hard the controller searches: 'fast' = a single "
            "cheap pass; 'normal' (default) = today's interactive budget; "
            "'thinking'/'einstein' spend the freed engine cycles on a larger "
            "sample, more refit iterations, and — at thinking+ — measuring "
            "real blocking on the full frame instead of extrapolating. "
            "Overridable via the GOLDENMATCH_PLANNING_EFFORT env var. Default "
            "'normal' is byte-for-byte the prior behavior."
        ),
    )
    throughput: ThroughputConfig | None = None
    memory: MemoryConfig | None = None
    identity: IdentityConfig | None = None
    exclude_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Column names to skip across the suite. GoldenMatch "
            "auto-config never picks these for matchkeys/blocking. "
            "GoldenFlow transforms skip them entirely (column passes "
            "through unchanged). Layered ADDITIVELY with GoldenCheck "
            "detector-derived exclusions (#404) -- the user list is "
            "OR'd with auto-detection, not a replacement. "
            "`QualityConfig.autoconfig_force_include` still wins on "
            "conflict (rescue beats every opt-out path). Column still "
            "appears in golden record output -- exclusion is about "
            "matching + transforming, not output. See spec "
            "docs/superpowers/specs/2026-05-21-unified-column-exclusions-design.md."
        ),
    )
    prepared_record_store: bool = Field(
        default=False,
        description=(
            "When True, the prep stage (quality scan + transform + auto-fix) "
            "writes its output to a DuckDB-backed disk store keyed by config "
            "signature. Subsequent calls with the same config + data shape "
            "read prepared records from disk instead of re-prepping. Path "
            "via GOLDENMATCH_PREPARED_RECORD_STORE_DIR env var; persistence "
            "via GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST=1. Spec: "
            "docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md "
            "§Component 1."
        ),
    )
    partitioned_block_scoring: bool = Field(
        default=False,
        description=(
            "When True AND prepared_record_store is True, the pipeline "
            "materializes blocks to the disk store as a side effect of "
            "build_blocks (Component 2 Phase 2 of Distributed Plan v1). "
            "Stages on-disk blocks for Component 3 (distributed scoring); "
            "no single-process win expected. Default off."
        ),
    )
    n_buckets: int | None = Field(
        default=None,
        ge=1,
        le=1024,
        description=(
            "Number of hash buckets for Component 2 v2 bucketed Parquet "
            "storage. None means use the heuristic default "
            "max(cpu_count() * 4, 64). Hard-capped at 1024. Spec: "
            "docs/superpowers/specs/2026-05-17-distributed-plan-component-2-v2"
            "-bucketed-storage-design.md §Configuration."
        ),
    )

    # Auto-config verification hand-offs (see goldenmatch/core/autoconfig_verify.py).
    # These attrs are set by auto_configure_df and read by the pipeline;
    # they are NOT persisted to YAML. Declaring them as PrivateAttr insulates
    # the hand-off contract from Pydantic v2 private-attr handling changes.
    _preflight_report: PreflightReport | None = PrivateAttr(default=None)
    _strict_autoconfig: bool = PrivateAttr(default=False)
    _domain_profile: Any = PrivateAttr(default=None)
    _throughput_plan: Any = PrivateAttr(default=None)
    # Fused-match routing flag (see goldenmatch/core/fused_routing.py). Set by
    # the controller post-step via ExecutionPlan.apply_to; read by the pipeline
    # to short-circuit block->score->cluster. Default-False keeps every plan
    # byte-identical when unset. Mirrors _throughput_plan's hand-off contract.
    _use_fused_match: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def _validate_fuzzy_needs_blocking(self) -> GoldenMatchConfig:
        mks = self.get_matchkeys()
        has_fuzzy = any(mk.type in ("weighted", "probabilistic") for mk in mks)
        if has_fuzzy and self.blocking is None:
            raise ValueError(
                "Weighted/probabilistic matchkeys require a 'blocking' configuration."
            )
        return self

    def get_matchkeys(self) -> list[MatchkeyConfig]:
        """Return matchkeys from either top-level or match_settings."""
        if self.matchkeys:
            return self.matchkeys
        if self.match_settings:
            return self.match_settings.matchkeys
        return []


# RulesPayload's `blocking` field forward-references BlockingConfig (defined
# later in this module). Resolve the reference now that all models exist.
RulesPayload.model_rebuild()
