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
    # Date-aware comparator (#1858): parses ISO YYYY-MM-DD and scores by
    # Damerau-Levenshtein over the canonical digits, so a typo scores far above
    # an unrelated date (jaro_winkler collapses both to 0.80+). Non-ISO input
    # degrades to levenshtein. Use this for date columns instead of a name-
    # oriented fuzzy scorer.
    "date",
    # Magnitude-aware date comparator (spec 2026-07-23-fs-domain-comparators):
    # parses both dates to a day-ordinal and scores by DAY-DISTANCE bands, so a
    # full-year DOB gap is a weak partial (the edit-distance `date` scorer above
    # is magnitude-blind -- one changed digit reads 0.90). Unparseable input
    # degrades to the `date` scorer. Use for date/dob columns on the FS path.
    "date_diff",
    # Magnitude-aware numeric comparator (spec 2026-07-23-fs-domain-comparators,
    # Phase 2): parses both to float and bands |a-b| on a monotone [0,1] ramp,
    # so string similarity on numbers (`levenshtein("100","900")` ~0.67) no longer
    # reads distinct amounts as near-agreement. Bare `numeric_diff` = 10% relative
    # band; `numeric_diff:abs:<eps>` / `numeric_diff:pct:<frac>` set the band (the
    # suffixed forms validate via _NUMERIC_DIFF_RE below). FS path.
    "numeric_diff",
    # Great-circle (haversine) distance comparator (spec 2026-07-23, Phase 2):
    # parses ONE combined "lat,long" field per side and bands the km distance.
    # (Two separate lat/long columns are the deferred cross-field comparator.) FS.
    "geo_haversine",
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
    # Reference-data name scorers (refdata/scorer.py): Jaro-Winkler modulated by
    # US-Census surname frequency (rare surnames weigh more) and an alias-aware
    # given-name variant (Bob<->Robert). Registered into the PluginRegistry when
    # `goldenmatch` is imported (via _api -> refdata), so a config referencing
    # them validates + scores without a manual `import goldenmatch.refdata`.
    # Kernel-backed on both surfaces (bucket ids 15/16; TS score-wasm).
    "name_freq_weighted_jw", "given_name_aliased_jw",
})

# Scorers valid as MATCHKEY scorers but NOT as negative-evidence scorers: the NE
# path runs the native `score_one` kernel (ids 0..=3), while the reference-data
# name scorers are FS kernel ids 4/5 with no NE path. Enforced in
# NegativeEvidenceField (they were implicitly excluded before their promotion to
# VALID_SCORERS).
_NE_UNSUPPORTED_SCORERS = frozenset({"name_freq_weighted_jw", "given_name_aliased_jw"})

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
# numeric_diff:abs:<eps> / numeric_diff:pct:<frac> -- the band-parameterized forms
# of the numeric_diff scorer (bare `numeric_diff` is a plain VALID_SCORERS member).
_NUMERIC_DIFF_RE = re.compile(r"^numeric_diff:(abs|pct):\d+(\.\d+)?$")


def _is_valid_scorer(scorer: str) -> bool:
    """A scorer name is valid if it's a VALID_SCORERS member or a recognized
    parameterized form (currently the ``numeric_diff:abs|pct:<band>`` suffix)."""
    return scorer in VALID_SCORERS or bool(_NUMERIC_DIFF_RE.match(scorer))


# ── FieldTransform ──────────────────────────────────────────────────────────


