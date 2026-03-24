"""GoldenCheck — data validation that discovers rules from your data."""

__version__ = "0.4.0"

from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile
from goldencheck.notebook import ScanResult

__all__ = [
    "scan_file",
    "scan_file_with_llm",
    "Finding",
    "Severity",
    "DatasetProfile",
    "ColumnProfile",
    "ScanResult",
    "__version__",
]
