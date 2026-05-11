"""GoldenMatch -- entity resolution toolkit.

Quick start:
    import goldenmatch as gm

    # Deduplicate a CSV
    result = gm.dedupe("customers.csv", exact=["email"], fuzzy={"name": 0.85})
    result.golden.write_csv("deduped.csv")

    # Match across files
    result = gm.match("targets.csv", "reference.csv", fuzzy={"name": 0.85})

    # Privacy-preserving linkage
    result = gm.pprl_link("hospital_a.csv", "hospital_b.csv", fields=["name", "dob", "zip"])

    # Evaluate accuracy
    metrics = gm.evaluate("data.csv", config="config.yaml", ground_truth="gt.csv")

    # Streaming single-record matching
    matches = gm.match_one(record, df, matchkey)

    # Domain extraction
    rulebooks = gm.discover_rulebooks()

    # Explain a match
    explanation = gm.explain_pair(record_a, record_b, matchkey)

All features are accessible via `import goldenmatch as gm`.
"""

__version__ = "1.12.0"

# ── High-level API (convenience functions) ────────────────────────────────
from goldenmatch._api import (
    DedupeResult,
    MatchResult,
    add_correction,
    dedupe,
    dedupe_df,
    evaluate,
    explain_pair_df,
    get_memory,
    learn,
    load_config,
    match,
    match_df,
    memory_stats,
    pprl_link,
    score_pair_df,
    score_strings,
)

# ── REST API Client ──────────────────────────────────────────────────────
from goldenmatch.client import Client

# ── Config schemas (for building configs programmatically) ────────────────
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    BudgetConfig,
    DomainConfig,
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    LLMScorerConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
    StandardizationConfig,
    ValidationConfig,
)

# ── Agent ────────────────────────────────────────────────────────────────
from goldenmatch.core.agent import AgentSession
from goldenmatch.core.anomaly import detect_anomalies

# ── Auto-configuration ──────────────────────────────────────────────────
from goldenmatch.core.autoconfig import auto_configure, auto_configure_df

# ── Auto-config verification ────────────────────────────────────────────
# See PR #44 for design notes.
from goldenmatch.core.autoconfig_verify import (
    ConfigValidationError,
    PostflightAdjustment,
    PostflightReport,
    PreflightFinding,
    PreflightReport,
    postflight,
    preflight,
)

# ── Data quality ─────────────────────────────────────────────────────────
from goldenmatch.core.autofix import auto_fix_dataframe
from goldenmatch.core.blocker import build_blocks

# ── Active learning / boost ──────────────────────────────────────────────
from goldenmatch.core.boost import boost_accuracy
from goldenmatch.core.cluster import (
    add_to_cluster,
    build_clusters,
    compute_cluster_confidence,
    unmerge_cluster,
    unmerge_record,
)

# ── Cluster comparison (CCMS) ──────────────────────────────────────────
from goldenmatch.core.compare_clusters import CompareResult, compare_clusters

# ── Diff / Rollback ─────────────────────────────────────────────────────
from goldenmatch.core.diff import generate_diff

# ── Domain extraction ────────────────────────────────────────────────────
from goldenmatch.core.domain_registry import (
    DomainRulebook,
    discover_rulebooks,
    extract_with_rulebook,
    load_rulebook,
    match_domain,
    save_rulebook,
)

# ── Evaluation ───────────────────────────────────────────────────────────
from goldenmatch.core.evaluate import (
    EvalResult,
    evaluate_clusters,
    evaluate_pairs,
    load_ground_truth_csv,
)

# ── Explainability ───────────────────────────────────────────────────────
from goldenmatch.core.explain import explain_cluster_nl, explain_pair_nl
from goldenmatch.core.golden import build_golden_record

# ── Graph ER ─────────────────────────────────────────────────────────────
from goldenmatch.core.graph_er import run_graph_er
from goldenmatch.core.ingest import load_file, load_files

# ── Learned blocking ─────────────────────────────────────────────────────
from goldenmatch.core.learned_blocking import apply_learned_blocks, learn_blocking_rules

# ── Lineage ──────────────────────────────────────────────────────────────
from goldenmatch.core.lineage import build_lineage, save_lineage
from goldenmatch.core.llm_budget import BudgetTracker
from goldenmatch.core.llm_cluster import llm_cluster_pairs
from goldenmatch.core.llm_extract import llm_extract_features
from goldenmatch.core.llm_labeler import label_pairs as llm_label_pairs

# ── LLM scoring ──────────────────────────────────────────────────────────
from goldenmatch.core.llm_scorer import llm_score_pairs

# ── Streaming / incremental ──────────────────────────────────────────────
from goldenmatch.core.match_one import match_one
from goldenmatch.core.matchkey import compute_matchkeys

# ── Learning Memory ──────────────────────────────────────────────────────
from goldenmatch.core.memory import (
    Correction,
    CorrectionStats,
    LearnedAdjustment,
    MemoryLearner,
    MemoryStore,
    apply_corrections,
)

# ── Core pipeline functions ───────────────────────────────────────────────
from goldenmatch.core.pipeline import run_dedupe, run_match

# ── Probabilistic (Fellegi-Sunter) ───────────────────────────────────────
from goldenmatch.core.probabilistic import score_probabilistic, train_em