class FieldTransform(BaseModel):
    transform: str = Field(
        description="Name of the normalization to apply, such as a simple transform, 'substring:start:end', 'qgram:n', 'bloom_filter', or a registered plugin transform.",
    )

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
    field: str | None = Field(
        default=None,
        description="Source column this field compares on; may be given as 'column' instead.",
    )
    column: str | None = Field(
        default=None,
        description="Alias for 'field' naming the source column to compare.",
    )
    transforms: list[str] = Field(
        default_factory=list,
        description="Normalization steps applied to the value before scoring, in order.",
    )
    scorer: str | None = Field(
        default=None,
        description="Similarity comparator used to score agreement between the two values.",
    )
    weight: float | None = Field(
        default=None,
        description="Relative importance of this field's agreement within a weighted matchkey.",
    )
    model: str | None = Field(
        default=None,
        description="Embedding model name used when the scorer is 'embedding'.",
    )  # for embedding scorer
    columns: list[str] | None = Field(
        default=None,
        description="Set of source columns fused into one vector when the scorer is 'record_embedding'.",
    )  # for record_embedding scorer
    column_weights: dict[str, float] | None = Field(
        default=None,
        description="Per-column weights blending the inputs for the 'record_embedding' scorer.",
    )  # per-field weights for record_embedding
    levels: int = Field(
        default=2,
        ge=2,
        description="Number of probabilistic agreement bands (2=agree/disagree, 3=agree/partial/disagree).",
    )  # comparison levels for probabilistic: 2=agree/disagree, 3=agree/partial/disagree
    partial_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Similarity at or above which a 3-level comparison counts as a partial agreement.",
    )  # score >= this = partial agree (when levels=3)
    # Probabilistic-only: term-frequency (Winkler) weight adjustment. When True,
    # an exact agreement on a *rare* value carries more match weight than on a
    # *common* one (matching on "Zelinski" is stronger evidence than on
    # "Smith"). Off by default — only meaningful for skewed-frequency
    # categorical fields (names, cities). Applied by the vectorized FS scorer
    # using per-value frequencies computed at EM-train time.
    tf_adjustment: bool = Field(
        default=False,
        description="Enables Winkler term-frequency weighting so agreement on a rare value counts more than on a common one.",
    )
    # #1207 PR2a: per-dataset value->relative-frequency table for
    # name_freq_weighted_jw; when present the scorer downweights agreements on
    # high-frequency values across the whole JW range (data-driven), else falls
    # back to static census IDF in the borderline zone.
    tf_freqs: dict[str, float] | None = Field(
        default=None,
        description="Precomputed value-to-frequency table that drives data-driven downweighting of common values.",
    )
    # Workbench-only hint: which kind of MatchkeyConfig to wrap this field
    # in when /preview / /run translate the flat row list into engine
    # MatchkeyConfigs. Optional + None-default so engine-internal callers
    # that build MatchkeyField directly remain unaffected; preview's
    # _build_config falls back to its scorer-based heuristic when absent.
    type: Literal["exact", "weighted", "probabilistic"] | None = Field(
        default=None,
        description="Workbench hint for which matchkey kind to wrap this field in when translating a flat field list.",
    )
    # Probabilistic-only: EM iterations cap. Mirrors MatchkeyConfig.em_iterations
    # so the workbench can tune training stability without surfacing the full
    # MatchkeyConfig shape. Read by _build_config when type == "probabilistic".
    # `None` (not 20) is the default so `model_dump(exclude_none=True)` doesn't
    # leak the value into saved YAML for non-probabilistic matchkeys; the
    # workbench → engine translation in web/preview.py coerces None → 20.
    em_iterations: int | None = Field(
        default=None,
        ge=1,
        description="Per-field cap on EM training iterations for a probabilistic comparison.",
    )
    # N-level custom banding (Splink-converter Stage 1). Descending similarity
    # cutoffs; level index = count of satisfied thresholds (0 = disagree,
    # levels-1 = top agree). None => legacy banding (partial_threshold for
    # 2/3 levels, even k/N spacing for N>3). Length must be levels-1.
    level_thresholds: list[float] | None = Field(
        default=None,
        description="Descending similarity cutoffs defining each custom probabilistic band; must hold levels-1 entries.",
    )

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
        if self.scorer is not None and not _is_valid_scorer(self.scorer):
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

    field: str = Field(
        description="Column whose disagreement between two records counts as evidence against a match.",
    )
    transforms: list[str] = Field(
        default_factory=list,
        description="Normalization steps applied to the value before the disagreement check.",
    )
    scorer: str = Field(
        description="Similarity comparator used to decide whether the two values disagree.",
    )
    threshold: float = Field(
        ge=0.0,
        le=1.0,
        description="Similarity below which the two values are treated as disagreeing and the penalty fires.",
    )
    # Weighted/exact only: flat 0-1 penalty subtracted from the score when
    # this field disagrees. REQUIRED for weighted/exact (enforced by
    # ``MatchkeyConfig._validate_weighted``, not here); REJECTED for
    # probabilistic matchkeys, which use EM-learned weights instead (or the
    # ``penalty_bits`` override below).
    penalty: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Flat 0-1 amount subtracted from a weighted/exact score on disagreement.",
    )
    # Probabilistic-only: fixed LLR override in log2 units. When set, the NE
    # dimension skips EM and contributes -abs(penalty_bits) when FIRED (both
    # values present and scorer similarity STRICTLY below threshold), else 0.
    # Absent => the weight is EM-learned (see core/probabilistic.py).
    # Rejected on weighted/exact matchkeys (they use `penalty`).
    penalty_bits: float | None = Field(
        default=None,
        description="Fixed log-likelihood-ratio penalty in bits applied on disagreement for probabilistic matchkeys, overriding EM.",
    )
    # When set, ``field`` is a SYNTHESIZED column the pipeline materializes
    # before scoring by space-joining ``derive_from`` (e.g. a person full name
    # from ['first_name', 'last_name']). Lets an NE score a composite the raw
    # frame doesn't carry -- used by the facility full-name NE lever so a
    # token_sort on the whole name can tell distinct colleagues apart from
    # nickname/typo duplicates. None => ``field`` must already exist.
    derive_from: list[str] | None = Field(
        default=None,
        description="Source columns space-joined to synthesize the compared field when it is not present in the raw frame.",
    )

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
        if not _is_valid_scorer(self.scorer):
            raise ValueError(
                f"Invalid scorer '{self.scorer}'. Must be one of "
                f"{sorted(VALID_SCORERS)}"
            )
        # Negative evidence uses the native `score_one` kernel (ids 0..=3); the
        # reference-data name scorers are FS kernel ids 4/5 and have no NE path,
        # so reject them here even though they are valid MATCHKEY scorers. (They
        # were implicitly excluded before their promotion to VALID_SCORERS.)
        if self.scorer in _NE_UNSUPPORTED_SCORERS:
            raise ValueError(
                f"Scorer '{self.scorer}' is not supported as a negative-evidence "
                f"scorer (reference-data name scorers have no NE kernel path). "
                f"Use one of the standard scorers for negative evidence."
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
    threshold: float = Field(
        ge=0.0,
        le=1.0,
        description="Score at or above which a pair is accepted as a match across the payload's matchkeys.",
    )
    matchkeys: list[MatchkeyField] = Field(
        description="Flat list of field-level match rules the workbench translates into engine matchkeys.",
    )
    standardization: dict[str, list[str]] | None = Field(
        default=None,
        description="Per-column standardizer names applied before matching; validated against VALID_STANDARDIZERS.",
    )
    blocking: BlockingConfig | None = Field(
        default=None,
        description="Blocking strategy and keys to pin; None lets the engine auto-suggest candidate generation.",
    )

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

    name: str = Field(
        description="Identifier for this matchkey, used in output and logs.",
    )
    type: Literal["exact", "weighted", "probabilistic"] | None = Field(
        default=None,
        description="Matching mode: exact equality, weighted per-field scoring, or probabilistic Fellegi-Sunter.",
    )
    comparison: str | None = Field(
        default=None,
        description="Alias for 'type' accepting the same exact/weighted/probabilistic values.",
    )
    fields: list[MatchkeyField] = Field(
        description="Fields compared to decide whether two records match under this matchkey.",
    )
    threshold: float | None = Field(
        default=None,
        description="Score at or above which a pair is accepted as a match; required for weighted matchkeys.",
    )
    auto_threshold: bool = Field(
        default=False,
        description="Enables automatic Otsu-style tuning of the accept threshold from the score distribution.",
    )
    rerank: bool = Field(
        default=False,
        description="Enables cross-encoder reranking of borderline pairs near the threshold.",
    )
    rerank_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="Cross-encoder model used to rerank borderline pairs when rerank is on.",
    )
    rerank_band: float = Field(
        default=0.1,
        description="Half-width of the score band around the threshold within which pairs are reranked.",
    )
    # v1.11: negative evidence fields — default-None for v1.10 cache compat.
    #
    # Valid on all three matchkey types. ``weighted``/``exact`` use a flat
    # ``penalty`` (0-1, unchanged since v1.11). ``probabilistic`` (Fellegi-
    # Sunter) uses EM-learned NE weights (Formulation B), with an optional
    # ``penalty_bits`` fixed override. See
    # ``docs/superpowers/specs/2026-07-14-fs-negative-evidence-design.md``.
    negative_evidence: list[NegativeEvidenceField] | None = Field(
        default=None,
        description="Fields whose disagreement penalizes the match score, catching false positives that agree on other fields.",
    )
    # Fellegi-Sunter EM parameters
    em_iterations: int = Field(
        default=20,
        ge=1,
        description="Maximum EM iterations when training probabilistic weights.",
    )
    convergence_threshold: float = Field(
        default=0.001,
        gt=0.0,
        description="EM stops early once parameter change between iterations falls below this value.",
    )
    link_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Probabilistic match probability at or above which a pair is auto-linked.",
    )
    review_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Probabilistic match probability at or above which a pair is sent for manual review; must not exceed link_threshold.",
    )
    # Probabilistic-only: persisted EM model (Splink-style train-once -> reuse).
    # When set and the file exists, the trained EMResult is loaded and EM is
    # skipped; when set and absent, EM runs and the result is saved there.
    # Ignored for non-probabilistic matchkeys. See core/probabilistic.py
    # load_or_train_em.
    model_path: str | None = Field(
        default=None,
        description="File where the trained probabilistic model is loaded from if present, or saved to after training.",
    )
    # Probabilistic-only: how a missing value on either side of a comparison is
    # treated (#1846).
    #
    #   "unobserved" (default, #1819/#1834) -- textbook Fellegi-Sunter: a missing
    #       value is absence of evidence and contributes nothing either way.
    #   "disagree" -- a missing value is evidence AGAINST a match (level 0), the
    #       pre-#1834 behavior.
    #
    # Neither is universally right; it depends on whether missingness is
    # INFORMATIVE in the data. When missing-not-at-random (a record lacking a DOB
    # is systematically unlike one that has it), "disagree" is the better model
    # and "unobserved" over-merges: measured on historical_50k (8.9-50% nulls
    # across FS fields), "unobserved" costs f1_probabilistic 0.83 -> 0.33.
    # On clean data (febrl3, ncvr_synthetic) the two are indistinguishable.
    #
    # auto_configure_probabilistic_df picks this from the profiled null rates;
    # GOLDENMATCH_FS_MISSING overrides globally. Ignored for non-probabilistic
    # matchkeys.
    missing: Literal["unobserved", "disagree"] | None = Field(
        default=None,
        description="How a missing value is treated probabilistically: 'unobserved' contributes nothing, 'disagree' counts against a match.",
    )

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
            if not self.fields:
                raise ValueError(
                    "Probabilistic matchkeys require at least one comparison field."
                )
            field_names = [f.resolved_field for f in self.fields]
            duplicates = sorted(
                name for name in set(field_names) if field_names.count(name) > 1
            )
            if duplicates:
                raise ValueError(
                    "Probabilistic matchkeys cannot contain duplicate comparison "
                    f"fields: {', '.join(duplicates)}."
                )
            if (
                self.link_threshold is not None
                and self.review_threshold is not None
                and self.review_threshold > self.link_threshold
            ):
                raise ValueError(
                    "review_threshold must be less than or equal to link_threshold."
                )
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
    fields: list[str] = Field(
        description="Columns whose combined values form the blocking key; records sharing a key become candidate pairs.",
    )
    transforms: list[str] = Field(
        default_factory=list,
        description="Normalization steps applied to every field before deriving the block key.",
    )
    # Per-field transform chains (#1826). A field present here uses ITS chain
    # for the block-key derivation; fields absent keep the key-level
    # ``transforms`` chain. This is what lets a mixed Splink rule
    # (``l.last=r.last AND SUBSTR(l.first,1,1)=SUBSTR(r.first,1,1)``) map
    # exactly instead of widening every field to the substring (the
    # 388K-mega-block footgun).
    field_transforms: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-field transform chains overriding the key-level transforms for the named fields only.",
    )

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
    column: str = Field(
        description="Column records are sorted on for sorted-neighborhood blocking.",
    )
    transforms: list[str] = Field(
        default_factory=list,
        description="Normalization steps applied to the value before sorting.",
    )


