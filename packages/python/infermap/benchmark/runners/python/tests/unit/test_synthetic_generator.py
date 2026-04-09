"""Tests for the synthetic case generator (composition + JSON output)."""
from __future__ import annotations

import json
from pathlib import Path

from infermap_bench.synthetic import (
    GeneratedCase,
    Field,
    Schema,
    generate_all_synthetic,
    load_synthetic_config,
    sample_typos,
    write_generated_json,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
CONFIG_PATH = REPO_ROOT / "benchmark" / "synthetic.config.json"


def test_config_loads():
    cfg = load_synthetic_config(CONFIG_PATH)
    assert cfg["version"] == 1
    assert len(cfg["schemas"]) == 8
    assert cfg["variants_per_schema"] == 10


def test_generate_all_produces_expected_count():
    cfg = load_synthetic_config(CONFIG_PATH)
    cases = list(generate_all_synthetic(cfg))
    assert len(cases) == 8 * 10  # 80 cases


def test_cases_have_required_fields():
    cfg = load_synthetic_config(CONFIG_PATH)
    cases = list(generate_all_synthetic(cfg))
    for case in cases:
        assert isinstance(case, GeneratedCase)
        assert case.id.startswith("synthetic/")
        assert case.category == "synthetic"
        assert case.expected_difficulty in {"easy", "medium", "hard"}
        assert len(case.source_fields) > 0
        assert len(case.target_fields) > 0
        src_names = {f["name"] for f in case.source_fields}
        mapped_src = {m["source"] for m in case.expected_mappings}
        unmapped_src = set(case.expected_unmapped_source)
        assert src_names == mapped_src | unmapped_src, (
            f"{case.id}: coverage violation — src_names={src_names} "
            f"mapped={mapped_src} unmapped={unmapped_src}"
        )


def test_easy_cases_have_mostly_mapped_fields():
    cfg = load_synthetic_config(CONFIG_PATH)
    cases = list(generate_all_synthetic(cfg))
    easy_cases = [c for c in cases if c.expected_difficulty == "easy"]
    assert len(easy_cases) > 0
    for c in easy_cases:
        assert len(c.expected_mappings) == len(c.source_fields), (
            f"{c.id}: easy case has {len(c.source_fields)} sources but only "
            f"{len(c.expected_mappings)} mappings — canonical tracker likely broken"
        )


def test_canonical_name_survives_sample_typos():
    import random as _random
    schema = Schema(
        name="customer",
        fields=[Field(name="email", dtype="string",
                      samples=["a@b.co", "c@d.co", "e@f.co"], canonical_name="email")],
    )
    out = sample_typos(schema, _random.Random(1), rate=1.0)
    assert out.fields[0].canonical_name == "email"
    assert out.fields[0].samples != schema.fields[0].samples


def test_determinism_same_seed():
    cfg = load_synthetic_config(CONFIG_PATH)
    cases_a = list(generate_all_synthetic(cfg))
    cases_b = list(generate_all_synthetic(cfg))
    assert len(cases_a) == len(cases_b)
    for a, b in zip(cases_a, cases_b):
        assert a.id == b.id
        assert a.source_fields == b.source_fields
        assert a.expected_mappings == b.expected_mappings


def test_loaded_cases_set_value_count_to_sample_length():
    """Regression guard: `_load_synthetic_cases` must set value_count to
    len(samples) on every field so ProfileScorer doesn't silently abstain
    (its gate is `source.value_count == 0 or target.value_count == 0 → None`).
    """
    from infermap_bench.cli import _load_synthetic_cases
    cases = _load_synthetic_cases(
        REPO_ROOT / "benchmark" / "cases" / "synthetic" / "generated.json"
    )
    assert len(cases) > 0
    for c in cases:
        for f in c.source_schema.fields:
            assert f.value_count == len(f.sample_values), (
                f"{c.id}: source field {f.name} has value_count={f.value_count} "
                f"but {len(f.sample_values)} samples"
            )
        for f in c.target_schema.fields:
            assert f.value_count == len(f.sample_values), (
                f"{c.id}: target field {f.name} has value_count={f.value_count} "
                f"but {len(f.sample_values)} samples"
            )


def test_write_and_reload_roundtrip(tmp_path):
    cfg = load_synthetic_config(CONFIG_PATH)
    cases = list(generate_all_synthetic(cfg))
    output = tmp_path / "generated.json"
    write_generated_json(cases, output)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["cases"]) == 80
    assert data["seed"] == cfg["seed"]
    sample = data["cases"][0]
    for key in ("id", "category", "subcategory", "tags", "expected_difficulty",
                "source_fields", "target_fields", "expected", "applied_transforms"):
        assert key in sample
    assert "mappings" in sample["expected"]
    assert "unmapped_source" in sample["expected"]
    assert "unmapped_target" in sample["expected"]
