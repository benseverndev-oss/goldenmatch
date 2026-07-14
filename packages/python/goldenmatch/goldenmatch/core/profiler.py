"""Data quality profiler for GoldenMatch."""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.table import Table

from goldenmatch._polars_lazy import pl

if TYPE_CHECKING:
    from goldenmatch.core.frame import Column

# ── Heuristic type detection helpers ────────────────────────────────────────

_PHONE_STRIP_RE = re.compile(r"[()\-+.\s]")
_DATE_PATTERNS = [
    re.compile(r"^\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}$"),
    re.compile(r"^\d{4}[/\-]\d{1,2}[/\-]\d{1,2}$"),
    re.compile(r"^\d{1,2}\s\w+\s\d{2,4}$"),
]
_ADDRESS_WORDS = re.compile(
    r"\b(st|street|ave|avenue|rd|road|dr|drive|blvd|boulevard|ln|lane|ct|court|way|pl|place|cir|circle)\b",
    re.IGNORECASE,
)
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z \-']{0,28}[A-Za-z]$|^[A-Za-z]{2,3}$")


def _guess_type(values: list[str]) -> str:
    """Heuristic guess of what the string data looks like."""
    if not values:
        return "text"

    n = len(values)

    # email: >60% contain @ and a dot after @
    email_count = sum(1 for v in values if "@" in v and "." in v.split("@")[-1])
    if email_count / n > 0.6:
        return "email"

    # phone: >60% are mostly digits after stripping common phone chars
    phone_count = 0
    for v in values:
        stripped = _PHONE_STRIP_RE.sub("", v)
        if stripped.isdigit() and 7 <= len(stripped) <= 15:
            phone_count += 1
    if phone_count / n > 0.6:
        return "phone"

    # zip: >60% are 5 or 9-10 digit strings
    zip_count = 0
    for v in values:
        clean = v.replace("-", "")
        if clean.isdigit() and len(clean) in (5, 9):
            zip_count += 1
    if zip_count / n > 0.6:
        return "zip"

    # state: >60% are exactly 2 uppercase letters
    state_count = sum(1 for v in values if len(v) == 2 and v.isalpha() and v.isupper())
    if state_count / n > 0.6:
        return "state"

    # numeric: >60% parse as numbers
    numeric_count = 0
    for v in values:
        try:
            float(v.replace(",", ""))
            numeric_count += 1
        except ValueError:
            pass
    if numeric_count / n > 0.6:
        return "numeric"

    # name: >60% are 2-30 chars, alpha + spaces + hyphens only
    name_count = sum(1 for v in values if _NAME_RE.match(v.strip()))
    if name_count / n > 0.6:
        return "name"

    # address: >40% contain digits AND common address words
    addr_count = sum(
        1 for v in values
        if any(c.isdigit() for c in v) and _ADDRESS_WORDS.search(v)
    )
    if addr_count / n > 0.4:
        return "address"

    # date: >40% match common date patterns
    date_count = sum(
        1 for v in values
        if any(p.match(v.strip()) for p in _DATE_PATTERNS)
    )
    if date_count / n > 0.4:
        return "date"

    return "text"


# ── Column profiling ───────────────────────────────────────────────────────


def _as_column(series: Any) -> Column:
    """Coerce a pl.Series (or an already-seam Column) to the Column seam.

    Typed Any -> Column so pyright never forms a Series & Column
    intersection at call sites (it would resolve seam methods through
    pl.Series and reject the parity twins)."""
    from goldenmatch.core.frame import Column, PolarsColumn

    if isinstance(series, Column):
        return series
    return PolarsColumn(series)


def _polars_importable() -> bool:
    """True when polars can be imported (False in the D6 zero-polars end-state).

    Under the zero-polars import blocker the ``import polars`` raises
    ImportError, so this returns False and callers take the seam-native path."""
    try:
        import polars  # noqa: F401

        return True
    except ImportError:
        return False


