"""Provider Protocol definition."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from infermap.types import SchemaInfo


@runtime_checkable
class Provider(Protocol):
    """Protocol that all schema providers must implement."""

    def extract(self, source: Any, **kwargs) -> SchemaInfo:
        """Extract a SchemaInfo from the given source."""
        ...
