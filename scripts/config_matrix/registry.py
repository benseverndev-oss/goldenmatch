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
                "exact": {"meaning": "Exact string equality after transforms.", "range": "0 or 1", "best_for": "Email, phone, ID"},
                "jaro_winkler": {"meaning": "Jaro-Winkler edit distance with a shared-prefix bonus.", "range": "0.0-1.0", "best_for": "Names"},
                "levenshtein": {"meaning": "Normalized Levenshtein edit distance.", "range": "0.0-1.0", "best_for": "General strings"},
                "token_sort": {"meaning": "Sort the tokens, then ratio; order-insensitive.", "range": "0.0-1.0", "best_for": "Names, addresses"},
                "soundex_match": {"meaning": "Phonetic Soundex-code equality.", "range": "0 or 1", "best_for": "Names"},
                "embedding": {"meaning": "Cosine similarity of a model embedding of the field.", "range": "0.0-1.0", "best_for": "Semantic matching"},
                "record_embedding": {"meaning": "Cosine similarity of an embedding over several columns.", "range": "0.0-1.0", "best_for": "Cross-field semantic"},
                "ensemble": {"meaning": "Best of several sub-scorers (max of jaro_winkler / token_sort / soundex).", "range": "0.0-1.0", "best_for": "Names with reordering"},
                "dice": {"meaning": "Dice coefficient over character bigrams / bloom filters.", "range": "0.0-1.0", "best_for": "PPRL"},
                "jaccard": {"meaning": "Jaccard overlap of token/character sets or bloom filters.", "range": "0.0-1.0", "best_for": "PPRL"},
                "qgram": {"meaning": "Q-gram (n-gram) overlap similarity.", "range": "0.0-1.0", "best_for": "General strings, typos"},
                "date": {"meaning": "Damerau-Levenshtein over canonical ISO date digits; typo-tolerant.", "range": "0.0-1.0", "best_for": "Dates (dob, birth_date)"},
                "date_diff": {"meaning": "Day-distance banded similarity; magnitude-aware (a year gap is a weak partial, not a near-match). FS path.", "range": "0.0-1.0", "best_for": "Dates (dob, birth_date)"},
                "numeric_diff": {"meaning": "Banded numeric distance (abs/pct); magnitude-aware, so string-close numbers that are far apart no longer read as near-agreement. FS path.", "range": "0.0-1.0", "best_for": "Amounts, measurements, ages"},
                "geo_haversine": {"meaning": "Great-circle (haversine) distance banded to a similarity, on a single combined 'lat,long' field. FS path.", "range": "0.0-1.0", "best_for": "Coordinates (lat,long)"},
                "phash": {"meaning": "Perceptual-hash Hamming similarity.", "range": "0.0-1.0", "best_for": "Images"},
                "audio_fp": {"meaning": "Audio-fingerprint similarity.", "range": "0.0-1.0", "best_for": "Audio clips"},
                "radial": {"meaning": "Rotation/crop-invariant radial-variance similarity.", "range": "0.0-1.0", "best_for": "Rotated/cropped images"},
                "initialism_match": {"meaning": "Matches initials/acronyms against their expansions.", "range": "0 or 1", "best_for": "Acronyms, initials"},
                "alias_match": {"meaning": "Matches known name aliases and nicknames (Bob <-> Robert).", "range": "0 or 1", "best_for": "Names with nicknames"},
                "given_name_aliased_jw": {"meaning": "Jaro-Winkler with alias-aware exact collapse of given-name variants (Bob <-> Robert).", "range": "0.0-1.0", "best_for": "Given names"},
                "name_freq_weighted_jw": {"meaning": "Jaro-Winkler modulated by US-Census surname frequency (rare surnames weigh more).", "range": "0.0-1.0", "best_for": "Surnames"},
            }),
            ("Blocking strategies", "goldenmatch.config.schemas:BlockingConfig.strategy", "`BlockingConfig.strategy`", {
                "static": {"meaning": "Group records by an exact blocking key.", "best_for": "Clean data with reliable keys"},
                "adaptive": {"meaning": "Static plus recursive sub-blocking of oversized blocks.", "best_for": "Default choice"},
                "sorted_neighborhood": {"meaning": "Slide a window over sorted records.", "best_for": "Typos in the blocking key"},
                "multi_pass": {"meaning": "Union candidate pairs from several blocking passes.", "best_for": "Noisy data, best recall"},
                "ann": {"meaning": "Approximate nearest-neighbor over embeddings.", "best_for": "Semantic matching"},
                "ann_pairs": {"meaning": "Direct-pair ANN scoring (skips block materialization).", "best_for": "Faster ANN"},
                "canopy": {"meaning": "Cheap loose canopy pre-grouping.", "best_for": "Text-heavy data"},
                "learned": {"meaning": "Data-driven mined blocking predicates.", "best_for": "Auto-discovered rules"},
                "lsh": {"meaning": "MinHash / LSH sketching on a text column.", "best_for": "Near-duplicate text"},
                "simhash": {"meaning": "SimHash LSH over embeddings.", "best_for": "Semantic near-duplicate text"},
                "perceptual": {"meaning": "Banded-Hamming LSH over perceptual image hashes.", "best_for": "Near-duplicate images"},
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
                "most_complete": {"meaning": "Value from the record with the fewest nulls.", "best_for": "Default; sparse records"},
                "most_recent": {"meaning": "Value from the newest record (needs date_column).", "best_for": "Time-stamped sources"},
                "source_priority": {"meaning": "Value from the highest-priority source (needs source_priority).", "best_for": "A trusted source ranking"},
                "majority_vote": {"meaning": "Most frequent value across the cluster.", "best_for": "Many redundant sources"},
                "first_non_null": {"meaning": "First non-null value in row order.", "best_for": "Pre-ordered inputs"},
                "longest_value": {"meaning": "The longest, most detailed value.", "best_for": "Truncated/abbreviated values"},
                "unanimous_or_null": {"meaning": "The value only if every record agrees, else null.", "best_for": "Zero-tolerance fields"},
                "confidence_majority": {"meaning": "Value backed by the highest total match confidence.", "best_for": "Weighting by match quality"},
            }),
            ("Group survivorship strategies", "goldenmatch.config.schemas:_GROUP_STRATEGIES", "`GoldenGroupRule.strategy`", {
                "most_complete": {"meaning": "Take all group columns from the most-complete row.", "best_for": "Default; a coherent row"},
                "most_recent": {"meaning": "Take the group from the newest row.", "best_for": "Time-stamped groups"},
                "source_priority": {"meaning": "Take the group from the highest-priority source.", "best_for": "A trusted source ranking"},
                "anchor": {"meaning": "Take all group columns from the anchor column's winning row.", "best_for": "Tying the group to one key field"},
            }),
            ("Standardizers", "goldenmatch.config.schemas:VALID_STANDARDIZERS", "`StandardizationConfig.rules`", {
                "email": "Lowercase and canonicalize an email address.",
                "name_proper": "Proper-case a name.", "name_upper": "Uppercase a name.", "name_lower": "Lowercase a name.",
                "phone": "Normalize a phone number.", "zip5": "Normalize a US ZIP to 5 digits.",
                "address": "Normalize a postal address.", "state": "Normalize a US state to its 2-letter code.",
                "strip": "Trim whitespace.", "trim_whitespace": "Collapse and trim whitespace.",
            }),
            ("Matchkey types", "goldenmatch.config.schemas:_VALID_MK_TYPES", "`MatchkeyConfig.type`", {
                "exact": {"meaning": "All fields must match exactly; fastest, no scoring.", "best_for": "Reliable keys, max speed"},
                "weighted": {"meaning": "Weighted mean of per-field scores compared to a threshold.", "best_for": "Tunable fuzzy matching"},
                "probabilistic": {"meaning": "Fellegi-Sunter scoring with EM-learned agreement weights.", "best_for": "Unknown field reliability, best recall"},
            }),
            ("Backends", "goldenmatch.core.execution_plan:BackendName", "`GoldenMatchConfig.backend` / `--backend`", {
                "polars-direct": {"meaning": "Straight in-memory Polars execution.", "best_for": "< 500K, native kernel absent"},
                "bucket": {"meaning": "Memory-bounded field-hash bucket scorer (the in-memory default route).", "best_for": "< 500K, default (native on)"},
                "chunked": {"meaning": "Chunked in-memory execution to cap peak memory.", "best_for": "5M+ on one machine"},
                "duckdb": {"meaning": "DuckDB-backed out-of-core execution.", "best_for": "500K-50M, out-of-core"},
                "ray": {"meaning": "Distributed execution on a Ray cluster.", "best_for": "50M+, distributed"},
            }),
            ("Clustering strategies", "goldenmatch.core.execution_plan:ClusteringStrategy", "planner cluster route", {
                "in_memory": {"meaning": "In-memory union-find clustering.", "best_for": "Default; graph fits in RAM"},
                "partitioned_union_find": {"meaning": "Disk-partitioned union-find for large pair sets.", "best_for": "Huge single-box pair sets"},
                "streaming_cc": {"meaning": "Streaming connected-components.", "best_for": "Chunked / out-of-core runs"},
                "distributed_wcc": {"meaning": "Distributed weakly-connected-components on a cluster.", "best_for": "Ray cluster, 100M+"},
            }),
            ("Planning efforts", "goldenmatch.core.autoconfig_controller:_PLANNING_EFFORTS", "`planning_effort`", {
                "fast": {"meaning": "Single cheap auto-config pass.", "best_for": "Quick iteration"},
                "normal": {"meaning": "Default interactive auto-config budget (byte-identical to historical).", "best_for": "Default balance"},
                "thinking": {"meaning": "Extra refit iterations plus full-frame blocking measurement.", "best_for": "Harder datasets"},
                "einstein": {"meaning": "Maximum auto-config search; slowest and most thorough.", "best_for": "Max quality, cost no object"},
            }),
        ],
        tuning_link="/goldenmatch/tuning",
        doc_coverage=(
            ("scoring.mdx", "goldenmatch.config.schemas:VALID_SCORERS"),
            ("blocking.mdx", "goldenmatch.config.schemas:BlockingConfig.strategy"),
            ("configuration.mdx", "goldenmatch.config.schemas:VALID_SIMPLE_TRANSFORMS"),
            ("configuration.mdx", "goldenmatch.config.schemas:VALID_STRATEGIES"),
            ("configuration.mdx", "goldenmatch.config.schemas:VALID_STANDARDIZERS"),
            ("backends-and-scale.mdx", "goldenmatch.core.execution_plan:BackendName"),
            ("backends-and-scale.mdx", "goldenmatch.core.execution_plan:ClusteringStrategy"),
            ("auto-config.mdx", "goldenmatch.core.autoconfig_controller:_PLANNING_EFFORTS"),
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
            ("Check types", "goldencheck.models.finding:CHECK_TYPES", "`Finding.check`", {
                "existence": "A column the config expects is present.",
                "required": "A required column contains no nulls.",
                "unmapped_column": "A column has no rule and no inferred type.",
                "type_inference": "The inferred data type for a column.",
                "nullability": "A column's null rate exceeds the allowed bound.",
                "null_correlation": "Nulls in two columns co-occur suspiciously.",
                "unique": "A column declared unique has duplicates.",
                "uniqueness": "The observed uniqueness of a column.",
                "cardinality": "A column's distinct-value count / ratio.",
                "composite_key": "A candidate multi-column key.",
                "identity_safe_pk": "A column is a safe, stable primary key.",
                "duplicate_rows": "Fully duplicated rows.",
                "near_duplicate_rows": "Near-duplicate rows (fuzzy).",
                "fuzzy_duplicate_values": "Near-duplicate values within a column.",
                "key_uniqueness_loss": "A key lost uniqueness versus the baseline.",
                "range": "A value falls outside the allowed range.",
                "range_distribution": "Value distribution versus the expected range.",
                "enum": "A value is outside the allowed enum set.",
                "format_detection": "The detected value format (email, phone, ...).",
                "pattern_consistency": "Values deviate from the column's dominant pattern.",
                "encoding_detection": "Suspect character encoding / mojibake.",
                "sequence_detection": "A column is a monotonic sequence / counter.",
                "temporal_order": "Dates violate an expected ordering.",
                "future_dated": "A date is in the future.",
                "stale_data": "Data is older than the freshness bound.",
                "temporal_order_drift": "Temporal ordering changed versus the baseline.",
                "cross_column": "A cross-column relationship finding.",
                "cross_column_validation": "A cross-column validation rule failed.",
                "functional_dependency": "A discovered functional dependency.",
                "fd_violation": "Rows violate a functional dependency.",
                "correlation_break": "A previously strong correlation weakened.",
                "new_correlation": "A new column correlation appeared.",
                "referential_integrity": "A foreign-key reference is unmatched.",
                "denial_constraint": "A mined denial constraint is violated.",
                "drift_detection": "General distribution drift versus the baseline.",
                "distribution_drift": "A column's value distribution drifted.",
                "entropy_drift": "A column's entropy drifted.",
                "bound_violation": "A value crossed a learned baseline bound.",
                "benford_drift": "Leading-digit (Benford) distribution drifted.",
                "type_drift": "A column's inferred type changed.",
                "pattern_drift": "A column's dominant pattern changed.",
                "new_pattern": "A new value pattern appeared.",
            }),
        ],
        doc_coverage=(
            ("checks.mdx", "goldencheck.models.finding:CHECK_TYPES"),
        ),
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
        doc_coverage=(
            ("transforms.mdx", "goldenflow.transforms:list_transforms"),
        ),
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
            ("Stages", "goldenpipe.engine.registry:BUILTIN_STAGES", "`StageSpec.use`", {
                "load": "Load a source file into the pipeline frame.",
                "infer_schema": "Infer the source schema (via infermap).",
                "goldencheck.scan": "Run a GoldenCheck data-quality scan.",
                "goldenflow.transform": "Run GoldenFlow transforms / standardization.",
                "goldenmatch.dedupe": "Run GoldenMatch dedupe / entity resolution.",
                "goldenmatch.dedupe_fused": "Run the fused GoldenMatch dedupe kernel.",
                "goldenmatch.identity_resolve": "Resolve records against the identity graph.",
                "goldenanalysis.report": "Run GoldenAnalysis metrics + reporting.",
            }),
        ],
        doc_coverage=(
            ("stages.mdx", "goldenpipe.engine.registry:BUILTIN_STAGES"),
        ),
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
        doc_coverage=(
            ("mapping.mdx", "infermap.types:VALID_DTYPES"),
            ("mapping.mdx", "infermap.scorers.pattern_type:SEMANTIC_TYPES"),
        ),
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
            ("Analyzers", "goldenanalysis.registry:_FALLBACK", "`analyze(analyzers=...)`", {
                "frame.summary": "Generic frame stats: row/column counts, null ratio, duplicate ratio, memory.",
                "match.rates": "GoldenMatch results: pair count, match rate, threshold, recall estimate, score histogram.",
                "cluster.distribution": "GoldenMatch clusters: count, singletons, size percentiles, reduction ratio.",
                "quality.rollup": "GoldenCheck / GoldenFlow rollup: findings, health score, rows changed, rules fired.",
            }),
        ],
        doc_coverage=(
            ("cross-run.mdx", "goldenanalysis.models.report:Direction"),
            ("cross-run.mdx", "goldenanalysis.models.policy:Baseline"),
            ("analyzers.mdx", "goldenanalysis.registry:_FALLBACK"),
        ),
    ),
}