def profile_column(series: Any, name: str | None = None) -> dict[str, Any]:
    """Profile a single column and return a statistics dict.

    W3c: internals route through the Column seam (PolarsColumn wraps the
    series -- byte-identical delegation; an ArrowColumn caller gets the same
    stats via the parity-pinned pc twins).

    ``series`` may be a ``pl.Series`` (name + dtype spelling read intrinsically,
    byte-identical) or an already-seam ``Column`` (polars-free path); on the
    latter the caller passes ``name`` and dtype falls back to the semantic
    spelling (the ``pl.Series`` dtype string is unavailable without polars)."""
    col = _as_column(series)
    _is_pl_series = "polars" in sys.modules and isinstance(series, pl.Series)
    if _is_pl_series:
        name = series.name
        dtype = str(series.dtype)
    else:
        name = name if name is not None else "<column>"
        dtype = col.semantic_dtype()
    total = len(col)
    null_count = col.null_count()
    null_rate = null_count / total if total > 0 else 0.0

    non_null = col.drop_nulls()
    non_null_count = len(non_null)
    unique_count = non_null.n_unique() if non_null_count > 0 else 0
    unique_rate = unique_count / non_null_count if non_null_count > 0 else 0.0

    is_string = col.semantic_dtype() == "text"

    min_length: int | None = None
    max_length: int | None = None
    avg_length: float | None = None
    empty_string_count = 0
    suspected_type = "text"

    if is_string:
        lengths = non_null.str_len_chars()
        if non_null_count > 0:
            min_length = int(lengths.min())  # type: ignore[arg-type]
            max_length = int(lengths.max())  # type: ignore[arg-type]
            avg_length = float(lengths.mean())  # type: ignore[arg-type]

        # Count empty or whitespace-only strings (excluding actual nulls)
        empty_string_count = col.blank_count() if non_null_count > 0 else 0

        # Heuristic type detection on non-null, non-empty values
        non_empty_vals = [
            v for v in non_null.to_list() if isinstance(v, str) and v.strip()
        ]
        suspected_type = _guess_type(non_empty_vals)
    else:
        empty_string_count = 0

    # Sample values
    sample_values: list[Any] = non_null.to_list()[:5] if non_null_count > 0 else []

    return {
        "name": name,
        "dtype": dtype,
        "total": total,
        "null_count": null_count,
        "null_rate": null_rate,
        "unique_count": unique_count,
        "unique_rate": unique_rate,
        "min_length": min_length,
        "max_length": max_length,
        "avg_length": avg_length,
        "sample_values": sample_values,
        "empty_string_count": empty_string_count,
        "suspected_type": suspected_type,
    }


# ── DataFrame profiling ───────────────────────────────────────────────────


def _columns_as_polars_series(frame: Any) -> list[Any]:
    """One polars Series per column, for the (still polars-bound) ``profile_column``.

    Polars backend: hand back the native Series verbatim (byte-identical to the
    pre-seam ``df[col]`` path -- same dtype spelling, no round-trip). Arrow
    backend: materialize each column into a polars Series via its Arrow array,
    so ``profile_column``'s per-column stats are computed identically regardless
    of the input backend (``profile_column`` itself is ported in a later PR).
    """
    from goldenmatch.core.frame import PolarsFrame

    if isinstance(frame, PolarsFrame):
        native = frame.native
        return [native[c] for c in frame.columns]
    # Arrow frame: wrap each column as a pl.Series so profile_column reads the
    # exact same name + polars dtype spelling as the polars lane (byte-identical)
    # -- but ONLY when polars is importable. In the D6 zero-polars end-state the
    # wrap is impossible, so hand the caller the seam Column instead (name is
    # threaded separately in profile_dataframe).
    if _polars_importable():
        return [pl.Series(c, frame.column(c).to_arrow()) for c in frame.columns]
    return [frame.column(c) for c in frame.columns]