class CanopyConfig(BaseModel):
    fields: list[str] = Field(
        description="Columns used to compute cheap similarity when forming canopies.",
    )
    loose_threshold: float = Field(
        default=0.3,
        description="Loose similarity at or above which a record joins a canopy as a candidate.",
    )
    tight_threshold: float = Field(
        default=0.7,
        description="Tight similarity at or above which a record is removed from the pool so it seeds no new canopy.",
    )
    max_canopy_size: int = Field(
        default=500,
        description="Ceiling on records in one canopy, capping the candidate pairs it can generate.",
    )


class LSHKeyConfig(BaseModel):
    """MinHash/LSH blocking on a text column (#1081).

    Provide either ``threshold`` (the band/row split is then chosen by
    ``optimal_bands``) or an explicit ``num_bands`` (which must divide
    ``num_perms``). If both are set, ``num_bands`` wins (``threshold`` is
    ignored). Shingle ``mode`` is char- or word-grams of size ``k``.
    """

    column: str = Field(
        description="Text column MinHash/LSH blocks on to group near-duplicate strings.",
    )
    mode: Literal["char", "word"] = Field(
        default="char",
        description="Whether shingles are character-grams or word-grams before hashing.",
    )
    k: int = Field(
        default=3,
        description="Shingle size (number of chars or words per gram).",
    )
    num_perms: int = Field(
        default=128,
        description="Number of MinHash permutations; more permutations sharpen the similarity estimate at higher cost.",
    )
    seed: int = Field(
        default=0,
        description="Random seed making the MinHash permutations reproducible.",
    )
    threshold: float | None = Field(
        default=None,
        description="Target Jaccard similarity from which the band/row split is derived when num_bands is unset.",
    )
    num_bands: int | None = Field(
        default=None,
        description="Explicit LSH band count (must divide num_perms); overrides threshold when set.",
    )

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

    column: str = Field(
        description="Text column embedded then SimHash-blocked to group semantically near records.",
    )
    num_planes: int = Field(
        default=256,
        description="Number of random hyperplanes the embedding is projected through to form the SimHash signature.",
    )
    seed: int = Field(
        default=0,
        description="Random seed making the SimHash hyperplanes reproducible.",
    )
    threshold: float | None = Field(
        default=None,
        description="Target cosine similarity from which the band/row split is derived when num_bands is unset.",
    )
    num_bands: int | None = Field(
        default=None,
        description="Explicit LSH band count (must divide num_planes); overrides threshold when set.",
    )
    model: str | None = Field(
        default=None,
        description="Embedding model used to vectorize the column; None uses the in-house ER embedder.",
    )

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

    column: str = Field(
        description="Column holding fixed-width hex perceptual hashes to block media near-duplicates on.",
    )
    num_bands: int = Field(
        default=16,
        description="Number of contiguous bit-bands the hash is split into; more bands raise recall and candidate pairs.",
    )
    hash_bits: int = Field(
        default=64,
        description="Total bit width of the perceptual hash; must be a positive multiple of num_bands.",
    )

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

    enabled: bool = Field(
        default=False,
        description="Turns on the sketch-then-verify throughput tier in place of per-field fuzzy/FS scoring.",
    )
    recall_target: float = Field(
        default=0.95,
        gt=0.0,
        lt=1.0,
        description="Desired blocking recall the sketch tier tunes its band count toward.",
    )
    similarity_threshold: float | None = Field(
        default=None,
        gt=0.0,
        lt=1.0,
        description="Overrides the default near-duplicate similarity cutoff; None uses the metric-specific default.",
    )


