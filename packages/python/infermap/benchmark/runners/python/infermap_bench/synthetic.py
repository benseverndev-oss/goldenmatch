"""Synthetic case generator — canonical (Python-only) per spec §7.4.

This module contains the pure transform primitives used by the generator
(Task 4.3). Transforms that mutate an existing field (renames, case changes,
sample typos, dtype casts, reorder) preserve `Field.canonical_name` so the
generator can recover ground truth regardless of name/sample mutations.
`drop_columns` removes fields entirely and `add_distractors` inserts new
fields with `canonical_name=None`; both are tracked explicitly by the
generator when computing expected mappings.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Field:
    name: str
    dtype: str
    samples: list[str]
    canonical_name: str | None = None


@dataclass(frozen=True)
class Schema:
    name: str
    fields: list[Field]


# ---------- helpers ----------

_SNAKE_RE = re.compile(r"_([a-z])")
_CAMEL_RE = re.compile(r"([a-z])([A-Z])")


def _to_camel(snake: str) -> str:
    return _SNAKE_RE.sub(lambda m: m.group(1).upper(), snake)


def _to_snake(camel: str) -> str:
    return _CAMEL_RE.sub(lambda m: f"{m.group(1)}_{m.group(2)}", camel).lower()


# ---------- transforms ----------


def case_change(schema: Schema, rng: random.Random) -> Schema:
    """Randomly convert each field name to camelCase, PascalCase, or UPPERCASE."""
    choices = ("camel", "pascal", "upper")
    new_fields = []
    for f in schema.fields:
        mode = rng.choice(choices)
        camel = _to_camel(f.name)
        if mode == "camel":
            new_name = camel
        elif mode == "pascal":
            new_name = camel[:1].upper() + camel[1:] if camel else ""
        else:
            new_name = f.name.upper()
        new_fields.append(replace(f, name=new_name))
    return replace(schema, fields=new_fields)


def snake_camel_swap(schema: Schema, rng: random.Random) -> Schema:
    """Flip snake_case fields to camelCase and vice versa."""
    new_fields = []
    for f in schema.fields:
        if "_" in f.name:
            new_fields.append(replace(f, name=_to_camel(f.name)))
        else:
            new_fields.append(replace(f, name=_to_snake(f.name)))
    return replace(schema, fields=new_fields)


def abbreviate(
    schema: Schema,
    rng: random.Random,
    table: dict[str, str],
) -> Schema:
    """Replace field names that appear in the abbreviation table."""
    new_fields = [
        replace(f, name=table.get(f.name, f.name)) for f in schema.fields
    ]
    return replace(schema, fields=new_fields)


def prefix_add(
    schema: Schema,
    rng: random.Random,
    choices: list[str],
) -> Schema:
    """Prepend a randomly chosen prefix to every field."""
    prefix = rng.choice(choices)
    new_fields = [replace(f, name=f"{prefix}_{f.name}") for f in schema.fields]
    return replace(schema, fields=new_fields)


def suffix_add(
    schema: Schema,
    rng: random.Random,
    choices: list[str],
) -> Schema:
    """Append a randomly chosen suffix to every field."""
    suffix = rng.choice(choices)
    new_fields = [replace(f, name=f"{f.name}_{suffix}") for f in schema.fields]
    return replace(schema, fields=new_fields)


def column_reorder(schema: Schema, rng: random.Random) -> Schema:
    """Shuffle the field order."""
    new_fields = list(schema.fields)
    rng.shuffle(new_fields)
    return replace(schema, fields=new_fields)


def drop_columns(
    schema: Schema,
    rng: random.Random,
    count: int = 1,
) -> tuple[Schema, list[str]]:
    """Remove up to `count` random fields.

    Returns (new_schema, dropped_canonical_names).
    """
    if len(schema.fields) <= count:
        return schema, []
    indices = sorted(rng.sample(range(len(schema.fields)), count))
    dropped_canonical = [
        schema.fields[i].canonical_name
        for i in indices
        if schema.fields[i].canonical_name is not None
    ]
    kept = [f for i, f in enumerate(schema.fields) if i not in indices]
    return replace(schema, fields=kept), dropped_canonical


def add_distractors(
    schema: Schema,
    rng: random.Random,
    pool: list[Field],
    count: int = 1,
) -> tuple[Schema, list[str]]:
    """Insert up to `count` distractor fields from the pool."""
    if not pool or count <= 0:
        return schema, []
    take = min(count, len(pool))
    added = rng.sample(pool, take)
    new_fields = list(schema.fields) + list(added)
    rng.shuffle(new_fields)
    return replace(schema, fields=new_fields), [f.name for f in added]


def dtype_string_cast(schema: Schema, rng: random.Random) -> Schema:
    """Convert non-string dtypes to string."""
    new_fields = [
        replace(f, dtype="string") if f.dtype != "string" else f
        for f in schema.fields
    ]
    return replace(schema, fields=new_fields)


def sample_typos(
    schema: Schema,
    rng: random.Random,
    rate: float = 0.15,
) -> Schema:
    """Introduce character-level typos in sample values.

    Only affects samples with length > 1. Never mutates name/canonical_name.
    """
    new_fields = []
    for f in schema.fields:
        mutated = []
        for sample in f.samples:
            if rng.random() < rate and len(sample) > 1:
                idx = rng.randrange(len(sample))
                replacement = chr(ord("a") + rng.randrange(26))
                mutated.append(sample[:idx] + replacement + sample[idx + 1:])
            else:
                mutated.append(sample)
        new_fields.append(replace(f, samples=mutated))
    return replace(schema, fields=new_fields)


# ---------- generator ----------

import hashlib
import json
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class GeneratedCase:
    id: str
    category: str
    subcategory: str
    tags: list[str]
    expected_difficulty: str
    source_fields: list[dict]
    target_fields: list[dict]
    expected_mappings: list[dict[str, str]]
    expected_unmapped_source: list[str]
    expected_unmapped_target: list[str]
    applied_transforms: list[str]


def load_synthetic_config(path: "Path | str") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _schema_from_config(entry: dict) -> Schema:
    return Schema(
        name=entry["name"],
        fields=[
            Field(
                name=f["name"],
                dtype=f["dtype"],
                samples=list(f["samples"]),
                canonical_name=f["name"],
            )
            for f in entry["fields"]
        ],
    )


def _sub_seed(global_seed: int, schema_name: str, variant_index: int) -> int:
    key = f"{global_seed}:{schema_name}:{variant_index}"
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)


def _sample_difficulty(rng: random.Random, dist: dict[str, float]) -> str:
    r = rng.random()
    cumulative = 0.0
    for name, weight in dist.items():
        cumulative += weight
        if r < cumulative:
            return name
    return "hard"


def _field_to_dict(f: Field) -> dict:
    return {"name": f.name, "dtype": f.dtype, "samples": list(f.samples)}


def _distractor_pool(cfg: dict, subcategory: str) -> list[Field]:
    per_domain = cfg["distractors"].get(subcategory, [])
    universal = cfg["distractors"].get("_universal", [])
    all_names = list(per_domain) + list(universal)
    default_str = cfg["distractor_samples"]["default_string"]
    return [
        Field(name=n, dtype="string", samples=list(default_str), canonical_name=None)
        for n in all_names
    ]


def _apply_transforms(
    target_schema: Schema,
    cfg: dict,
    difficulty: str,
    rng: random.Random,
) -> tuple[Schema, list[str], list[str], list[str]]:
    pool_names = cfg["transforms"][difficulty]
    count = cfg["transform_counts"][difficulty]
    chosen = [rng.choice(pool_names) for _ in range(count)]

    working = target_schema
    dropped_target_names: list[str] = []
    added_source_names: list[str] = []

    for name in chosen:
        if name == "case_change":
            working = case_change(working, rng)
        elif name == "snake_camel_swap":
            working = snake_camel_swap(working, rng)
        elif name == "abbreviate":
            working = abbreviate(working, rng, table=cfg["abbreviations"])
        elif name == "prefix_add":
            working = prefix_add(working, rng, choices=cfg["prefixes"])
        elif name == "suffix_add":
            working = suffix_add(working, rng, choices=cfg["suffixes"])
        elif name == "column_reorder":
            working = column_reorder(working, rng)
        elif name == "drop_columns":
            working, dropped = drop_columns(working, rng, count=1)
            dropped_target_names.extend(dropped)
        elif name == "add_distractors":
            pool = _distractor_pool(cfg, target_schema.name)
            working, added = add_distractors(working, rng, pool=pool, count=1)
            added_source_names.extend(added)
        elif name == "dtype_string_cast":
            working = dtype_string_cast(working, rng)
        elif name == "sample_typos":
            working = sample_typos(working, rng, rate=0.15)
        # Unknown transform names silently no-op

    return working, chosen, dropped_target_names, added_source_names


def generate_all_synthetic(cfg: dict) -> Iterator[GeneratedCase]:
    seed = cfg["seed"]
    variants = cfg["variants_per_schema"]
    dist = cfg["difficulty_distribution"]

    for schema_entry in cfg["schemas"]:
        target_schema = _schema_from_config(schema_entry)
        for variant_index in range(variants):
            sub = _sub_seed(seed, target_schema.name, variant_index)
            rng = random.Random(sub)
            difficulty = _sample_difficulty(rng, dist)

            source_schema, transforms, dropped_targets, _added_sources = _apply_transforms(
                target_schema, cfg, difficulty, rng
            )

            target_kept_names = {
                f.name for f in target_schema.fields if f.name not in dropped_targets
            }

            expected_mappings: list[dict[str, str]] = []
            expected_unmapped_source: list[str] = []
            mapped_target_names: set[str] = set()

            for src_f in source_schema.fields:
                if src_f.canonical_name is None:
                    expected_unmapped_source.append(src_f.name)
                elif src_f.canonical_name in target_kept_names:
                    expected_mappings.append(
                        {"source": src_f.name, "target": src_f.canonical_name}
                    )
                    mapped_target_names.add(src_f.canonical_name)
                else:
                    expected_unmapped_source.append(src_f.name)

            expected_unmapped_target = sorted(
                (target_kept_names - mapped_target_names) | set(dropped_targets)
            )

            yield GeneratedCase(
                id=f"synthetic/{target_schema.name}/{difficulty}/{variant_index}",
                category="synthetic",
                subcategory=target_schema.name,
                tags=["synthetic", difficulty] + [f"transform:{t}" for t in transforms],
                expected_difficulty=difficulty,
                source_fields=[_field_to_dict(f) for f in source_schema.fields],
                target_fields=[_field_to_dict(f) for f in target_schema.fields],
                expected_mappings=expected_mappings,
                expected_unmapped_source=expected_unmapped_source,
                expected_unmapped_target=expected_unmapped_target,
                applied_transforms=transforms,
            )


def write_generated_json(cases, output_path: "Path | str") -> None:
    cases_list = list(cases)
    data = {
        "version": 1,
        "seed": 42,
        "cases": [
            {
                "id": c.id,
                "category": c.category,
                "subcategory": c.subcategory,
                "tags": c.tags,
                "expected_difficulty": c.expected_difficulty,
                "source_fields": c.source_fields,
                "target_fields": c.target_fields,
                "expected": {
                    "mappings": c.expected_mappings,
                    "unmapped_source": c.expected_unmapped_source,
                    "unmapped_target": c.expected_unmapped_target,
                },
                "applied_transforms": c.applied_transforms,
            }
            for c in cases_list
        ],
    }
    Path(output_path).write_text(
        json.dumps(data, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