def _count_all_empty_rows(frame: Any) -> int:
    """Backend-agnostic all-empty-row count (cold-path Python fold).

    A row is empty when EVERY cell is empty, where a cell is empty if it is
    null OR (for string columns only) whitespace-only. Non-string cells are
    empty only when null -- a ``0`` int is NOT empty. Mirrors the Polars
    ``is_null() | (strip_chars() == "")`` (string) / ``is_null()`` (other)
    conjunction the pre-seam path built. String-ness comes from the column's
    semantic dtype (``"text"`` == Polars ``Utf8``/``String``).
    """
    cols = frame.columns
    if not cols:
        return 0
    is_string = {c: frame.column(c).semantic_dtype() == "text" for c in cols}
    count = 0
    for row in frame.select_dicts(cols):
        empty = True
        for c in cols:
            v = row[c]
            if v is None:
                continue
            if is_string[c] and isinstance(v, str) and v.strip() == "":
                continue
            empty = False
            break
        if empty:
            count += 1
    return count


def profile_dataframe(df: Any) -> dict[str, Any]:
    """Profile an entire DataFrame and return a comprehensive report dict.

    Accepts a ``pl.DataFrame``, a ``pa.Table``, or an already-wrapped ``Frame``
    (``to_frame`` is idempotent). Byte-identical on the Polars path.
    """
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)
    total_rows = frame.height
    total_columns = len(frame.columns)
    # Thread the column name explicitly: profile_column reads it intrinsically
    # from a pl.Series (name arg ignored, byte-identical) and from the arg on
    # the polars-free seam-Column path.
    columns = [
        profile_column(s, name=c)
        for s, c in zip(_columns_as_polars_series(frame), frame.columns)
    ]

    # Duplicate rows: count of rows that appear more than once
    # (== total_rows - distinct_row_count(), full-row identity over all columns).
    duplicate_row_count = frame.count_duplicate_rows()

    # Empty rows: every cell null, or whitespace-only for string columns.
    empty_row_count = _count_all_empty_rows(frame)

    # Issue detection
    issues: list[dict[str, Any]] = []

    # Name-to-type mapping for mismatch detection
    _name_type_hints = {
        "email": "email",
        "e_mail": "email",
        "phone": "phone",
        "telephone": "phone",
        "tel": "phone",
        "zip": "zip",
        "zipcode": "zip",
        "zip_code": "zip",
        "postal": "zip",
        "name": "name",
        "first_name": "name",
        "last_name": "name",
        "fname": "name",
        "lname": "name",
        "address": "address",
        "addr": "address",
        "street": "address",
        "state": "state",
    }

    for cp in columns:
        col_name = cp["name"]
        null_rate = cp["null_rate"]

        # ERROR: >95% nulls
        if null_rate > 0.95:
            issues.append({
                "severity": "error",
                "column": col_name,
                "message": f"Column '{col_name}' has {null_rate:.0%} null values (likely empty/wrong column).",
            })
        # WARNING: >50% nulls
        elif null_rate > 0.50:
            issues.append({
                "severity": "warning",
                "column": col_name,
                "message": f"Column '{col_name}' has {null_rate:.0%} null values.",
            })

        # WARNING: suspected type mismatch
        col_lower = col_name.lower().replace(" ", "_")
        expected_type = _name_type_hints.get(col_lower)
        if expected_type and cp["suspected_type"] != expected_type:
            issues.append({
                "severity": "warning",
                "column": col_name,
                "message": (
                    f"Column '{col_name}' is named like a {expected_type} column "
                    f"but data looks like '{cp['suspected_type']}'."
                ),
            })

        # WARNING: >20% empty strings
        if cp["total"] > 0 and cp["empty_string_count"] / cp["total"] > 0.20:
            issues.append({
                "severity": "warning",
                "column": col_name,
                "message": f"Column '{col_name}' has {cp['empty_string_count']} empty/whitespace-only values ({cp['empty_string_count'] / cp['total']:.0%}).",
            })

        # INFO: low cardinality
        non_null_count = cp["total"] - cp["null_count"]
        if cp["total"] > 100 and cp["unique_count"] < 5 and non_null_count > 0:
            issues.append({
                "severity": "info",
                "column": col_name,
                "message": f"Column '{col_name}' has very low cardinality ({cp['unique_count']} unique values).",
            })

        # INFO: appears to be an ID
        if cp["unique_rate"] == 1.0 and cp["null_count"] == 0:
            issues.append({
                "severity": "info",
                "column": col_name,
                "message": f"Column '{col_name}' appears to be a unique ID (100% unique, no nulls).",
            })

    # WARNING: duplicate rows
    if duplicate_row_count > 0:
        issues.append({
            "severity": "warning",
            "column": None,
            "message": f"Dataset contains {duplicate_row_count} duplicate row(s).",
        })

    return {
        "total_rows": total_rows,
        "total_columns": total_columns,
        "columns": columns,
        "duplicate_row_count": duplicate_row_count,
        "empty_row_count": empty_row_count,
        "issues": issues,
    }