class BlockingConfig(BaseModel):
    keys: list[BlockingKeyConfig] = Field(
        default_factory=list,
        description="Blocking keys that generate candidate pairs; records sharing any key are compared.",
    )
    max_block_size: int = Field(
        default=5000,
        description="Ceiling on how many records one block may hold before it is treated as oversized.",
    )
    skip_oversized: bool = Field(
        default=False,
        description="When true, blocks exceeding max_block_size are dropped rather than scored, guarding against mega-block blowups.",
    )
    strategy: Literal["static", "adaptive", "sorted_neighborhood", "multi_pass", "ann", "canopy", "ann_pairs", "learned", "lsh", "simhash", "perceptual"] = Field(
        default="static",
        description="Candidate-generation method that selects how pairs are proposed for scoring.",
    )
    learned_sample_size: int = Field(
        default=5000,
        description="Number of sampled records the learned-predicate miner trains its blocking rules on.",
    )
    learned_min_recall: float = Field(
        default=0.95,
        description="Minimum pair recall a learned predicate must retain to be accepted.",
    )
    learned_min_reduction: float = Field(
        default=0.90,
        description="Minimum fraction of the full comparison space a learned predicate must eliminate.",
    )
    learned_predicate_depth: int = Field(
        default=2,
        description="Maximum number of conjoined conditions in a mined blocking predicate.",
    )
    learned_cache_path: str | None = Field(
        default=None,
        description="File where learned blocking predicates are persisted for reuse across runs.",
    )  # persist for reuse
    auto_suggest: bool = Field(
        default=False,
        description="Lets the engine discover blocking keys at runtime instead of using the static keys.",
    )
    auto_select: bool = Field(
        default=False,
        description="Lets the engine pick the best blocking strategy automatically.",
    )
    sub_block_keys: list[BlockingKeyConfig] | None = Field(
        default=None,
        description="Secondary keys used to split oversized blocks into smaller candidate sets.",
    )
    window_size: int = Field(
        default=20,
        description="Sliding-window width, in sorted records, for sorted-neighborhood blocking.",
    )
    sort_key: list[SortKeyField] | None = Field(
        default=None,
        description="Ordered columns records are sorted on for sorted-neighborhood blocking.",
    )
    passes: list[BlockingKeyConfig] | None = Field(
        default=None,
        description="Sequence of blocking key sets applied in separate passes for multi_pass blocking.",
    )
    union_mode: bool = Field(
        default=True,
        description="When true, candidate pairs from all passes are unioned rather than intersected.",
    )
    max_total_comparisons: int | None = Field(
        default=None,
        description="Global cap on the number of candidate pairs generated across all blocks.",
    )
    ann_column: str | None = Field(
        default=None,
        description="Text column embedded for approximate-nearest-neighbor blocking.",
    )
    ann_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Embedding model used to vectorize records for ANN blocking.",
    )
    ann_top_k: int = Field(
        default=20,
        description="Number of nearest neighbors retrieved per record in ANN blocking.",
    )
    canopy: CanopyConfig | None = Field(
        default=None,
        description="Configuration for canopy clustering when the strategy is 'canopy'.",
    )
    lsh: LSHKeyConfig | None = Field(
        default=None,
        description="MinHash/LSH configuration required when the strategy is 'lsh'.",
    )
    simhash: SimHashKeyConfig | None = Field(
        default=None,
        description="SimHash/LSH configuration required when the strategy is 'simhash'.",
    )
    perceptual: PerceptualKeyConfig | None = Field(
        default=None,
        description="Perceptual-hash LSH configuration used when the strategy is 'perceptual'.",
    )

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
    strategy: str = Field(
        description="Survivorship strategy that picks the winning value for a field across a cluster's records.",
    )
    date_column: str | None = Field(
        default=None,
        description="Column supplying recency, required by the 'most_recent' strategy.",
    )
    source_priority: list[str] | None = Field(
        default=None,
        description="Ordered source names preferred first, required by the 'source_priority' strategy.",
    )
    when: str | None = Field(
        default=None,
        description="Predicate over already-resolved fields gating whether this rule applies.",
    )       # predicate over already-resolved fields
    validate_with: str | None = Field(
        default=None,
        alias="validate",
        description="Name of a goldenflow validator that filters candidate values before survivorship.",
    )  # candidate-filter name (goldenflow validator)

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
    name: str = Field(
        description="Identifier for this group of columns that must survive together.",
    )
    columns: list[str] = Field(
        description="Related columns resolved as a unit so their values stay from a single source record.",
    )
    category: str | None = Field(
        default=None,
        description="Optional label categorizing the group for reporting.",
    )
    strategy: str = Field(
        default="most_complete",
        description="Survivorship strategy applied to the group as a whole when choosing the winning record.",
    )
    date_column: str | None = Field(
        default=None,
        description="Column supplying recency, required by the group's 'most_recent' strategy.",
    )
    source_priority: list[str] | None = Field(
        default=None,
        description="Ordered source names preferred first, required by the group's 'source_priority' strategy.",
    )
    anchor: str | None = Field(
        default=None,
        description="Column whose non-null presence selects the source record, required by and only valid with the 'anchor' strategy.",
    )
    allow_fill: bool = Field(
        default=False,
        description="When true, individually missing values in the winning record may be filled from other records.",
    )

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
    default_strategy: str | None = Field(
        default=None,
        description="Survivorship strategy applied to any field without its own rule; required unless 'default' is set.",
    )
    default: GoldenFieldRule | None = Field(
        default=None,
        description="Full default rule whose strategy backfills default_strategy when the latter is unset.",
    )
    field_rules: dict[str, GoldenFieldRule | list[GoldenFieldRule]] = Field(
        default_factory=dict,
        description="Per-column survivorship rules, or an ordered list of conditional rules ending in a default clause.",
    )
    field_groups: list[GoldenGroupRule] = Field(
        default_factory=list,
        description="Groups of columns resolved together so their values stay mutually consistent.",
    )
    field_group_detection: bool = Field(
        default=False,
        description="Enables automatic discovery of related column groups to resolve as units.",
    )
    max_cluster_size: int = Field(
        default=100,
        description="Cluster size above which auto-split intervenes to break up likely over-merged clusters.",
    )
    auto_split: bool = Field(
        default=True,
        description="Enables splitting oversized clusters into tighter subclusters before building golden records.",
    )
    quality_weighting: bool = Field(
        default=True,
        description="Weights survivorship choices by per-source completeness and quality signals.",
    )
    weak_cluster_threshold: float = Field(
        default=0.3,
        description="Cohesion score below which a cluster is flagged as weak and handled more conservatively.",
    )
    # #726: cap on cumulative auto-split edge-work. None => auto-scaled
    # max(5_000_000, n_rows * 5). Raise this (or env
    # GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET) if a loud "clusters left oversized"
    # warning fires on a legitimately dense dataset. Precedence: this field >
    # env > auto-scaled.
    split_edge_budget: int | None = Field(
        default=None,
        description="Cap on cumulative edge work spent auto-splitting clusters; None auto-scales from the row count.",
    )
    # v1.18: post-cluster golden-rules refinement. When True, after
    # clustering the pipeline runs `refine_golden_rules` against the
    # cluster output + column profiles to pick per-field strategies
    # informed by within-cluster spread, per-source completeness, etc.
    # Default False to preserve existing behavior; opt-in for v1.18 users.
    # Spec: docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md
    adaptive: bool = Field(
        default=False,
        description="Enables post-cluster refinement that re-picks per-field strategies from cluster health and profiles.",
    )

    # v1.20.x (#430): LLM fallback for ambiguous fields. When True and
    # the heuristic refiner returns None for a field (no rule fires),
    # dispatch one LLM call per field to pick a strategy. Cached by
    # (dataset, field). BudgetTracker integration via the existing
    # `BudgetConfig`-attached scorer config (set on `match_settings`).
    # Soft-fails: no API key / budget exhausted / invalid response
    # -> falls back to the base default_strategy.
    use_llm_for_ambiguous: bool = Field(
        default=False,
        description="Falls back to one cached LLM call per field to pick a strategy when the heuristic refiner is undecided.",
    )

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
    cluster_overrides: dict[int, dict[str, GoldenFieldRule]] | None = Field(
        default=None,
        description="Per-cluster field rules that supersede the top-level rules for the named clusters only.",
    )

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
    column: str = Field(
        description="Column the validation rule is checked against.",
    )
    rule_type: Literal["regex", "min_length", "max_length", "not_null", "in_set", "format"] = Field(
        description="Kind of check applied to the column's values.",
    )
    params: dict = Field(
        default_factory=dict,
        description="Rule-specific parameters, such as the pattern, length bound, or allowed set.",
    )
    action: Literal["null", "quarantine", "flag"] = Field(
        default="flag",
        description="What happens to a failing value: null it out, quarantine the row, or just flag it.",
    )


