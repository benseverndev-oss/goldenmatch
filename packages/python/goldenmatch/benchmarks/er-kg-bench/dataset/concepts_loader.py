"""Pure stdlib loader + validator for concepts.jsonl.

No goldenmatch or erkgbench imports here.  The drift-guard test
(test_valid_classes_match_run_class_order) imports erkgbench.run; this
module does not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

VALID_FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        "abbreviation",
        "nickname_alias",
        "synonym_brand",
        "same_name_collision",
        "cross_lingual",
        "typo",
        "org_suffix",
        "temporal_version",
        "cross_document_exact",
    }
)

_CANONICAL_ID_RE = re.compile(r"^(Q\d+|gm:[a-z0-9_]+)$")


@dataclass(frozen=True)
class Variant:
    surface: str
    failure_class: str


@dataclass(frozen=True)
class Concept:
    concept: str
    canonical_id: str
    entity_type: str
    context: str
    variants: tuple[Variant, ...]


def load_concepts(path: Path | str) -> list[Concept]:
    """Parse and validate a concepts.jsonl file.

    Each non-blank line must be a JSON object with the fields:
      concept, canonical_id, entity_type, context, variants

    Raises ValueError on any validation failure.
    """
    path = Path(path)
    results: list[Concept] = []

    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {lineno}: invalid JSON: {exc}") from exc

            # --- required string fields ---
            for field in ("concept", "entity_type", "context"):
                val = obj.get(field)
                if not isinstance(val, str) or not val:
                    raise ValueError(
                        f"line {lineno}: field '{field}' must be a non-empty string"
                    )

            # --- canonical_id ---
            cid = obj.get("canonical_id")
            if not isinstance(cid, str) or not _CANONICAL_ID_RE.match(cid):
                raise ValueError(
                    f"line {lineno}: canonical_id '{cid}' does not match Q\\d+ or gm:[a-z0-9_]+"
                )

            # --- variants ---
            raw_variants = obj.get("variants")
            if not isinstance(raw_variants, list) or not raw_variants:
                raise ValueError(f"line {lineno}: 'variants' must be a non-empty list")

            variants: list[Variant] = []
            for i, v in enumerate(raw_variants):
                surface = v.get("surface") if isinstance(v, dict) else None
                if not isinstance(surface, str) or not surface:
                    raise ValueError(
                        f"line {lineno}, variant {i}: 'surface' must be a non-empty string"
                    )
                fc = v.get("failure_class") if isinstance(v, dict) else None
                if fc not in VALID_FAILURE_CLASSES:
                    raise ValueError(
                        f"line {lineno}, variant {i}: "
                        f"failure_class '{fc}' is not in VALID_FAILURE_CLASSES"
                    )
                variants.append(Variant(surface=surface, failure_class=fc))

            results.append(
                Concept(
                    concept=obj["concept"],
                    canonical_id=cid,
                    entity_type=obj["entity_type"],
                    context=obj["context"],
                    variants=tuple(variants),
                )
            )

    return results
