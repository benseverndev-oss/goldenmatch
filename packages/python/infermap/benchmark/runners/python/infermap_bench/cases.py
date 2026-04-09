"""Load committed case directories into `Case` objects."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from infermap.providers.file import FileProvider
from infermap.types import SchemaInfo

from .manifest import CaseRef


class IncompleteCaseError(ValueError):
    """Raised when a case directory is missing one of the four required files."""


class ExpectedCoverageError(ValueError):
    """Raised when expected.json does not account for every source/target column."""


class FieldCountMismatchError(ValueError):
    """Raised when the manifest's field_counts disagree with actual CSV widths."""


@dataclass(frozen=True)
class Expected:
    mappings: list[dict[str, str]]  # [{"source": "...", "target": "..."}]
    unmapped_source: list[str]
    unmapped_target: list[str]


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    subcategory: str
    tags: list[str]
    expected_difficulty: str
    source_schema: SchemaInfo
    target_schema: SchemaInfo
    expected: Expected


_REQUIRED_FILES = ("source.csv", "target.csv", "expected.json", "case.json")


def load_case(benchmark_root: Path | str, ref: CaseRef) -> Case:
    """Resolve a CaseRef into a fully-loaded Case.

    Args:
        benchmark_root: the directory containing the `cases/` tree (typically
            `benchmark/` or a self-test root).
        ref: the reference from the manifest.

    Raises:
        IncompleteCaseError: if any of the four required files is missing.
        FieldCountMismatchError: if manifest field_counts disagree with CSV widths.
        ExpectedCoverageError: if expected.json has a structural problem or
            fails the coverage invariant (every column in exactly one bucket).
    """
    benchmark_root = Path(benchmark_root)
    case_dir = benchmark_root / ref.path
    for required in _REQUIRED_FILES:
        if not (case_dir / required).exists():
            raise IncompleteCaseError(f"cases/{ref.id}: missing {required}")

    provider = FileProvider()
    src_schema = provider.extract(case_dir / "source.csv")
    tgt_schema = provider.extract(case_dir / "target.csv")

    actual_src_n = len(src_schema.fields)
    actual_tgt_n = len(tgt_schema.fields)
    if actual_src_n != ref.field_counts["source"]:
        raise FieldCountMismatchError(
            f"cases/{ref.id}: manifest says source has {ref.field_counts['source']} "
            f"fields, CSV has {actual_src_n}"
        )
    if actual_tgt_n != ref.field_counts["target"]:
        raise FieldCountMismatchError(
            f"cases/{ref.id}: manifest says target has {ref.field_counts['target']} "
            f"fields, CSV has {actual_tgt_n}"
        )

    expected_raw = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected = _parse_expected(ref.id, expected_raw, src_schema, tgt_schema)

    return Case(
        id=ref.id,
        category=ref.category,
        subcategory=ref.subcategory,
        tags=list(ref.tags),
        expected_difficulty=ref.expected_difficulty,
        source_schema=src_schema,
        target_schema=tgt_schema,
        expected=expected,
    )


def _parse_expected(
    case_id: str,
    raw: object,
    src_schema: SchemaInfo,
    tgt_schema: SchemaInfo,
) -> Expected:
    if not isinstance(raw, dict):
        raise ExpectedCoverageError(f"cases/{case_id}: expected.json must be an object")
    for key in ("mappings", "unmapped_source", "unmapped_target"):
        if key not in raw:
            raise ExpectedCoverageError(f"cases/{case_id}: expected.json missing '{key}'")

    if not isinstance(raw["mappings"], list):
        raise ExpectedCoverageError(f"cases/{case_id}: expected.json 'mappings' must be a list")

    mappings: list[dict[str, str]] = []
    for m in raw["mappings"]:
        if not isinstance(m, dict) or "source" not in m or "target" not in m:
            raise ExpectedCoverageError(
                f"cases/{case_id}: each mapping must have 'source' and 'target'"
            )
        mappings.append({"source": str(m["source"]), "target": str(m["target"])})

    unmapped_source = list(raw["unmapped_source"])
    unmapped_target = list(raw["unmapped_target"])

    src_names = {f.name for f in src_schema.fields}
    tgt_names = {f.name for f in tgt_schema.fields}

    src_mapped = {m["source"] for m in mappings}
    tgt_mapped = {m["target"] for m in mappings}
    src_unmapped_set = set(unmapped_source)
    tgt_unmapped_set = set(unmapped_target)

    # Coverage: every source/target column appears in exactly one bucket.
    src_categorized = src_mapped | src_unmapped_set
    tgt_categorized = tgt_mapped | tgt_unmapped_set

    missing_src = src_names - src_categorized
    if missing_src:
        raise ExpectedCoverageError(
            f"cases/{case_id}: source columns not categorized in expected.json: "
            f"{sorted(missing_src)}"
        )
    missing_tgt = tgt_names - tgt_categorized
    if missing_tgt:
        raise ExpectedCoverageError(
            f"cases/{case_id}: target columns not categorized in expected.json: "
            f"{sorted(missing_tgt)}"
        )

    # Overlap: a column cannot be both mapped and unmapped.
    if src_mapped & src_unmapped_set:
        raise ExpectedCoverageError(
            f"cases/{case_id}: source columns in both mappings and unmapped_source: "
            f"{sorted(src_mapped & src_unmapped_set)}"
        )
    if tgt_mapped & tgt_unmapped_set:
        raise ExpectedCoverageError(
            f"cases/{case_id}: target columns in both mappings and unmapped_target: "
            f"{sorted(tgt_mapped & tgt_unmapped_set)}"
        )

    return Expected(
        mappings=mappings,
        unmapped_source=unmapped_source,
        unmapped_target=unmapped_target,
    )
