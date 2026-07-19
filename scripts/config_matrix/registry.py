"""Per-package config-matrix specs. One entry per suite package; the shared
renderer (render.py) composes each package's generated block from whatever
sections it declares."""
from __future__ import annotations

from dataclasses import dataclass, field

_RUST = "packages/rust/extensions"


@dataclass(frozen=True)
class PackageSpec:
    name: str
    doc_path: str
    nav_group: str
    env_prefix: str
    src_dirs: list[str]
    schema_roots: list[str] = field(default_factory=list)
    constructors: list[str] = field(default_factory=list)
    cli_module: str | None = None
    mcp_module: str | None = None
    # (title, "module:attr", applies, gloss?) where gloss is None / {value: text}
    # / "doc" (derive from item docstrings) / ("doc", {overrides}).
    vocabs: list[tuple] = field(default_factory=list)
    vocab_warmup: list[str] = field(default_factory=list)
    tuning_link: str | None = None
    # Env names other docs may reference despite not being read in code (rare;
    # the migration-page + removal-context heuristics cover most). Explicit escape hatch.
    env_allow: tuple[str, ...] = ()
    # Once a package's introspectable knobs are all explained, flip this on to
    # BLOCK on any regression (a new field/option/tool without a description).
    # Packages still filling the long tail leave it False (advisory coverage only).
    require_full_coverage: bool = False
    # (topical-page, canonical-set target) pairs: every value of the canonical set
    # must be documented in that page too, so a new scorer/strategy is propagated
    # to its reference doc, not just the matrix. target is "module:CONST" (frozenset
    # / enum / Literal alias) or "module:Model.field" (a Literal field).
    doc_coverage: tuple[tuple[str, str], ...] = ()