class ValidationConfig(BaseModel):
    rules: list[ValidationRuleConfig] = Field(
        default_factory=list,
        description="Per-column validation rules run against the input before matching.",
    )
    auto_fix: bool = Field(
        default=True,
        description="Runs GoldenFlow auto-fix on the data before validation executes.",
    )  # whether to run auto-fix before validation


class QualityConfig(BaseModel):
    """GoldenCheck integration config for enhanced data quality."""
    enabled: bool = Field(
        default=True,
        description="Enables GoldenCheck quality scanning and fixes; auto-detected true when goldencheck is installed.",
    )       # auto-detected: True if goldencheck installed
    mode: str = Field(
        default="announced",
        description="How quality findings are surfaced: 'silent', 'announced', or 'disabled'.",
    )    # "silent" | "announced" | "disabled"
    fix_mode: str = Field(
        default="safe",
        description="How aggressively quality fixes are applied: 'safe', 'moderate', or 'none'.",
    )     # "safe" | "moderate" | "none"
    domain: str | None = Field(
        default=None,
        description="Domain pack tuning the quality checks, such as 'healthcare', 'finance', or 'ecommerce'.",
    )  # "healthcare" | "finance" | "ecommerce"

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
    autoconfig_force_exclude: list[str] = Field(
        default_factory=list,
        description="Columns always excluded from matching regardless of auto-detection.",
    )
    autoconfig_force_include: list[str] = Field(
        default_factory=list,
        description="Columns rescued from any auto-exclusion; wins on conflict with force_exclude.",
    )


class TransformConfig(BaseModel):
    """GoldenFlow integration config for data transformation."""
    enabled: bool = Field(
        default=True,
        description="Enables GoldenFlow data transformation; auto-detected true when goldenflow is installed.",
    )       # auto-detected: True if goldenflow installed
    mode: Literal["silent", "announced", "disabled"] = Field(
        default="announced",
        description="How applied transforms are surfaced: 'silent', 'announced', or 'disabled'.",
    )


class StandardizationConfig(BaseModel):
    rules: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-column ordered standardizer names applied before matching.",
    )

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
    path: str = Field(
        description="Filesystem path to the input data file.",
    )
    id_column: str | None = Field(
        default=None,
        description="Column holding a stable record identifier; a row index is used when unset.",
    )
    source_label: str | None = Field(
        default=None,
        description="Human-readable label attached to records from this file.",
    )
    source_name: str | None = Field(
        default=None,
        description="Source name recorded on each record for provenance and source-priority survivorship.",
    )
    column_map: dict[str, str] | None = Field(
        default=None,
        description="Renames raw file columns to canonical names before matching.",
    )
    delimiter: str = Field(
        default=",",
        description="Field delimiter for delimited text files.",
    )
    encoding: str = Field(
        default="utf8",
        description="Character encoding used to decode the file.",
    )
    sheet: str | None = Field(
        default=None,
        description="Worksheet name to read from an Excel workbook.",
    )
    parse_mode: str = Field(
        default="auto",
        description="How the file is parsed: auto, delimited, fixed_width, key_value, block, or entity_extract.",
    )  # auto, delimited, fixed_width, key_value, block, entity_extract
    header_row: int | None = Field(
        default=None,
        description="Zero-based row index that holds column headers.",
    )
    has_header: bool | None = Field(
        default=None,
        description="Whether the file has a header row; None lets the parser infer it.",
    )
    skip_rows: list[int] | None = Field(
        default=None,
        description="Row indices to skip while reading, such as banner or junk lines.",
    )


