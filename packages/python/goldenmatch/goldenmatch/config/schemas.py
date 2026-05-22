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
    "dice", "jaccard",
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
    levels: int = 2  # comparison levels for probabilistic: 2=agree/disagree, 3=agree/partial/disagree
    partial_threshold: float = 0.8  # score >= this = partial agree (when levels=3)
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

    @model_validator(mode="after")
    def _resolve_field_column(self) -> MatchkeyField:
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
    penalty: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_transforms_and_scorer(self) -> NegativeEvidenceField:
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
    # #126 / Wave D: NE applies to ``weighted`` + ``exact`` matchkey types
    # only. ``probabilistic`` (Fellegi-Sunter) matchkeys are intentionally
    # NOT extended with NE under v1.13 — the LLR-additivity of FS doesn't
    # cleanly compose with a flat penalty on the [0,1] score. See
    # ``docs/superpowers/specs/2026-05-21-ne-fs-investigation.md`` for the
    # formulation comparison; the Bayesian-factor (Formulation B) approach
    # becomes viable once #129's labeled-correction substrate is available.
    # Users who explicitly need NE-on-FS in v1.13 can opt into the
    # calibration-losing post-FS floor via
    # ``GOLDENMATCH_NE_FS_ESCAPE_MODE=floor`` (escape hatch; not the
    # default; semantics not preserved across versions).
    negative_evidence: list[NegativeEvidenceField] | None = None
    # Fellegi-Sunter EM parameters
    em_iterations: int = 20
    convergence_threshold: float = 0.001
    link_threshold: float | None = None  # auto-computed if None
    review_threshold: float | None = None  # auto-computed if None

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

    @model_validator(mode="after")
    def _validate_fields_nonempty(self) -> BlockingKeyConfig:
        if not self.fields:
            raise ValueError("Blocking key must have at least one field.")
        return self


class SortKeyField(BaseModel):
    column: str
    transforms: list[str] = Field(default_factory=list)


class CanopyConfig(BaseModel):
    fields: list[str]
    loose_threshold: float = 0.3
    tight_threshold: float = 0.7
    max_canopy_size: int = 500


class BlockingConfig(BaseModel):
    keys: list[BlockingKeyConfig] = []
    max_block_size: int = 5000
    skip_oversized: bool = False
    strategy: Literal["static", "adaptive", "sorted_neighborhood", "multi_pass", "ann", "canopy", "ann_pairs", "learned"] = "static"
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

    @model_validator(mode="after")
    def _validate_keys_or_passes(self) -> BlockingConfig:
        """Ensure at least keys or passes is provided for strategies that need them."""
        if self.auto_suggest:
            return self  # auto_suggest discovers keys at runtime
        # Strategies that don't need keys: ann, ann_pairs, canopy, learned,
        # sorted_neighborhood (uses sort_key instead)
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
        return self


# ── GoldenFieldRule / GoldenRulesConfig ─────────────────────────────────────


class GoldenFieldRule(BaseModel):
    strategy: str
    date_column: str | None = None
    source_priority: list[str] | None = None

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


class GoldenRulesConfig(BaseModel):
    default_strategy: str | None = None
    default: GoldenFieldRule | None = None
    field_rules: dict[str, GoldenFieldRule] = Field(default_factory=dict)
    max_cluster_size: int = 100
    auto_split: bool = True
    quality_weighting: bool = True
    weak_cluster_threshold: float = 0.3
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

    @field_validator("dataset")
    @classmethod
    def _reject_empty_dataset(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("MemoryConfig.dataset must be non-empty (or None)")
        return stripped


# ── MatchSettingsConfig ─────────────────────────────────────────────────────


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