REGISTRY: dict[str, PackageSpec] = {
    "goldenmatch": PackageSpec(
        name="goldenmatch",
        require_full_coverage=True,
        doc_path="docs-site/goldenmatch/config-matrix.mdx",
        nav_group="GoldenMatch",
        env_prefix="GOLDENMATCH_",
        src_dirs=["packages/python/goldenmatch/goldenmatch", _RUST],
        schema_roots=["goldenmatch.config.schemas:GoldenMatchConfig"],
        cli_module="goldenmatch.cli.main",
        mcp_module="goldenmatch.mcp.server",
        vocabs=[
            ("Scorers", "goldenmatch.config.schemas:VALID_SCORERS", "`MatchkeyField.scorer` / `NegativeEvidenceField.scorer`", {
                "exact": "Exact string equality after transforms.",
                "jaro_winkler": "Jaro-Winkler string similarity, strong on short names and typos.",
                "levenshtein": "Normalized Levenshtein edit distance.",
                "token_sort": "Token-sorted ratio; order-insensitive multi-word compare.",
                "soundex_match": "Phonetic Soundex-code equality.",
                "embedding": "Cosine similarity of a model embedding of the field.",
                "record_embedding": "Cosine similarity of an embedding over several columns.",
                "ensemble": "Weighted blend of several sub-scorers.",
                "dice": "Dice coefficient over character bigrams.",
                "jaccard": "Jaccard overlap of token/character sets.",
                "qgram": "Q-gram (n-gram) overlap similarity.",
                "date": "Damerau-Levenshtein over canonical ISO date digits; typo-tolerant date compare.",
                "phash": "Perceptual-hash Hamming similarity for images.",
                "audio_fp": "Audio-fingerprint similarity for sound clips.",
                "radial": "Rotation/crop-invariant radial-variance image similarity.",
                "initialism_match": "Matches initials/acronyms against their expansions.",
                "alias_match": "Matches known name aliases and nicknames (Bob <-> Robert).",
            }),
            ("Simple transforms", "goldenmatch.config.schemas:VALID_SIMPLE_TRANSFORMS", "`transforms` chains", {
                "lowercase": "Lowercase the value.", "uppercase": "Uppercase the value.",
                "strip": "Trim leading/trailing whitespace.", "strip_all": "Remove all whitespace.",
                "normalize_whitespace": "Collapse runs of whitespace to single spaces.",
                "alpha_only": "Keep only alphabetic characters.", "digits_only": "Keep only digits.",
                "first_token": "Keep the first whitespace token.", "last_token": "Keep the last whitespace token.",
                "token_sort": "Sort the whitespace tokens alphabetically.",
                "soundex": "Soundex phonetic encoding.", "metaphone": "Metaphone phonetic encoding.",
            }),
            ("Survivorship strategies", "goldenmatch.config.schemas:VALID_STRATEGIES", "`GoldenFieldRule.strategy`", {
                "most_complete": "Value from the record with the fewest nulls.",
                "most_recent": "Value from the newest record (needs date_column).",
                "source_priority": "Value from the highest-priority source (needs source_priority).",
                "majority_vote": "Most frequent value across the cluster.",
                "first_non_null": "First non-null value in row order.",
                "longest_value": "The longest, most detailed value.",
                "unanimous_or_null": "The value only if every record agrees, else null.",
                "confidence_majority": "Value backed by the highest total match confidence.",
            }),
            ("Group survivorship strategies", "goldenmatch.config.schemas:_GROUP_STRATEGIES", "`GoldenGroupRule.strategy`", {
                "most_complete": "Take all group columns from the most-complete row.",
                "most_recent": "Take the group from the newest row.",
                "source_priority": "Take the group from the highest-priority source.",
                "anchor": "Take all group columns from the anchor column's winning row.",
            }),
            ("Standardizers", "goldenmatch.config.schemas:VALID_STANDARDIZERS", "`StandardizationConfig.rules`", {
                "email": "Lowercase and canonicalize an email address.",
                "name_proper": "Proper-case a name.", "name_upper": "Uppercase a name.", "name_lower": "Lowercase a name.",
                "phone": "Normalize a phone number.", "zip5": "Normalize a US ZIP to 5 digits.",
                "address": "Normalize a postal address.", "state": "Normalize a US state to its 2-letter code.",
                "strip": "Trim whitespace.", "trim_whitespace": "Collapse and trim whitespace.",
            }),
            ("Matchkey types", "goldenmatch.config.schemas:_VALID_MK_TYPES", "`MatchkeyConfig.type`", {
                "exact": "All fields must match exactly; fastest, no scoring.",
                "weighted": "Weighted mean of per-field scores compared to a threshold.",
                "probabilistic": "Fellegi-Sunter scoring with EM-learned agreement weights.",
            }),
            ("Backends", "goldenmatch.core.execution_plan:BackendName", "`GoldenMatchConfig.backend` / `--backend`", {
                "polars-direct": "Straight in-memory Polars execution.",
                "bucket": "Memory-bounded field-hash bucket scorer (the in-memory default route).",
                "chunked": "Chunked in-memory execution to cap peak memory.",
                "duckdb": "DuckDB-backed out-of-core execution.",
                "ray": "Distributed execution on a Ray cluster.",
            }),
            ("Clustering strategies", "goldenmatch.core.execution_plan:ClusteringStrategy", "planner cluster route", {
                "in_memory": "In-memory union-find clustering.",
                "partitioned_union_find": "Disk-partitioned union-find for large pair sets.",
                "streaming_cc": "Streaming connected-components.",
                "distributed_wcc": "Distributed weakly-connected-components on a cluster.",
            }),
            ("Planning efforts", "goldenmatch.core.autoconfig_controller:_PLANNING_EFFORTS", "`planning_effort`", {
                "fast": "Single cheap auto-config pass.",
                "normal": "Default interactive auto-config budget (byte-identical to historical).",
                "thinking": "Extra refit iterations plus full-frame blocking measurement.",
                "einstein": "Maximum auto-config search; slowest and most thorough.",
            }),
        ],
        tuning_link="/goldenmatch/tuning",
        doc_coverage=(
            ("scoring.mdx", "goldenmatch.config.schemas:VALID_SCORERS"),
            ("blocking.mdx", "goldenmatch.config.schemas:BlockingConfig.strategy"),
            ("configuration.mdx", "goldenmatch.config.schemas:VALID_SIMPLE_TRANSFORMS"),
            ("configuration.mdx", "goldenmatch.config.schemas:VALID_STRATEGIES"),
            ("configuration.mdx", "goldenmatch.config.schemas:VALID_STANDARDIZERS"),
        ),
    ),
    "goldencheck": PackageSpec(
        name="goldencheck",
        require_full_coverage=True,
        doc_path="docs-site/goldencheck/config-matrix.mdx",
        nav_group="GoldenCheck",
        env_prefix="GOLDENCHECK_",
        src_dirs=["packages/python/goldencheck/goldencheck", _RUST],
        schema_roots=["goldencheck.config.schema:GoldenCheckConfig"],
        cli_module="goldencheck.cli.main",
        mcp_module="goldencheck.mcp.server",
        vocabs=[
            ("Finding severity", "goldencheck.models.finding:Severity", "`Settings.severity_threshold` / `fail_on`", {
                "INFO": "Informational finding; never fails the run.",
                "WARNING": "Advisory finding; fails only when fail_on=warning.",
                "ERROR": "Highest severity; fails the run at the default fail_on=error.",
            }),
            ("Denial-constraint operators", "goldencheck.denial.models:Op", "mined denial constraints", {
                "=": "Equal.", "≠": "Not equal.",
                "<": "Less than.", "≤": "Less than or equal.",
                ">": "Greater than.", "≥": "Greater than or equal.",
            }),
        ],
    ),
    "goldenflow": PackageSpec(
        name="goldenflow",
        require_full_coverage=True,
        doc_path="docs-site/goldenflow/config-matrix.mdx",
        nav_group="GoldenFlow",
        env_prefix="GOLDENFLOW_",
        src_dirs=["packages/python/goldenflow/goldenflow", _RUST],
        schema_roots=["goldenflow.config.schema:GoldenFlowConfig"],
        cli_module="goldenflow.cli.main",
        mcp_module="goldenflow.mcp.server",
        vocabs=[
            ("Transform ops", "goldenflow.transforms:list_transforms", "`TransformSpec.ops`", ("doc", {
                "strip": "Trim leading/trailing whitespace.",
                "normalize_unicode": "Normalize to Unicode NFC form.",
                "lowercase": "Lowercase the value.", "uppercase": "Uppercase the value.",
                "title_case": "Title-case the value.", "collapse_whitespace": "Collapse runs of whitespace to single spaces.",
                "remove_punctuation": "Strip punctuation characters.",
                "date_iso8601": "Parse/format a date as ISO-8601.", "date_us": "Parse a US (MM/DD/YYYY) date.",
                "date_eu": "Parse a European (DD/MM/YYYY) date.", "age_from_dob": "Compute age in years from a date of birth.",
                "phone_digits": "Keep only the phone number's digits.", "phone_e164": "Format a phone number as E.164.",
                "phone_national": "Format a phone number in national format.", "phone_validate": "Flag whether the phone number is valid.",
                "ssn_validate": "Flag whether the value is a valid US Social Security Number.",
            })),
            ("Domain packs", "goldenflow.domains:_DOMAINS", "`learn_config(domain=...)`", {
                "people_hr": "People / HR field pack.", "healthcare": "Healthcare field pack.",
                "finance": "Financial-services field pack.", "ecommerce": "E-commerce / retail field pack.",
                "real_estate": "Real-estate field pack.", "carceral": "Corrections / justice field pack.",
            }),
            ("Canonicalize kinds", "goldenflow.canonicalize:CanonicalizeKind", "`canonicalize` op", {
                "email": "Canonical email form.", "phone": "Canonical phone form.",
                "name": "Canonical person-name form.", "postal": "Canonical postal-address form.",
            }),
        ],
        vocab_warmup=["goldenflow"],
    ),
    "goldenpipe": PackageSpec(
        name="goldenpipe",
        require_full_coverage=True,
        doc_path="docs-site/goldenpipe/config-matrix.mdx",
        nav_group="GoldenPipe",
        env_prefix="GOLDENPIPE_",
        src_dirs=["packages/python/goldenpipe/goldenpipe", _RUST],
        schema_roots=["goldenpipe.models.config:PipelineConfig"],
        cli_module="goldenpipe.cli.main",
        mcp_module="goldenpipe.mcp.server",
        vocabs=[
            ("Stage status", "goldenpipe.models.context:StageStatus", "per-stage result status", {
                "success": "Stage completed successfully.",
                "skipped": "Stage skipped by its skip_if condition.",
                "failed": "Stage raised an error.",
            }),
            ("Pipeline status", "goldenpipe.models.context:PipeStatus", "run result status", {
                "success": "Every stage succeeded.",
                "partial": "Some stages succeeded and some failed.",
                "failed": "One or more stages failed the run.",
            }),
            ("Column types", "goldenpipe.models.column_context:ColumnType", "column classification", {
                "name": "Person or entity name.", "email": "Email address.", "phone": "Phone number.",
                "date": "Date/time value.", "geo": "Geographic coordinate.", "address": "Postal address.",
                "zip": "Postal/ZIP code.", "identifier": "Unique identifier / key.",
                "numeric": "Numeric measure.", "string": "Free-form string.", "description": "Long free text.",
            }),
            ("Cardinality bands", "goldenpipe.models.column_context:CardinalityBand", "column cardinality band", {
                "": "Unset / unknown.", "low": "Few distinct values.", "mid": "Moderate distinct values.",
                "high": "Many distinct values (near-unique).", "skip": "Excluded from cardinality analysis.",
            }),
            ("Repair fixers", "goldenpipe.repair_host:FIXERS", "repair-op allowlist", {
                "fix_mojibake": "Repair UTF-8/Latin-1 mojibake.", "normalize_unicode": "Normalize to Unicode NFC.",
                "date_parse": "Parse a date into ISO form.", "email_normalize": "Lowercase/normalize an email.",
                "email_canonical": "Canonicalize an email (dedupe dots/plus).", "name_proper": "Proper-case a name.",
                "phone_national": "Normalize a phone to national format.", "zip_normalize": "Normalize a ZIP code.",
            }),
        ],
    ),
    "infermap": PackageSpec(
        name="infermap",
        require_full_coverage=True,
        doc_path="docs-site/infermap/config-matrix.mdx",
        nav_group="InferMap",
        env_prefix="INFERMAP_",
        src_dirs=["packages/python/infermap/infermap", _RUST],
        constructors=["infermap.engine:MapEngine"],
        cli_module="infermap.cli",
        mcp_module="infermap.mcp.server",
        vocabs=[
            ("Data types", "infermap.types:VALID_DTYPES", "`FieldInfo.dtype`", {
                "string": "Text.", "integer": "Whole number.", "float": "Decimal number.",
                "boolean": "True/false.", "date": "Calendar date.", "datetime": "Date and time.",
            }),
            ("Semantic pattern types", "infermap.scorers.pattern_type:SEMANTIC_TYPES", "pattern-type detection", {
                "email": "Email address.", "phone": "Phone number.", "url": "Web URL.",
                "uuid": "UUID.", "ip_v4": "IPv4 address.", "date_iso": "ISO-8601 date.",
                "zip_us": "US ZIP code.", "currency": "Currency amount.",
            }),
        ],
    ),
    "goldenanalysis": PackageSpec(
        name="goldenanalysis",
        require_full_coverage=True,
        doc_path="docs-site/goldenanalysis/config-matrix.mdx",
        nav_group="GoldenAnalysis",
        env_prefix="GOLDENANALYSIS_",
        src_dirs=["packages/python/goldenanalysis/goldenanalysis", _RUST],
        schema_roots=["goldenanalysis.models.policy:RegressionPolicy"],
        constructors=["goldenanalysis._api:analyze"],
        cli_module="goldenanalysis.cli.main",
        vocabs=[
            ("Metric direction", "goldenanalysis.models.report:Direction", "`Metric.direction`", {
                "higher_better": "A higher value is better; flag a regression on a decrease.",
                "lower_better": "A lower value is better; flag a regression on an increase.",
                "neutral": "No preferred direction; never flags a regression.",
            }),
            ("Regression baseline", "goldenanalysis.models.policy:Baseline", "`--baseline`", {
                "previous": "Compare against the immediately prior run.",
                "rolling_median": "Compare against the rolling median of recent runs.",
                "last_known_good": "Compare against the last run marked good.",
            }),
        ],
    ),
}