class InputConfig(BaseModel):
    files: list[InputFileConfig] = Field(
        default_factory=list,
        description="Input files to load and combine, used for deduplication across one or more sources.",
    )
    file_a: InputFileConfig | None = Field(
        default=None,
        description="First file in a two-source record-linkage (match) run.",
    )
    file_b: InputFileConfig | None = Field(
        default=None,
        description="Second file matched against file_a in a two-source record-linkage run.",
    )


# ── OutputConfig ────────────────────────────────────────────────────────────


class OutputConfig(BaseModel):
    path: str | None = Field(
        default=None,
        description="Destination file path for the primary results output.",
    )
    format: str | None = Field(
        default=None,
        description="Output file format (e.g. csv or parquet); inferred from the path when unset.",
    )
    directory: str | None = Field(
        default=None,
        description="Directory the run's output artifacts are written into.",
    )
    run_name: str | None = Field(
        default=None,
        description="Name identifying this run, used to key output subdirectories and lineage.",
    )
    # When True, the lineage sidecar gains a `golden_records` section with
    # per-field provenance (value + source_row_id of the winning record).
    # Default off: at large scale this materializes one provenance object per
    # cluster + a large JSON sidecar. The vectorized batch builder makes it
    # feasible (per-field source_row_id, no per-row candidate list).
    lineage_provenance: bool = Field(
        default=False,
        description="Adds per-field golden-record provenance (winning value plus source row id) to the lineage sidecar.",
    )


# ── LLM Budget / Scorer Config ────────────────────────────────────────────


class BudgetConfig(BaseModel):
    max_cost_usd: float | None = Field(
        default=None,
        description="Hard cap on total LLM spend for the run; calls stop once it is reached.",
    )
    max_calls: int | None = Field(
        default=None,
        description="Hard cap on the number of LLM requests for the run.",
    )
    escalation_model: str | None = Field(
        default=None,
        description="Pricier model borderline pairs are escalated to for a second opinion.",
    )
    escalation_band: list[float] = Field(
        default_factory=lambda: [0.80, 0.90],
        description="Score band [low, high] whose pairs are escalated to the pricier model.",
    )
    escalation_budget_pct: float = Field(
        default=20,
        description="Percentage of the budget reserved for escalation to the pricier model.",
    )
    warn_at_pct: float = Field(
        default=80,
        description="Percentage of the budget spent at which a warning is emitted.",
    )


class LLMScorerConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Turns on LLM scoring of borderline candidate pairs.",
    )
    provider: str | None = Field(
        default=None,
        description="LLM provider ('openai' or 'anthropic'); auto-detected from credentials when None.",
    )  # "openai" or "anthropic", auto-detected if None
    model: str | None = Field(
        default=None,
        description="LLM model name (e.g. 'gpt-4o-mini'); auto-detected when None.",
    )  # e.g. "gpt-4o-mini", auto-detected if None
    auto_threshold: float = Field(
        default=0.95,
        description="Score above which pairs are auto-accepted without an LLM call.",
    )  # auto-accept pairs above this
    candidate_lo: float = Field(
        default=0.75,
        description="Lower bound of the score band whose pairs are sent to the LLM.",
    )  # lower bound of LLM scoring range
    candidate_hi: float = Field(
        default=0.95,
        description="Upper bound of the score band whose pairs are sent to the LLM.",
    )  # upper bound (same as auto_threshold)
    batch_size: int = Field(
        default=75,
        description="Number of pairs packed into a single LLM request.",
    )
    max_workers: int = Field(
        default=5,
        description="Number of concurrent LLM requests.",
    )  # concurrent LLM requests
    calibration_sample_size: int = Field(
        default=100,
        description="Pairs sampled per calibration round to tune the accept threshold.",
    )  # pairs per calibration round
    calibration_max_rounds: int = Field(
        default=5,
        description="Maximum threshold-calibration rounds before stopping.",
    )  # max calibration iterations
    calibration_convergence_delta: float = Field(
        default=0.01,
        description="Calibration stops once the threshold shift between rounds falls below this.",
    )  # stop when threshold shift < this
    budget: BudgetConfig | None = Field(
        default=None,
        description="Cost and call limits governing LLM usage; None means unbounded.",
    )
    mode: str = Field(
        default="pairwise",
        description="LLM scoring mode: 'pairwise' per-pair scoring or 'cluster' in-context block clustering.",
    )  # "pairwise" (legacy) or "cluster" (in-context LLM clustering)
    cluster_max_size: int = Field(
        default=100,
        description="Maximum records per LLM cluster block in cluster mode.",
    )  # max records per LLM cluster block
    cluster_min_size: int = Field(
        default=5,
        description="Block size below which cluster mode falls back to pairwise scoring.",
    )  # below this, fall back to pairwise


# ── Domain Extraction Config ──────────────────────────────────────────────


class DomainConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Turns on domain feature extraction as a pipeline step before matchkeys.",
    )
    mode: str | None = Field(
        default=None,
        description="Domain to extract for ('product', 'person', 'bibliographic', 'company', or 'auto' to detect).",
    )  # "product", "person", "bibliographic", "company", "auto"
    confidence_threshold: float = Field(
        default=0.3,
        description="Extraction confidence below which a record is routed to the LLM instead.",
    )  # below this, route to LLM
    llm_validation: bool = Field(
        default=True,
        description="Uses the LLM to validate low-confidence extractions.",
    )  # whether to use LLM for low-confidence extractions
    budget: BudgetConfig | None = Field(
        default=None,
        description="Cost and call limits for the domain-extraction LLM calls.",
    )  # reuses budget config


# ── Learning Memory Config ─────────────────────────────────────────────────


