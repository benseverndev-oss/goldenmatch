"""GoldenCheck — data validation that discovers rules from your data."""
from __future__ import annotations

__version__ = "1.3.0"

# Core: scanner + models
from goldencheck.cell_quality import cell_quality
from goldencheck.config.loader import load_config

# Config: schema, loader, writer
from goldencheck.config.schema import (
    ColumnRule,
    GoldenCheckConfig,
    IgnoreEntry,
    RelationRule,
    Settings,
)
from goldencheck.config.writer import save_config
from goldencheck.engine.confidence import (
    apply_confidence_downgrade,
    apply_corroboration_boost,
)
from goldencheck.engine.differ import (
    DiffReport,
    FindingChange,
    SchemaChange,
    StatChange,
    diff_files,
)
from goldencheck.engine.fixer import FixEntry, FixReport, apply_fixes
from goldencheck.engine.reader import read_file
from goldencheck.engine.scanner import scan_dataframe, scan_file, scan_file_with_llm
from goldencheck.engine.triage import TriageResult, auto_triage

# Engine: validator, confidence, triage, fixer, differ, reader
from goldencheck.engine.validator import validate_file
from goldencheck.functional_dependencies import (
    FunctionalDependency,
    functional_dependencies,
)
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import ColumnProfile, DatasetProfile
from goldencheck.notebook import ScanResult

# Semantic: classifier
from goldencheck.semantic.classifier import classify_columns, list_available_domains

try:
    from goldencheck.agent import AgentSession, ReviewQueue  # noqa: F401
    _agent_exports = ["AgentSession", "ReviewQueue"]
except ImportError:
    _agent_exports = []

def __getattr__(name: str):
    if name == "create_baseline":
        from goldencheck.baseline import create_baseline
        return create_baseline
    if name == "load_baseline":
        from goldencheck.baseline import load_baseline
        return load_baseline
    raise AttributeError(f"module 'goldencheck' has no attribute {name!r}")


__all__ = [
    # Core
    "scan_dataframe",
    "scan_file",
    "scan_file_with_llm",
    "cell_quality",
    "functional_dependencies",
    "FunctionalDependency",
    "Finding",
    "Severity",
    "DatasetProfile",
    "ColumnProfile",
    "ScanResult",
    "__version__",
    # Engine
    "validate_file",
    "apply_confidence_downgrade",
    "apply_corroboration_boost",
    "auto_triage",
    "TriageResult",
    "apply_fixes",
    "FixReport",
    "FixEntry",
    "diff_files",
    "DiffReport",
    "SchemaChange",
    "FindingChange",
    "StatChange",
    "read_file",
    # Config
    "GoldenCheckConfig",
    "ColumnRule",
    "Settings",
    "RelationRule",
    "IgnoreEntry",
    "load_config",
    "save_config",
    # Semantic
    "classify_columns",
    "list_available_domains",
    # Baseline
    "create_baseline",
    "load_baseline",
    # Agent (optional)
    *_agent_exports,
]
