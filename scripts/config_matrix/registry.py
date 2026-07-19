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
    vocabs: list[tuple[str, str, str]] = field(default_factory=list)
    vocab_warmup: list[str] = field(default_factory=list)
    tuning_link: str | None = None


REGISTRY: dict[str, PackageSpec] = {
    "goldenmatch": PackageSpec(
        name="goldenmatch",
        doc_path="docs-site/goldenmatch/config-matrix.mdx",
        nav_group="GoldenMatch",
        env_prefix="GOLDENMATCH_",
        src_dirs=["packages/python/goldenmatch/goldenmatch", _RUST],
        schema_roots=["goldenmatch.config.schemas:GoldenMatchConfig"],
        vocabs=[
            ("Scorers", "goldenmatch.config.schemas:VALID_SCORERS", "`MatchkeyField.scorer` / `NegativeEvidenceField.scorer`"),
            ("Simple transforms", "goldenmatch.config.schemas:VALID_SIMPLE_TRANSFORMS", "`transforms` chains"),
            ("Survivorship strategies", "goldenmatch.config.schemas:VALID_STRATEGIES", "`GoldenFieldRule.strategy`"),
            ("Group survivorship strategies", "goldenmatch.config.schemas:_GROUP_STRATEGIES", "`GoldenGroupRule.strategy`"),
            ("Standardizers", "goldenmatch.config.schemas:VALID_STANDARDIZERS", "`StandardizationConfig.rules`"),
            ("Matchkey types", "goldenmatch.config.schemas:_VALID_MK_TYPES", "`MatchkeyConfig.type`"),
        ],
        tuning_link="/goldenmatch/tuning",
    ),
    "goldencheck": PackageSpec(
        name="goldencheck",
        doc_path="docs-site/goldencheck/config-matrix.mdx",
        nav_group="GoldenCheck",
        env_prefix="GOLDENCHECK_",
        src_dirs=["packages/python/goldencheck/goldencheck", _RUST],
        schema_roots=["goldencheck.config.schema:GoldenCheckConfig"],
        vocabs=[
            ("Finding severity", "goldencheck.models.finding:Severity", "`Settings.severity_threshold` / `fail_on`"),
        ],
    ),
    "goldenflow": PackageSpec(
        name="goldenflow",
        doc_path="docs-site/goldenflow/config-matrix.mdx",
        nav_group="GoldenFlow",
        env_prefix="GOLDENFLOW_",
        src_dirs=["packages/python/goldenflow/goldenflow", _RUST],
        schema_roots=["goldenflow.config.schema:GoldenFlowConfig"],
        vocabs=[
            ("Transform ops", "goldenflow.transforms:list_transforms", "`TransformSpec.ops`"),
            ("Domain packs", "goldenflow.domains:_DOMAINS", "`learn_config(domain=...)`"),
            ("Canonicalize kinds", "goldenflow.canonicalize:CanonicalizeKind", "`canonicalize` op"),
        ],
        vocab_warmup=["goldenflow"],
    ),
    "goldenpipe": PackageSpec(
        name="goldenpipe",
        doc_path="docs-site/goldenpipe/config-matrix.mdx",
        nav_group="GoldenPipe",
        env_prefix="GOLDENPIPE_",
        src_dirs=["packages/python/goldenpipe/goldenpipe", _RUST],
        schema_roots=["goldenpipe.models.config:PipelineConfig"],
        vocabs=[
            ("Stage status", "goldenpipe.models.context:StageStatus", "per-stage result status"),
            ("Pipeline status", "goldenpipe.models.context:PipeStatus", "run result status"),
            ("Column types", "goldenpipe.models.column_context:ColumnType", "column classification"),
            ("Cardinality bands", "goldenpipe.models.column_context:CardinalityBand", "column cardinality band"),
            ("Repair fixers", "goldenpipe.repair_host:FIXERS", "repair-op allowlist"),
        ],
    ),
    "infermap": PackageSpec(
        name="infermap",
        doc_path="docs-site/infermap/config-matrix.mdx",
        nav_group="InferMap",
        env_prefix="INFERMAP_",
        src_dirs=["packages/python/infermap/infermap", _RUST],
        constructors=["infermap.engine:MapEngine"],
        vocabs=[
            ("Data types", "infermap.types:VALID_DTYPES", "`FieldInfo.dtype`"),
            ("Semantic pattern types", "infermap.scorers.pattern_type:SEMANTIC_TYPES", "pattern-type detection"),
        ],
    ),
    "goldenanalysis": PackageSpec(
        name="goldenanalysis",
        doc_path="docs-site/goldenanalysis/config-matrix.mdx",
        nav_group="GoldenAnalysis",
        env_prefix="GOLDENANALYSIS_",
        src_dirs=["packages/python/goldenanalysis/goldenanalysis", _RUST],
        schema_roots=["goldenanalysis.models.policy:RegressionPolicy"],
        constructors=["goldenanalysis._api:analyze"],
        vocabs=[
            ("Metric direction", "goldenanalysis.models.report:Direction", "`Metric.direction`"),
            ("Regression baseline", "goldenanalysis.models.policy:Baseline", "`--baseline`"),
        ],
    ),
}
