"""GoldenCheck — data validation that discovers rules from your data."""
from __future__ import annotations

__version__ = "1.0.1"

# Core: scanner + models
from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile
from goldencheck.notebook import ScanResult

# Engine: validator, confidence, triage, fixer, differ, reader
from goldencheck.engine.validator import validate_file
from goldencheck.engine.confidence import (
    apply_confidence_downgrade,
    apply_corroboration_boost,
)
from goldencheck.engine.triage import auto_triage, TriageResult
from goldencheck.engine.fixer import apply_fixes, FixReport, FixEntry
from goldencheck.engine.differ import diff_files, DiffReport, SchemaChange, FindingChange, StatChange
from goldencheck.engine.reader import read_file

# Config: schema, loader, writer
from goldencheck.config.schema import (
    GoldenCheckConfig,
    ColumnRule,
    Settings,
    RelationRule,
    IgnoreEntry,
)
from goldencheck.config.loader import load_config
from goldencheck.config.writer import save_config

# Semantic: classifier
from goldencheck.semantic.classifier import classify_columns, list_available_domains

try:
    from goldencheck.agent import AgentSession, ReviewQueue  # noqa: F401
    _agent_exports = ["AgentSession", "ReviewQueue"]
except ImportError:
    _agent_exports = []

__all__ = [
    # Core
    "scan_file",
    "scan_file_with_llm",
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
    # Agent (optional)
    *_agent_exports,
]
