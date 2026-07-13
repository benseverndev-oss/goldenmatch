"""Column-level validation rules with quarantine support for GoldenMatch."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from goldenmatch._polars_lazy import pl


@dataclass
class ValidationRule:
    """A single column validation rule.

    Attributes:
        column: Column name to validate.
        rule_type: One of: regex, min_length, max_length, not_null, in_set, format.
        params: Rule-specific parameters.
        action: "flag" (add flag column), "null" (set to null), "quarantine" (move row).
    """
    column: str
    rule_type: str
    params: dict = field(default_factory=dict)
    action: str = "flag"


def _check_format_email(value: str | None) -> bool:
    """Check if value looks like a valid email."""
    if value is None:
        return False
    value = str(value).strip().lower()
    if not value or "@" not in value:
        return False
    parts = value.split("@")
    if len(parts) != 2:
        return False
    return "." in parts[1] and len(parts[0]) > 0 and len(parts[1]) > 2


def _check_format_phone(value: str | None) -> bool:
    """Check if value looks like a valid phone number."""
    if value is None:
        return False
    digits = re.sub(r"\D", "", str(value))
    return len(digits) >= 7 and digits.isdigit()


def _check_format_zip5(value: str | None) -> bool:
    """Check if value looks like a valid 5-digit ZIP code."""
    if value is None:
        return False
    clean = str(value).strip().split("-")[0].split(" ")[0]
    digits = re.sub(r"\D", "", clean)
    return len(digits) >= 5 and digits[:5].isdigit()


_FORMAT_CHECKERS = {
    "email": _check_format_email,
    "phone": _check_format_phone,
    "zip5": _check_format_zip5,
}


def validate_dataframe(
    df,  # pl.DataFrame | pa.Table (Frame lane)
    rules: list[ValidationRule],
) -> tuple:
    """Validate a DataFrame against a list of rules.

    Returns:
        (valid_df, quarantine_df, validation_report)

        - valid_df: rows that passed all rules (with flags/nulls applied)
        - quarantine_df: rows that failed quarantine rules (with __quarantine_reason__)
        - validation_report: list of dicts with rule evaluation stats
    """
    # W5b-3: seam-driven. PolarsFrame.evaluate_validation_rule carries the
    # legacy _evaluate_rule branches VERBATIM (the delegation oracle moved
    # into the seam); mask bookkeeping runs in Python lists on both backends.
    # W-3 widening: dual-rep entry -- df may be a pa.Table (Frame lane).
    from goldenmatch.core.frame import PolarsFrame, column_from_values, to_frame

    validation_report: list[dict] = []
    if isinstance(df, pl.DataFrame):
        frame = to_frame(df.clone())
        backend = "polars"
    else:
        frame = to_frame(df)  # pa.Table is immutable; no clone needed
        backend = "polars" if isinstance(frame, PolarsFrame) else "arrow"
    height = frame.height
    quarantine_flags: list[bool] = [False] * height
    quarantine_reasons: list[list[str]] = [[] for _ in range(height)]

    for rule in rules:
        if rule.column not in frame.columns:
            raise ValueError(f"Column {rule.column!r} not found in DataFrame")

        params = dict(rule.params or {})
        if rule.rule_type == "format":
            fmt_type = params["type"]
            checker = _FORMAT_CHECKERS.get(fmt_type)
            if checker is None:
                raise ValueError(
                    f"Unknown format type: {fmt_type!r}. "
                    f"Available: {sorted(_FORMAT_CHECKERS)}"
                )
            params["__checker__"] = checker
        passed = frame.evaluate_validation_rule(rule.column, rule.rule_type, params)
        passed_list = [bool(v) for v in passed.to_list()]

        total_checked = height
        passed_count = sum(passed_list)
        failed_count = total_checked - passed_count
        fail_rate = failed_count / total_checked if total_checked > 0 else 0.0

        validation_report.append({
            "rule": rule.rule_type,
            "column": rule.column,
            "total_checked": total_checked,
            "passed": passed_count,
            "failed": failed_count,
            "fail_rate": fail_rate,
        })

        if rule.action == "flag":
            flag_col = f"__vf_{rule.column}_{rule.rule_type}__"
            frame = frame.with_column(flag_col, passed)

        elif rule.action == "null":
            failed_col = column_from_values(
                [not p for p in passed_list], "bool", backend=backend
            )
            frame = frame.with_null_where(rule.column, failed_col)

        elif rule.action == "quarantine":
            for i, is_passed in enumerate(passed_list):
                if not is_passed:
                    quarantine_flags[i] = True
                    quarantine_reasons[i].append(
                        f"{rule.column}:{rule.rule_type}"
                    )

    # Split into valid and quarantine DataFrames
    q_mask = column_from_values(quarantine_flags, "bool", backend=backend)
    keep_mask = column_from_values(
        [not q for q in quarantine_flags], "bool", backend=backend
    )
    quarantine_frame = frame.filter_mask(q_mask)
    valid_df = frame.filter_mask(keep_mask).native

    # Add quarantine reason column
    if quarantine_frame.height > 0:
        reason_series_data = [
            "; ".join(quarantine_reasons[i])
            for i, is_q in enumerate(quarantine_flags)
            if is_q
        ]
        quarantine_df = quarantine_frame.with_column(
            "__quarantine_reason__",
            column_from_values(reason_series_data, "utf8", backend=backend),
        ).native
    elif backend == "polars":
        quarantine_df = quarantine_frame.native.with_columns(
            pl.Series("__quarantine_reason__", [], dtype=pl.Utf8)
        )
    else:
        import pyarrow as pa

        quarantine_df = quarantine_frame.native.append_column(
            "__quarantine_reason__", pa.array([], type=pa.large_string())
        )

    return valid_df, quarantine_df, validation_report