class LearningConfig(BaseModel):
    """Learning Memory learning parameters."""
    threshold_min_corrections: int = Field(
        default=10,
        description="Minimum stored corrections before learned thresholds are tuned from them.",
    )
    weights_min_corrections: int = Field(
        default=50,
        description="Minimum stored corrections before learned field weights are tuned from them.",
    )


class MemoryConfig(BaseModel):
    """Learning Memory configuration."""
    enabled: bool = Field(
        default=True,
        description="Turns on Learning Memory so stored corrections and learned thresholds feed back into runs.",
    )
    backend: str = Field(
        default="sqlite",
        description="Storage backend for the memory store ('sqlite' or 'postgres').",
    )
    path: str = Field(
        default=".goldenmatch/memory.db",
        description="SQLite file path for the memory store when the backend is sqlite.",
    )
    connection: str | None = Field(
        default=None,
        description="Database connection string used when the backend is postgres.",
    )
    trust: dict[str, float] = Field(
        default_factory=lambda: {"human": 1.0, "agent": 0.5},
        description="Per-source trust weights that scale how strongly a correction's origin influences learning.",
    )
    learning: LearningConfig = Field(
        default_factory=LearningConfig,
        description="Thresholds governing how many corrections are needed before rules are learned.",
    )
    reanchor: bool = Field(
        default=True,
        description="Re-anchors stored corrections to current rows by record hash so they survive row reordering.",
    )
    dataset: str | None = Field(
        default=None,
        description="Dataset name scoping corrections so unrelated datasets do not share memory.",
    )
    table_prefix: str = Field(
        default="",
        description="Prefix applied to memory table names, letting multiple stores share one database.",
    )

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

    enabled: bool = Field(
        default=False,
        description="Turns on cross-device/channel stitching so a caller can join a person's records across channels.",
    )
    # Columns whose shared non-null value is a near-certain same-person signal.
    # Empty -> stitching.DEFAULT_DEVICE_KEYS.
    device_keys: list[str] = Field(
        default_factory=list,
        description="Columns whose shared non-null value is a near-certain same-person signal; empty uses the defaults.",
    )
    # Column carrying an explicit channel label per record.
    channel_column: str = Field(
        default="channel",
        description="Column carrying an explicit channel label per record.",
    )
    # Exact ``__source__`` -> channel overrides (beats the substring hints).
    channel_map: dict[str, str] = Field(
        default_factory=dict,
        description="Exact source-name to channel overrides that beat substring channel inference.",
    )
    # Per-channel trust weight in (0, 1]. Empty -> stitching.DEFAULT_CHANNEL_TRUST.
    channel_trust: dict[str, float] = Field(
        default_factory=dict,
        description="Per-channel trust weight in (0, 1] used to downweight cross-channel matches; empty uses the defaults.",
    )
    # Scale probabilistic match scores by the channels' trust factor.
    adjust_cross_channel: bool = Field(
        default=True,
        description="Scales probabilistic match scores by the two channels' trust factor.",
    )
    # Drop probabilistic stitch edges below this (post-adjustment) weight.
    prob_threshold: float = Field(
        default=0.0,
        description="Drops probabilistic stitch edges whose post-adjustment weight falls below this.",
    )


class SurvivorshipConfig(BaseModel):
    """Golden-record survivorship configuration (#1111, epic #1108).

    Drives ``goldenmatch.identity.survivorship.build_golden_with_provenance``:
    which merge strategy wins each field, the column carrying a per-record
    timestamp (for ``most_recent`` + provenance), and whether to learn per-field
    strategies from steward ``FIELD_CORRECT`` corrections. Config plumbing only.
    """

    # Per-field merge strategy overrides (column -> strategy name). Unlisted
    # columns use ``default_strategy``.
    field_strategies: dict[str, str] = Field(
        default_factory=dict,
        description="Per-column survivorship strategy overrides; unlisted columns fall back to default_strategy.",
    )
    default_strategy: str = Field(
        default="most_complete",
        description="Survivorship strategy applied to any column without its own override.",
    )
    # Column carrying a per-record timestamp (enables most_recent + per-cell
    # timestamp provenance).
    timestamp_column: str | None = Field(
        default=None,
        description="Column with a per-record timestamp enabling most_recent survivorship and per-cell provenance.",
    )
    # Fold learned per-field strategies (from FIELD_CORRECT corrections) into
    # ``field_strategies``. Consumed by a caller/learning pass, not on its own.
    learn_from_corrections: bool = Field(
        default=False,
        description="Folds per-field strategies learned from steward corrections into field_strategies.",
    )


class StabilizationConfig(BaseModel):
    """Cross-run entity stabilization -- Identity v3 (#1112, epic #1108).

    Drives ``goldenmatch.identity.stabilize.stabilize_identities``: how many
    distinct runs of cross-entity overlap trigger an auto-consolidation, which
    survivor wins, and a minimum edge score. Config plumbing only.
    """

    # Distinct runs of cross-entity overlap evidence before a pair consolidates.
    min_runs: int = Field(
        default=3,
        description="Distinct runs of cross-entity overlap evidence required before two entities auto-consolidate.",
    )
    # Survivor selection: most_records | oldest | newest | lowest_id.
    winner_strategy: str = Field(
        default="most_records",
        description="Which entity survives a consolidation: most_records, oldest, newest, or lowest_id.",
    )
    # Minimum max-edge score for a pair to count as overlap.
    min_score: float = Field(
        default=0.0,
        description="Minimum max-edge score for a cross-entity pair to count as overlap evidence.",
    )

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

    auto_apply: bool = Field(
        default=True,
        description="Whether a 'distinct' mediation verdict actually splits the record out or is only recorded.",
    )