# ── Report formatting ─────────────────────────────────────────────────────

_SEVERITY_STYLE = {
    "error": "bold red",
    "warning": "yellow",
    "info": "dim",
}

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def format_profile_report(profile: dict, df: pl.DataFrame | None = None) -> str:
    """Return a Rich-renderable string summary of a profile report.

    Args:
        profile: The output of profile_dataframe().
        df: Optional DataFrame to show a sample from.

    Returns:
        A string containing Rich markup for display.
    """
    from io import StringIO

    from rich.console import Console as _Console

    buf = StringIO()
    console = _Console(file=buf, force_terminal=True, width=120)

    # ── Overview panel ────────────────────────────────────────────────
    overview_lines = [
        f"[bold]Rows:[/bold] {profile['total_rows']}",
        f"[bold]Columns:[/bold] {profile['total_columns']}",
        f"[bold]Duplicate rows:[/bold] {profile['duplicate_row_count']}",
        f"[bold]Empty rows:[/bold] {profile['empty_row_count']}",
    ]
    console.print(Panel("\n".join(overview_lines), title="Overview", border_style="cyan"))

    # ── Per-column table ──────────────────────────────────────────────
    col_table = Table(title="Column Summary", show_lines=True)
    col_table.add_column("Name", style="bold")
    col_table.add_column("Type")
    col_table.add_column("Suspected")
    col_table.add_column("Null %", justify="right")
    col_table.add_column("Unique %", justify="right")
    col_table.add_column("Issues", justify="right")

    issue_counts: dict[str, int] = {}
    for iss in profile["issues"]:
        col = iss.get("column")
        if col:
            issue_counts[col] = issue_counts.get(col, 0) + 1

    for cp in profile["columns"]:
        null_pct = f"{cp['null_rate']:.1%}"
        unique_pct = f"{cp['unique_rate']:.1%}"
        n_issues = issue_counts.get(cp["name"], 0)
        issue_str = str(n_issues) if n_issues else "-"
        col_table.add_row(
            cp["name"],
            cp["dtype"],
            cp["suspected_type"],
            null_pct,
            unique_pct,
            issue_str,
        )

    console.print(col_table)

    # ── Issues list ───────────────────────────────────────────────────
    sorted_issues = sorted(
        profile["issues"],
        key=lambda i: _SEVERITY_ORDER.get(i["severity"], 99),
    )
    if sorted_issues:
        console.print()
        console.print(Panel("[bold]Issues[/bold]", border_style="yellow"))
        for iss in sorted_issues:
            sev = iss["severity"].upper()
            style = _SEVERITY_STYLE.get(iss["severity"], "")
            console.print(f"  [{style}][{sev}][/{style}] {iss['message']}")
    else:
        console.print()
        console.print("[green]No issues detected.[/green]")

    # ── Data sample ───────────────────────────────────────────────────
    if df is not None and df.height > 0:
        console.print()
        sample = df.head(5)
        sample_table = Table(title="Data Sample (first 5 rows)", show_lines=True)
        for col in sample.columns:
            sample_table.add_column(col)
        for row in sample.iter_rows():
            sample_table.add_row(*(str(v) if v is not None else "[dim]null[/dim]" for v in row))
        console.print(sample_table)

    return buf.getvalue()
