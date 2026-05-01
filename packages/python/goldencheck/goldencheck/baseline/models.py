"""Pydantic models for deep profiling baseline profiles."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

__all__ = [
    "StatProfile",
    "FunctionalDependency",
    "TemporalOrder",
    "CorrelationEntry",
    "PatternGrammar",
    "ConfidencePrior",
    "BaselineProfile",
]

logger = logging.getLogger(__name__)

# Known top-level keys in the YAML format (for unknown-key detection).
_KNOWN_YAML_KEYS = {
    "version",
    "created",
    "source",
    "rows",
    "columns",
    "stat_profiles",
    "constraints",
    "correlations",
    "patterns",
    "semantic_types",
    "priors",
    "history",
}


class StatProfile(BaseModel):
    """Statistical distribution profile for a single column."""

    distribution: str | None = None
    params: dict[str, Any] | None = None
    benford: dict[str, float] | None = None
    entropy: float
    bounds: dict[str, Any]


class FunctionalDependency(BaseModel):
    """A functional dependency between columns."""

    determinant: list[str]
    dependent: list[str]
    confidence: float


class TemporalOrder(BaseModel):
    """An expected temporal ordering constraint between two date/time columns."""

    before: str
    after: str
    violation_rate: float


class CorrelationEntry(BaseModel):
    """A pairwise (or multi-column) correlation observation."""

    columns: list[str]
    measure: str
    value: float
    strength: str
    note: str | None = None


class PatternGrammar(BaseModel):
    """A regex pattern grammar with coverage fraction."""

    pattern: str
    coverage: float


class ConfidencePrior(BaseModel):
    """A Bayesian-style prior for an anomaly type on a column."""

    confidence: float
    evidence_count: int


class BaselineProfile(BaseModel):
    """Full deep-profile baseline for a dataset."""

    version: str = "1.0"
    created: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    source: str
    rows: int
    columns: list[str]

    stat_profiles: dict[str, StatProfile] = Field(default_factory=dict)

    # Stored flat internally; translated to/from nested YAML.
    constraints_fd: list[FunctionalDependency] = Field(default_factory=list)
    constraints_keys: list[list[str]] = Field(default_factory=list)
    constraints_temporal: list[TemporalOrder] = Field(default_factory=list)

    correlations: list[CorrelationEntry] = Field(default_factory=list)
    patterns: dict[str, PatternGrammar] = Field(default_factory=dict)
    semantic_types: dict[str, str] = Field(default_factory=dict)
    priors: dict[str, ConfidencePrior] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def source_filename(self) -> str:
        """Return the basename of the source path (cross-platform)."""
        # os.path.basename handles both / and \ on all platforms
        name = os.path.basename(self.source.replace("\\", "/"))
        if not name:
            # Fallback for Windows-style paths when running on non-Windows
            name = self.source.replace("\\", "/").split("/")[-1]
        return name

    @property
    def confidence_priors(self) -> dict[str, dict[str, "ConfidencePrior"]]:
        """Return priors as a nested dict ``{check: {column: ConfidencePrior}}``.

        The flat ``priors`` field stores keys as ``"check:col"``.  This property
        unpacks them into the nested form expected by :func:`~goldencheck.baseline.priors.apply_prior`.
        """
        nested: dict[str, dict[str, ConfidencePrior]] = {}
        for key, prior in self.priors.items():
            if ":" in key:
                check, col = key.split(":", 1)
                nested.setdefault(check, {})[col] = prior
        return nested

    # ------------------------------------------------------------------
    # Serialisation helpers (private)
    # ------------------------------------------------------------------

    def _to_dict(self) -> dict[str, Any]:
        """Serialise to a dict using the public YAML schema."""
        data: dict[str, Any] = {
            "version": self.version,
            "created": self.created.isoformat(),
            "source": self.source,
            "rows": self.rows,
            "columns": self.columns,
        }

        if self.stat_profiles:
            data["stat_profiles"] = {
                col: sp.model_dump(exclude_none=False)
                for col, sp in self.stat_profiles.items()
            }

        # Nested constraints block
        constraints: dict[str, Any] = {}
        if self.constraints_fd:
            constraints["functional_dependencies"] = [
                fd.model_dump() for fd in self.constraints_fd
            ]
        if self.constraints_keys:
            constraints["candidate_keys"] = self.constraints_keys
        if self.constraints_temporal:
            constraints["temporal_orders"] = [
                to.model_dump() for to in self.constraints_temporal
            ]
        if constraints:
            data["constraints"] = constraints

        if self.correlations:
            data["correlations"] = [c.model_dump() for c in self.correlations]

        if self.patterns:
            data["patterns"] = {
                col: {
                    "grammars": [pg.model_dump()],
                    "total_coverage": pg.coverage,
                }
                for col, pg in self.patterns.items()
            }

        if self.semantic_types:
            data["semantic_types"] = self.semantic_types

        if self.priors:
            data["priors"] = {k: v.model_dump() for k, v in self.priors.items()}

        if self.history:
            data["history"] = self.history

        return data

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> BaselineProfile:
        """Deserialise from the public YAML schema dict."""
        # Detect unknown keys and warn
        unknown = set(data.keys()) - _KNOWN_YAML_KEYS
        if unknown:
            logger.warning("BaselineProfile.load: ignoring unknown keys: %s", sorted(unknown))

        kwargs: dict[str, Any] = {
            "version": data.get("version", "1.0"),
            "created": datetime.fromisoformat(data["created"]),
            "source": data["source"],
            "rows": data["rows"],
            "columns": data.get("columns", []),
        }

        # stat_profiles
        raw_stats = data.get("stat_profiles") or {}
        kwargs["stat_profiles"] = {col: StatProfile(**sp) for col, sp in raw_stats.items()}

        # Nested constraints -> flat fields
        constraints = data.get("constraints") or {}
        raw_fds = constraints.get("functional_dependencies") or []
        kwargs["constraints_fd"] = [FunctionalDependency(**fd) for fd in raw_fds]
        kwargs["constraints_keys"] = constraints.get("candidate_keys") or []
        raw_temporal = constraints.get("temporal_orders") or []
        kwargs["constraints_temporal"] = [TemporalOrder(**to) for to in raw_temporal]

        # correlations
        raw_corr = data.get("correlations") or []
        kwargs["correlations"] = [CorrelationEntry(**c) for c in raw_corr]

        # patterns (nested grammars list -> single PatternGrammar)
        raw_patterns = data.get("patterns") or {}
        patterns: dict[str, PatternGrammar] = {}
        for col, pat_data in raw_patterns.items():
            grammars = pat_data.get("grammars") or []
            if grammars:
                patterns[col] = PatternGrammar(**grammars[0])
        kwargs["patterns"] = patterns

        kwargs["semantic_types"] = data.get("semantic_types") or {}

        raw_priors = data.get("priors") or {}
        kwargs["priors"] = {k: ConfidencePrior(**v) for k, v in raw_priors.items()}

        kwargs["history"] = data.get("history") or []

        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Public save / load
    # ------------------------------------------------------------------

    def save(self, path: Path | str) -> None:
        """Serialise this profile to YAML at *path*."""
        data = self._to_dict()
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    @classmethod
    def load(cls, path: Path | str) -> BaselineProfile:
        """Load a BaselineProfile from a YAML file, ignoring unknown keys."""
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"Expected a YAML mapping in {path!r}, got {type(data).__name__}")
        return cls._from_dict(data)

    # ------------------------------------------------------------------
    # Merge / update
    # ------------------------------------------------------------------

    def update_from(self, new: BaselineProfile) -> None:
        """Merge *new* baseline into this one using the defined merge semantics.

        Merge rules:
        - Statistical profiles: replace with new.
        - FDs: keep existing if new confidence >= 0.8; add new FDs with confidence >= 0.9.
        - Candidate keys / temporal orders: replace with new.
        - Semantic types: replace with new.
        - Correlations: replace with new.
        - Patterns: replace with new.
        - Priors: weighted average by evidence_count.
        - Record a history entry with old source / created.
        - Update source, created, rows, columns.
        """
        # Record history before mutating
        self.history.append({
            "source": self.source,
            "created": self.created.isoformat(),
        })

        # Statistical profiles — replace
        self.stat_profiles = dict(new.stat_profiles)

        # FDs — filtered merge
        existing_keys = {
            (tuple(fd.determinant), tuple(fd.dependent)): fd
            for fd in self.constraints_fd
        }
        new_keys = {
            (tuple(fd.determinant), tuple(fd.dependent)): fd
            for fd in new.constraints_fd
        }

        merged_fds: list[FunctionalDependency] = []
        # Keep existing FDs only if new data has confidence >= 0.8
        for key, fd in existing_keys.items():
            new_fd = new_keys.get(key)
            if new_fd is not None and new_fd.confidence >= 0.8:
                merged_fds.append(fd)
            # If not present in new data, drop it (conservative)

        # Add brand-new FDs with confidence >= 0.9
        for key, new_fd in new_keys.items():
            if key not in existing_keys and new_fd.confidence >= 0.9:
                merged_fds.append(new_fd)

        self.constraints_fd = merged_fds

        # Keys / temporal — replace
        self.constraints_keys = list(new.constraints_keys)
        self.constraints_temporal = list(new.constraints_temporal)

        # Semantic types — replace
        self.semantic_types = dict(new.semantic_types)

        # Correlations — replace
        self.correlations = list(new.correlations)

        # Patterns — replace
        self.patterns = dict(new.patterns)

        # Priors — weighted average by evidence_count
        all_prior_keys = set(self.priors) | set(new.priors)
        merged_priors: dict[str, ConfidencePrior] = {}
        for key in all_prior_keys:
            old_p = self.priors.get(key)
            new_p = new.priors.get(key)
            if old_p is not None and new_p is not None:
                total = old_p.evidence_count + new_p.evidence_count
                weighted_conf = (
                    old_p.confidence * old_p.evidence_count
                    + new_p.confidence * new_p.evidence_count
                ) / total
                merged_priors[key] = ConfidencePrior(
                    confidence=weighted_conf,
                    evidence_count=total,
                )
            elif new_p is not None:
                merged_priors[key] = new_p
            else:
                merged_priors[key] = old_p  # type: ignore[assignment]
        self.priors = merged_priors

        # Update metadata
        self.source = new.source
        self.created = new.created
        self.rows = new.rows
        self.columns = list(new.columns)