class IdentityConfig(BaseModel):
    """Identity Graph configuration.

    Spec: ``docs/superpowers/specs/2026-05-12-identity-graph-design.md``
    """
    enabled: bool = Field(
        default=False,
        description="Turns on the durable identity graph that assigns stable entity ids across runs after clustering.",
    )
    backend: str = Field(
        default="sqlite",
        description="Storage backend for the identity graph ('sqlite' or 'postgres').",
    )
    path: str = Field(
        default=".goldenmatch/identity.db",
        description="SQLite file path for the identity graph when the backend is sqlite.",
    )
    connection: str | None = Field(
        default=None,
        description="Database connection string used when the backend is postgres.",
    )
    dataset: str | None = Field(
        default=None,
        description="Dataset name scoping identities so unrelated datasets do not share entity ids.",
    )
    source_pk_column: str | None = Field(
        default=None,
        description="Column supplying each record's source primary key for stable record ids; a payload hash is used when unset.",
    )
    emit_singletons: bool = Field(
        default=True,
        description="Whether single-record clusters also get a durable entity id.",
    )
    # v2.1: when a cluster's confidence drops below this, the resolver flags the
    # bottleneck pair as a ``conflicts_with`` edge so a steward sees it for
    # review. 0.6 mirrors the existing ``weak_cluster_threshold`` family. Set
    # to 0 to disable auto-detection.
    weak_confidence_threshold: float = Field(
        default=0.6,
        description="Cluster confidence below which the bottleneck pair is flagged as a conflict for steward review; 0 disables it.",
    )
    # #1110: cross-device / channel stitching (CDP/MDM epic #1108). None ->
    # stitching is not configured (the default; identity resolution is
    # unchanged).
    stitching: ChannelStitchConfig | None = Field(
        default=None,
        description="Cross-device/channel stitching configuration; None leaves identity resolution unchanged.",
    )
    # #1111: golden-record survivorship (strategies + per-cell provenance).
    # None -> default flat golden record (unchanged).
    survivorship: SurvivorshipConfig | None = Field(
        default=None,
        description="Identity golden-record survivorship configuration; None keeps the default flat golden record.",
    )
    # #1112: cross-run entity stabilization (Identity v3). None -> no stabilize
    # pass configured (the default).
    stabilization: StabilizationConfig | None = Field(
        default=None,
        description="Cross-run entity stabilization configuration; None runs no stabilize pass.",
    )
    # #1113: conflict mediation workflow. None -> not configured (default).
    mediation: MediationConfig | None = Field(
        default=None,
        description="Conflict mediation workflow configuration; None leaves mediation unconfigured.",
    )

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
    matchkeys: list[MatchkeyConfig] = Field(
        description="Matchkeys defining how records are compared and declared the same.",
    )


# ── GoldenMatchConfig (top-level) ──────────────────────────────────────────


class DistributedRoutingConfig(BaseModel):
    """Per-stage distributed-routing pins. ``auto`` lets the planner decide;
    an explicit value pins the stage and is surfaced by the linter."""

    scoring: Literal["auto", "distributed", "in_process"] = Field(
        default="auto",
        description="Pins where pair scoring runs; 'auto' lets the planner choose distributed vs in-process.",
    )
    clustering: Literal["auto", "distributed_wcc", "in_memory_scipy"] = Field(
        default="auto",
        description="Pins the clustering engine; 'auto' lets the planner choose distributed WCC vs in-memory scipy.",
    )
    golden: Literal["auto", "distributed", "in_process"] = Field(
        default="auto",
        description="Pins where golden-record building runs; 'auto' lets the planner choose distributed vs in-process.",
    )


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
    input: InputConfig | None = Field(
        default=None,
        description="Input files to load; omit when passing a DataFrame directly to the API.",
    )
    output: OutputConfig = Field(
        default_factory=lambda: OutputConfig(),
        description="Where and how results are written.",
    )
    match_settings: MatchSettingsConfig | None = Field(
        default=None,
        description="Nested matchkeys container; an alternative to the top-level matchkeys field.",
    )
    matchkeys: list[MatchkeyConfig] | None = Field(
        default=None,
        description="Matchkeys defining how records are compared; takes precedence over match_settings.",
    )
    blocking: BlockingConfig | None = Field(
        default=None,
        description="Candidate-generation configuration; required when any matchkey is weighted or probabilistic.",
    )
    golden_rules: GoldenRulesConfig | None = Field(
        default=None,
        description="Survivorship rules for building one golden record per cluster.",
    )
    standardization: StandardizationConfig | None = Field(
        default=None,
        description="Per-column standardizers applied before matching.",
    )
    validation: ValidationConfig | None = Field(
        default=None,
        description="Validation rules and auto-fix settings applied to the input.",
    )
    quality: QualityConfig | None = Field(
        default=None,
        description="GoldenCheck data-quality integration settings.",
    )
    transform: TransformConfig | None = Field(
        default=None,
        description="GoldenFlow data-transformation integration settings.",
    )
    llm_boost: bool = Field(
        default=False,
        description="Enables active-learning LLM boosting of borderline matches.",
    )
    llm_scorer: LLMScorerConfig | None = Field(
        default=None,
        description="LLM pair-scoring configuration for borderline candidates.",
    )
    llm_auto: bool = Field(
        default=False,
        description="Lets auto-config enable and configure the LLM scorer automatically.",
    )
    domain: DomainConfig | None = Field(
        default=None,
        description="Domain feature-extraction configuration used before matchkeys.",
    )
    backend: str | None = Field(
        default=None,
        description="Execution backend: None (default Polars in-memory), 'ray', 'duckdb', 'chunked', or 'bucket'.",
    )  # None (default Polars), "ray", "duckdb"
    distributed_routing: DistributedRoutingConfig | None = Field(
        default=None,
        description="Per-stage distributed-routing pins; None lets the planner decide every stage.",
    )
    semantic_blocking: SemanticBlockingConfig | None = Field(
        default=None,
        description="Opt-in semantic candidate-generation keys (ANN, initialism, alias) unioned into blocking.",
    )
    allow_slow_path: bool = Field(
        default=False,
        description="Permits falling back to a slower non-fused execution path when the fast path is ineligible.",
    )
    # Execution mode. "standard" (default) = the in-memory/Ray pipeline,
    # bit-identical artifacts. "scale" = the DataFusion spine (out-of-core,
    # deterministic + semantically correct but NOT bit-identical to standard;
    # MAX dedup, reduced feature surface). The spine entry
    # (backends/datafusion_spine.run_spine) enforces the scale-mode feature
    # gate; this field is the opt-in signal.
    mode: Literal["standard", "scale"] = Field(
        default="standard",
        description="Execution mode: 'standard' in-memory/Ray (bit-identical) or 'scale' DataFusion spine (out-of-core, reduced features).",
    )
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
    throughput: ThroughputConfig | None = Field(
        default=None,
        description="Opt-in sketch-then-verify throughput tier for high-recall, low-cost dedup.",
    )
    memory: MemoryConfig | None = Field(
        default=None,
        description="Learning Memory configuration for persisting corrections and learned thresholds.",
    )
    identity: IdentityConfig | None = Field(
        default=None,
        description="Identity Graph configuration for stable cross-run entity ids.",
    )
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
