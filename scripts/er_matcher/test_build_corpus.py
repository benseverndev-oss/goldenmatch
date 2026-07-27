"""Tests for `build_corpus` (Task 8 of the multi-source ER data pipeline):
blends every bundled/generated, non-eval-only source into the unified
training corpus JSONL + a provenance/license manifest. Stdlib + PyYAML
only -- box-safe, no torch/transformers/network."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path

import pytest
from build_corpus import build_corpus, main

FIX = Path(__file__).parent / "tests" / "fixtures"
LEIPZIG_FIX = FIX / "leipzig_mini"
FEBRL_FIX = FIX / "febrl_mini"
MAGELLAN_FIX = FIX / "magellan_mini"

_SOURCES_YAML = f"""
sources:
  febrl:
    loader: febrl
    mechanism: bundle
    license: MPL-1.1
    domain: person
    weight: 1.0
    kwargs:
      root: {FEBRL_FIX.as_posix()!r}

  abt_buy:
    loader: leipzig
    mechanism: bundle
    license: CC-BY
    attribution: "Leipzig DB Group; VLDB2010"
    domain: product
    weight: 1.0
    kwargs:
      root: {LEIPZIG_FIX.as_posix()!r}
      block_fields: [title]

  magellan_x:
    loader: magellan
    mechanism: fetch
    license: cite-only
    eval_only: true
    domain: product
    kwargs:
      root: {MAGELLAN_FIX.as_posix()!r}

  ncvr:
    loader: ncvr
    mechanism: fetch
    license: public-record
    eval_only: true
    domain: person
    kwargs: {{}}
