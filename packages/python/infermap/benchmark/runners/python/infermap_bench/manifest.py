"""Load and validate benchmark/manifest.json."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import MANIFEST_VERSION

Category = Literal["valentine", "real_world", "synthetic"]
Difficulty = Literal["easy", "medium", "hard"]


class InvalidManifestError(ValueError):
    """Raised when manifest.json has a structural problem."""


class IncompatibleManifestError(ValueError):
    """Raised when manifest.version exceeds what this runner supports."""


@dataclass(frozen=True)
class CaseSource:
    name: str
    url: str
    license: str
    attribution: str


@dataclass(frozen=True)
class CaseRef:
    """Lightweight reference to a committed case — no CSV data loaded yet."""

    id: str
    path: str  # relative to the manifest's parent directory
    category: Category
    subcategory: str
    source: CaseSource
    tags: list[str]
    expected_difficulty: Difficulty
    field_counts: dict[str, int]


_VALID_DIFFICULTIES = {"easy", "medium", "hard"}
_VALID_CATEGORIES = {"valentine", "real_world", "synthetic"}
_REQUIRED_CASE_FIELDS = {
    "id", "path", "category", "subcategory", "source",
    "tags", "expected_difficulty", "field_counts",
}
_REQUIRED_SOURCE_FIELDS = {"name", "url", "license", "attribution"}


def load_manifest(path: Path | str) -> list[CaseRef]:
    """Load and validate a manifest.json file.

    Raises:
        FileNotFoundError: if the file does not exist.
        InvalidManifestError: on any structural validation failure.
        IncompatibleManifestError: if manifest.version > runner MANIFEST_VERSION.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidManifestError(f"Manifest is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise InvalidManifestError("Manifest root must be a JSON object")
    if "version" not in raw:
        raise InvalidManifestError("Manifest is missing required 'version' field")
    if not isinstance(raw["version"], int) or isinstance(raw["version"], bool):
        raise InvalidManifestError("Manifest 'version' must be an integer")
    if raw["version"] > MANIFEST_VERSION:
        raise IncompatibleManifestError(
            f"Manifest version {raw['version']} exceeds runner max {MANIFEST_VERSION}. "
            f"Upgrade your infermap-bench runner."
        )
    if "cases" not in raw:
        raise InvalidManifestError("Manifest is missing required 'cases' field")
    if not isinstance(raw["cases"], list):
        raise InvalidManifestError("Manifest 'cases' must be an array")

    refs: list[CaseRef] = []
    for idx, entry in enumerate(raw["cases"]):
        refs.append(_validate_case_entry(entry, idx))
    return refs


def _validate_case_entry(entry: object, idx: int) -> CaseRef:
    if not isinstance(entry, dict):
        raise InvalidManifestError(f"cases[{idx}] must be a JSON object")
    missing = _REQUIRED_CASE_FIELDS - entry.keys()
    if missing:
        raise InvalidManifestError(
            f"cases[{idx}] missing required fields: {sorted(missing)}"
        )

    category = entry["category"]
    if category not in _VALID_CATEGORIES:
        raise InvalidManifestError(
            f"cases[{idx}].category must be one of {sorted(_VALID_CATEGORIES)}, got {category!r}"
        )

    difficulty = entry["expected_difficulty"]
    if difficulty not in _VALID_DIFFICULTIES:
        raise InvalidManifestError(
            f"cases[{idx}].expected_difficulty must be one of {sorted(_VALID_DIFFICULTIES)}, "
            f"got {difficulty!r}"
        )

    src = entry["source"]
    if not isinstance(src, dict):
        raise InvalidManifestError(f"cases[{idx}].source must be a JSON object")
    src_missing = _REQUIRED_SOURCE_FIELDS - src.keys()
    if src_missing:
        raise InvalidManifestError(
            f"cases[{idx}].source missing fields: {sorted(src_missing)}"
        )

    tags = entry["tags"]
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise InvalidManifestError(f"cases[{idx}].tags must be a list of strings")

    field_counts = entry["field_counts"]
    if not isinstance(field_counts, dict):
        raise InvalidManifestError(f"cases[{idx}].field_counts must be an object")
    if set(field_counts.keys()) != {"source", "target"}:
        raise InvalidManifestError(
            f"cases[{idx}].field_counts must have exactly 'source' and 'target' keys"
        )
    for key in ("source", "target"):
        val = field_counts[key]
        if not isinstance(val, int) or isinstance(val, bool):
            raise InvalidManifestError(
                f"cases[{idx}].field_counts.{key} must be an integer, got {type(val).__name__}"
            )

    return CaseRef(
        id=entry["id"],
        path=entry["path"],
        category=category,  # type: ignore[arg-type]
        subcategory=entry["subcategory"],
        source=CaseSource(
            name=src["name"],
            url=src["url"],
            license=src["license"],
            attribution=src["attribution"],
        ),
        tags=list(tags),
        expected_difficulty=difficulty,  # type: ignore[arg-type]
        field_counts={"source": int(field_counts["source"]), "target": int(field_counts["target"])},
    )