# ── Profiling ────────────────────────────────────────────────────────────
from goldenmatch.core.profiler import profile_dataframe
from goldenmatch.core.review_queue import ReviewQueue, gate_pairs
from goldenmatch.core.rollback import rollback_run

# ── Schema matching ──────────────────────────────────────────────────────
from goldenmatch.core.schema_match import auto_map_columns

# ── Reranking ────────────────────────────────────────────────────────────
from goldenmatch.core.scorer import (
    find_exact_matches,
    find_fuzzy_matches,
    rerank_top_pairs,
    score_blocks_parallel,
    score_pair,
)
from goldenmatch.core.sensitivity import SensitivityResult, SweepParam, run_sensitivity
from goldenmatch.core.standardize import apply_standardization
from goldenmatch.core.streaming import StreamProcessor, run_stream
from goldenmatch.core.threshold import suggest_threshold
from goldenmatch.core.validate import validate_dataframe
from goldenmatch.output.report import generate_dedupe_report

# ── Output ───────────────────────────────────────────────────────────────
from goldenmatch.output.writer import write_output
from goldenmatch.pprl.autoconfig import (
    auto_configure_pprl,
    auto_configure_pprl_llm,
    profile_for_pprl,
)

# ── PPRL ─────────────────────────────────────────────────────────────────
from goldenmatch.pprl.protocol import (
    LinkageResult,
    PartyData,
    PPRLConfig,
    compute_bloom_filters,
    link_smc,
    link_trusted_third_party,
    run_pprl,
)

# ── Shortcuts ────────────────────────────────────────────────────────────
explain_pair = explain_pair_nl
explain_cluster = explain_cluster_nl
pprl_auto_config = auto_configure_pprl

__all__ = [
    # Version
    "__version__",
    # High-level API
    "dedupe", "dedupe_df", "match", "match_df",
    "score_strings", "score_pair_df", "explain_pair_df",
    "pprl_link", "evaluate", "load_config",
    "DedupeResult", "MatchResult",
    # Agent
    "AgentSession", "ReviewQueue", "gate_pairs",
    # Config
    "GoldenMatchConfig", "MatchkeyConfig", "MatchkeyField",
    "BlockingConfig", "BlockingKeyConfig",
    "GoldenRulesConfig", "GoldenFieldRule",
    "LLMScorerConfig", "BudgetConfig",
    "DomainConfig", "StandardizationConfig", "ValidationConfig", "OutputConfig",
    # Pipeline
    "run_dedupe", "run_match",
    "find_exact_matches", "find_fuzzy_matches", "score_pair", "score_blocks_parallel",
    "build_clusters", "add_to_cluster", "unmerge_record", "unmerge_cluster",
    "compute_cluster_confidence",
    "build_blocks", "build_golden_record",
    "load_file", "load_files",
    "apply_standardization", "compute_matchkeys",
    # Streaming
    "match_one", "StreamProcessor", "run_stream",
    # Evaluation
    "evaluate_pairs", "evaluate_clusters", "load_ground_truth_csv", "EvalResult",
    # Explain
    "explain_pair", "explain_pair_nl", "explain_cluster", "explain_cluster_nl",
    # Domain
    "discover_rulebooks", "load_rulebook", "save_rulebook",
    "match_domain", "extract_with_rulebook", "DomainRulebook",
    # Probabilistic
    "train_em", "score_probabilistic",
    # Learned blocking
    "learn_blocking_rules", "apply_learned_blocks",
    # LLM
    "llm_score_pairs", "llm_cluster_pairs", "BudgetTracker",
    "llm_label_pairs", "llm_extract_features",
    # PPRL
    "PPRLConfig", "run_pprl", "compute_bloom_filters",
    "link_trusted_third_party", "link_smc",
    "PartyData", "LinkageResult",
    "auto_configure_pprl", "auto_configure_pprl_llm", "profile_for_pprl",
    "pprl_auto_config",
    # Profiling
    "profile_dataframe",
    # Lineage
    "build_lineage", "save_lineage",
    # Active learning / boost
    "boost_accuracy",
    # Auto-configuration
    "auto_configure", "auto_configure_df", "suggest_threshold",
    # Auto-config verification
    "preflight", "postflight",
    "PreflightReport", "PreflightFinding",
    "PostflightReport", "PostflightAdjustment",
    "ConfigValidationError",
    # Data quality
    "auto_fix_dataframe", "validate_dataframe", "detect_anomalies",
    # Schema matching
    "auto_map_columns",
    # Graph ER
    "run_graph_er",
    # Reranking
    "rerank_top_pairs",
    # Diff / Rollback
    "generate_diff", "rollback_run",
    # Cluster comparison
    "compare_clusters", "CompareResult",
    "run_sensitivity", "SensitivityResult", "SweepParam",
    # Output
    "write_output", "generate_dedupe_report",
    # REST API Client
    "Client",
    # Learning Memory
    "MemoryStore", "Correction", "LearnedAdjustment", "CorrectionStats",
    "MemoryLearner", "apply_corrections",
    # Learning Memory API
    "get_memory", "add_correction", "learn", "memory_stats",
]