"""


def _write_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(_SOURCES_YAML)
    return p


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_writes_all_three_jsonl_files_with_valid_rows(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    out_dir = tmp_path / "out"
    build_corpus(yaml_path, out_dir, seed=1)

    for split in ("train", "val", "test"):
        path = out_dir / f"{split}.jsonl"
        assert path.exists()
        rows = _read_jsonl(path)
        for row in rows:
            assert set(row) == {"a", "b", "label", "domain", "source", "dataset", "eid_a", "eid_b"}
            assert row["label"] in ("match", "no_match")


def test_eval_only_sources_excluded_from_corpus_rows(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    out_dir = tmp_path / "out"
    build_corpus(yaml_path, out_dir, seed=1)

    all_rows = [
        r for split in ("train", "val", "test") for r in _read_jsonl(out_dir / f"{split}.jsonl")
    ]
    sources_present = {r["source"] for r in all_rows}
    assert sources_present <= {"febrl", "leipzig"}
    assert "magellan" not in sources_present
    assert "ncvr" not in sources_present
    assert sources_present  # sanity: bundled sources actually produced rows


def test_manifest_records_every_source_incl_eval_only(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    out_dir = tmp_path / "out"
    manifest = build_corpus(yaml_path, out_dir, seed=1)

    assert set(manifest["sources"]) == {"febrl", "abt_buy", "magellan_x", "ncvr"}

    febrl_entry = manifest["sources"]["febrl"]
    assert febrl_entry["mechanism"] == "bundle"
    assert febrl_entry["eval_only"] is False
    assert febrl_entry["license"] == "MPL-1.1"
    assert sum(febrl_entry["counts"].values()) > 0
    # sources.yaml leaves `attribution:` unset for febrl (only CC-BY entries
    # are required to set it) -- the manifest must fall back to
    # FebrlSource's own class attribution rather than recording null.
    assert febrl_entry["attribution"] is not None
    assert "FEBRL" in febrl_entry["attribution"]

    abt_buy_entry = manifest["sources"]["abt_buy"]
    assert abt_buy_entry["license"] == "CC-BY"
    assert abt_buy_entry["attribution"] == "Leipzig DB Group; VLDB2010"
    assert sum(abt_buy_entry["counts"].values()) > 0

    magellan_entry = manifest["sources"]["magellan_x"]
    assert magellan_entry["eval_only"] is True
    assert magellan_entry["license"] == "cite-only"
    assert magellan_entry["counts"] == {"train": 0, "val": 0, "test": 0}

    ncvr_entry = manifest["sources"]["ncvr"]
    assert ncvr_entry["eval_only"] is True
    assert ncvr_entry["license"] == "public-record"
    assert ncvr_entry["counts"] == {"train": 0, "val": 0, "test": 0}


def test_manifest_totals_equal_sum_of_source_counts(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    out_dir = tmp_path / "out"
    manifest = build_corpus(yaml_path, out_dir, seed=1)

    for split in ("train", "val", "test"):
        expected = sum(v["counts"][split] for v in manifest["sources"].values())
        assert manifest["totals"][split] == expected
        actual_written = len(_read_jsonl(out_dir / f"{split}.jsonl"))
        assert manifest["totals"][split] == actual_written


def test_manifest_written_to_disk_and_matches_return_value(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    out_dir = tmp_path / "out"
    manifest = build_corpus(yaml_path, out_dir, seed=1)

    on_disk = json.loads((out_dir / "manifest.json").read_text())
    assert on_disk == manifest


def test_deterministic_same_seed_byte_identical(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    build_corpus(yaml_path, out_a, seed=42)
    build_corpus(yaml_path, out_b, seed=42)

    for split in ("train", "val", "test"):
        a_bytes = (out_a / f"{split}.jsonl").read_bytes()
        b_bytes = (out_b / f"{split}.jsonl").read_bytes()
        assert a_bytes == b_bytes


def test_cap_limits_rows_per_source_per_split(tmp_path):
    yaml_path = _write_yaml(tmp_path)

    out_uncapped = tmp_path / "uncapped"
    build_corpus(yaml_path, out_uncapped, seed=1)
    uncapped_rows = [
        r
        for split in ("train", "val", "test")
        for r in _read_jsonl(out_uncapped / f"{split}.jsonl")
    ]
    # Sanity: at least one contributing source has >1 row in some split
    # uncapped, else the cap=1 assertion below wouldn't prove anything.
    assert len(uncapped_rows) > 2

    out_capped = tmp_path / "capped"
    manifest = build_corpus(yaml_path, out_capped, seed=1, cap=1)

    for source_name, entry in manifest["sources"].items():
        for split, n in entry["counts"].items():
            assert n <= 1, f"{source_name}/{split} has {n} rows, cap=1 violated"

    for split in ("train", "val", "test"):
        rows = _read_jsonl(out_capped / f"{split}.jsonl")
        # at most one row per contributing source per split
        by_source: dict[str, int] = {}
        for r in rows:
            by_source[r["dataset"]] = by_source.get(r["dataset"], 0) + 1
        assert all(n <= 1 for n in by_source.values())


def test_cap_preserves_class_balance(tmp_path):
    """`--cap` must not silently produce an all-`match` (or all-`no_match`)
    slice: both bundled loaders emit all positives before any negatives, so
    a naive `rows[:cap]` would be label-degenerate. `abt_buy`/train has both
    a match and a no_match row uncapped (2 rows total) -- with cap=2 both
    labels must survive."""
    yaml_path = _write_yaml(tmp_path)
    out_dir = tmp_path / "capped_balance"
    build_corpus(yaml_path, out_dir, seed=1, cap=2)

    train_rows = _read_jsonl(out_dir / "train.jsonl")
    abt_buy_train = [r for r in train_rows if r["dataset"] == "abt_buy"]
    assert len(abt_buy_train) == 2
    assert {r["label"] for r in abt_buy_train} == {"match", "no_match"}

    test_rows = _read_jsonl(out_dir / "test.jsonl")
    febrl_test = [r for r in test_rows if r["dataset"] == "febrl"]
    assert len(febrl_test) == 2
    assert {r["label"] for r in febrl_test} == {"match", "no_match"}


def test_eval_only_class_attr_mismatch_raises(tmp_path):
    """If a `bundle`/`generate` sources.yaml entry forgets to set
    `eval_only: true` for a loader whose CLASS is eval_only (e.g. Magellan),
    `build_corpus` must refuse to fold its (cite-only, eval-only-by-design)
    rows into the training corpus rather than silently doing so."""
    p = tmp_path / "sources.yaml"
    p.write_text(
        f"""
sources:
  magellan_x:
    loader: magellan
    mechanism: bundle
    license: cite-only
    eval_only: false
    domain: product
    kwargs:
      root: {MAGELLAN_FIX.as_posix()!r}
"""
    )
    with pytest.raises(ValueError, match="eval_only"):
        build_corpus(p, tmp_path / "out", seed=1)


def test_manifest_label_totals_present_and_correct(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    out_dir = tmp_path / "out"
    manifest = build_corpus(yaml_path, out_dir, seed=1)

    assert set(manifest["label_totals"]) == {"train", "val", "test"}
    for split in ("train", "val", "test"):
        rows = _read_jsonl(out_dir / f"{split}.jsonl")
        expected = {"match": 0, "no_match": 0}
        for r in rows:
            expected[r["label"]] += 1
        assert manifest["label_totals"][split] == expected


def test_cli_main_writes_corpus(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    out_dir = tmp_path / "cliout"

    rc = main(["--sources", str(yaml_path), "--out-dir", str(out_dir), "--seed", "1"])

    assert rc == 0
    for split in ("train", "val", "test"):
        path = out_dir / f"{split}.jsonl"
        assert path.exists()
    assert (out_dir / "manifest.json").exists()
