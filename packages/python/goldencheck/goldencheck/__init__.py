"""GoldenCheck — data validation that discovers rules from your data."""

__version__ = "1.0.1"

from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile
from goldencheck.notebook import ScanResult

try:
    from goldencheck.agent import AgentSession, ReviewQueue  # noqa: F401
    _agent_exports = ["AgentSession", "ReviewQueue"]
except ImportError:
    _agent_exports = []

__all__ = [
    "scan_file",
    "scan_file_with_llm",
    "Finding",
    "Severity",
    "DatasetProfile",
    "ColumnProfile",
    "ScanResult",
    "__version__",
    *_agent_exports,
]
