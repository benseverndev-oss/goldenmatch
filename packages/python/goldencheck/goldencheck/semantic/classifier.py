"""Semantic type classifier — infers what each column represents."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml
import polars as pl

logger = logging.getLogger(__name__)

@dataclass
class TypeDef:
    name_hints: list[str]
    value_signals: dict[str, Any]
    suppress: list[str]

@dataclass
class ColumnClassification:
    type_name: str | None
    source: str  # "name", "value", "llm", "none"

def load_type_defs(custom_path: Path | None = None) -> dict[str, TypeDef]:
    """Load base types + optional user types."""
    base_path = Path(__file__).parent / "types.yaml"
    with open(base_path) as f:
        base = yaml.safe_load(f)

    defs = {}
    for name, cfg in base.get("types", {}).items():
        defs[name] = TypeDef(
            name_hints=cfg.get("name_hints", []),
            value_signals=cfg.get("value_signals", {}),
            suppress=cfg.get("suppress", []),
        )

    # Merge user types (replace same-name, prepend new)
    if custom_path and custom_path.exists():
        with open(custom_path) as f:
            user = yaml.safe_load(f)
        for name, cfg in user.get("types", {}).items():
            defs[name] = TypeDef(
                name_hints=cfg.get("name_hints", []),
                value_signals=cfg.get("value_signals", {}),
                suppress=cfg.get("suppress", []),
            )

    return defs

def classify_columns(
    df: pl.DataFrame,
    custom_types_path: Path | None = None,
) -> dict[str, ColumnClassification]:
    """Classify each column's semantic type with provenance."""
    type_defs = load_type_defs(custom_types_path)
    results = {}

    for col_name in df.columns:
        # 1. Name heuristic matching
        classification = _match_by_name(col_name, type_defs)
        if classification:
            results[col_name] = ColumnClassification(type_name=classification, source="name")
            continue

        # 2. Value-based inference
        classification = _match_by_value(df, col_name, type_defs)
        if classification:
            results[col_name] = ColumnClassification(type_name=classification, source="value")
            continue

        results[col_name] = ColumnClassification(type_name=None, source="none")

    return results

def _match_by_name(col_name: str, type_defs: dict[str, TypeDef]) -> str | None:
    col_lower = col_name.lower()
    for type_name, type_def in type_defs.items():
        for hint in type_def.name_hints:
            # Prefix match: hint ends with _
            if hint.endswith("_"):
                if col_lower.startswith(hint):
                    return type_name
            # Suffix match: hint starts with _
            elif hint.startswith("_"):
                if col_lower.endswith(hint):
                    return type_name
            # Substring match (only for hints without _ prefix/suffix markers)
            elif hint in col_lower:
                return type_name
    return None

def _match_by_value(df: pl.DataFrame, col_name: str, type_defs: dict[str, TypeDef]) -> str | None:
    col = df[col_name]
    non_null = col.drop_nulls()
    if len(non_null) == 0:
        return None

    for type_name, type_def in type_defs.items():
        signals = type_def.value_signals
        if not signals:
            continue
        if _check_value_signals(non_null, col, signals):
            return type_name
    return None

def _check_value_signals(non_null: pl.Series, col: pl.Series, signals: dict) -> bool:
    """Check if ALL value signals are satisfied."""
    for key, value in signals.items():
        if key == "min_unique_pct":
            if non_null.n_unique() / len(non_null) < value:
                return False
        elif key == "max_unique":
            if non_null.n_unique() > value:
                return False
        elif key == "format_match":
            # Check format detection (email, phone, date)
            if not _check_format_match(non_null, value):
                return False
        elif key == "min_match_pct":
            pass  # Used with format_match, handled there
        elif key == "mixed_case":
            if non_null.dtype not in (pl.Utf8, pl.String):
                return False
            sample = non_null.head(100).to_list()
            has_upper = any(any(c.isupper() for c in str(s)) for s in sample)
            has_lower = any(any(c.islower() for c in str(s)) for s in sample)
            if not (has_upper and has_lower):
                return False
        elif key == "avg_length_min":
            if non_null.dtype not in (pl.Utf8, pl.String):
                return False
            avg_len = non_null.str.len_chars().mean()
            if avg_len is None or avg_len < value:
                return False
        elif key == "numeric":
            if col.dtype not in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.Float32, pl.Float64):
                return False
        elif key == "short_strings":
            if non_null.dtype not in (pl.Utf8, pl.String):
                return False
            avg_len = non_null.str.len_chars().mean()
            if avg_len is None or avg_len >= 5:
                return False
    return True

def _check_format_match(non_null: pl.Series, format_type: str) -> bool:
    if non_null.dtype not in (pl.Utf8, pl.String):
        return False
    if format_type == "email":
        matches = non_null.str.contains(r"@.*\.", literal=False).sum()
        return matches / len(non_null) >= 0.70
    elif format_type == "phone":
        matches = non_null.str.contains(r"\d{3}.*\d{3}.*\d{4}", literal=False).sum()
        return matches / len(non_null) >= 0.70
    elif format_type == "date":
        matches = non_null.str.contains(r"\d{4}-\d{2}-\d{2}", literal=False).sum()
        return matches / len(non_null) >= 0.50
    return False
