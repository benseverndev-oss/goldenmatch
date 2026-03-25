from __future__ import annotations

from goldenflow.engine.profiler_bridge import ColumnProfile
from goldenflow.transforms import TransformInfo, list_transforms


# Finding check → transform mapping for --from-findings integration
FINDING_TRANSFORM_MAP: dict[str, list[str]] = {
    "format_inconsistency": ["date_iso8601", "phone_e164"],
    "whitespace_issues": ["strip", "collapse_whitespace"],
    "mixed_case": ["lowercase", "title_case"],
    "null_variants": ["null_standardize"],
    "unicode_issues": ["normalize_unicode"],
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
