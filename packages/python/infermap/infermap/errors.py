"""infermap exception classes."""


class InferMapError(Exception):
    """Base exception for infermap."""


class ConfigError(InferMapError):
    """Raised for invalid config or schema files."""


class ApplyError(InferMapError):
    """Raised when apply() encounters missing columns."""
