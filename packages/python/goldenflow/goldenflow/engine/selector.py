from __future__ import annotations

from goldenflow.engine.profiler_bridge import ColumnProfile
from goldenflow.transforms import TransformInfo, list_transforms


# Finding check → transform mapping for --from-findings integration
# Keys are real GoldenCheck check names (14 total from column + relation profilers)
FINDING_TRANSFORM_MAP: dict[str, list[str]] = {
    # Column-level checks
    "type_inference": ["strip", "to_integer"],
    "nullability": ["null_standardize"],
    "uniqueness": ["strip", "collapse_whitespace", "email_normalize"],
    "format_detection": ["phone_e164", "email_normalize", "date_iso8601", "zip_normalize"],
    "range_distribution": ["clamp"],
    "cardinality": ["category_auto_correct", "category_standardize"],
    "pattern_consistency": ["phone_e164", "date_iso8601", "zip_normalize", "ssn_format"],
    "encoding_detection": ["normalize_unicode", "normalize_quotes", "fix_mojibake"],
    "sequence_detection": ["pad_left"],
    "drift_detection": [],  # Detection-only, no automatic fix
    # Cross-column / relation checks
    "temporal_order": ["date_iso8601", "date_validate"],
    "null_correlation": [],  # Detection-only, no automatic fix
    "cross_column_validation": ["clamp"],
    "cross_column": ["date_validate", "age_from_dob"],
}


def select_transforms(
    profile: ColumnProfile,
    confidence_threshold: float = 0.8,
) -> list[TransformInfo]:
    """Select auto-applicable transforms for a column based on its profile."""
    all_transforms = list_transforms()
    selected: list[TransformInfo] = []

    for t in all_transforms:
        if not t.auto_apply:
            continue
        # Match if column type is in the transform's input_types
        if profile.inferred_type in t.input_types:
            selected.append(t)
        # "string" transforms apply to all string-like types
        elif "string" in t.input_types and profile.inferred_type in (
            "string", "email", "phone", "name", "address", "date",
        ):
            selected.append(t)

    # Filter out category_auto_correct for high-cardinality columns
    # (only apply to categorical columns — low unique count relative to rows)
    if profile.unique_pct > 0.1:  # more than 10% unique values = not categorical
        selected = [t for t in selected if t.name != "category_auto_correct"]

    # Sort by priority descending (higher = runs first)
    selected.sort(key=lambda t: t.priority, reverse=True)
    return selected


def select_from_findings(
    findings: list[dict],
) -> dict[str, list[str]]:
    """Map GoldenCheck findings to transform names. Returns {column: [transform_names]}."""
    column_transforms: dict[str, list[str]] = {}
    for finding in findings:
        check = finding.get("check", "")
        column = finding.get("column", "")
        if not column:
            continue
        transform_names = FINDING_TRANSFORM_MAP.get(check, [])
        if transform_names:
            column_transforms.setdefault(column, []).extend(transform_names)
    # Deduplicate
    return {col: list(dict.fromkeys(names)) for col, names in column_transforms.items()}
