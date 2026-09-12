"""generate_fixture's `corruption` knob: default output unchanged, noise scales with it."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent

#: sha256 of generate(rows=600, dupe_rate=0.3, seed=42) per shape, captured from the
#: generator BEFORE the corruption knob existed (see `--print-digests` below).
EXPECTED_DIGESTS: dict[str, str] = {
    "person": "595fb203a687445017d9aa2255dc0b6f99e7f13e68fc7f2f932343af98303c03",
    "biblio": "6d871307b8e8af2d82941dfdf6459cd8460700e1f6e8c7980a8c6a92cc58ab48",
    "product": "97df953dd01d185b8be2f0f492db3f7a1689d5b6bf1fafd155030394fe60d3dc",
}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fixture_digest(gen, shape: str, tmp: Path, **kwargs) -> str:
    tmp.mkdir(parents=True, exist_ok=True)
    out, truth = tmp / f"{shape}.parquet", tmp / f"{shape}.truth.parquet"
    gen.generate(
        rows=600,
        dupe_rate=0.3,
        out=out,
        truth=truth,
        seed=42,
        batch=1_000_000,
        shape=shape,
        **kwargs,
    )
    h = hashlib.sha256()
    for path in (out, truth):
        h.update(json.dumps(pq.read_table(path).to_pydict(), sort_keys=True, default=str).encode())
    return h.hexdigest()


def _duplicate_name_noise(gen, tmp: Path, corruption: float) -> float:
    """Share of duplicate person rows whose (first_name, surname) differs from their
    cluster's canonical row (the first row written for that cluster)."""
    tmp.mkdir(parents=True, exist_ok=True)
    out, truth = tmp / "p.parquet", tmp / "p.truth.parquet"
    gen.generate(
        rows=4000,
        dupe_rate=0.4,
        out=out,
        truth=truth,
        seed=7,
        batch=1_000_000,
        shape="person",
        corruption=corruption,
    )
    rec = pq.read_table(out).to_pydict()
    clusters = pq.read_table(truth).column("cluster_id").to_pylist()
    canonical: dict[int, tuple[str, str]] = {}
    dups = differ = 0
    for cid, first, sur in zip(clusters, rec["first_name"], rec["surname"]):
        if cid not in canonical:
            canonical[cid] = (first, sur)
            continue
        dups += 1
        differ += (first, sur) != canonical[cid]
    assert dups > 0
    return differ / dups


def test_default_corruption_is_byte_identical_to_the_pre_knob_generator(tmp_path):
    gen = _load("generate_fixture")
    for shape, expected in EXPECTED_DIGESTS.items():
        assert fixture_digest(gen, shape, tmp_path / shape) == expected
        assert (
            fixture_digest(gen, shape, tmp_path / f"{shape}-explicit", corruption=1.0) == expected
        )


def test_corruption_scales_duplicate_name_noise(tmp_path):
    """KNOWN-POSITIVE: zero corruption leaves duplicate names untouched; more corruption
    changes more of them."""
    gen = _load("generate_fixture")
    none = _duplicate_name_noise(gen, tmp_path / "none", 0.0)
    low = _duplicate_name_noise(gen, tmp_path / "low", 0.5)
    high = _duplicate_name_noise(gen, tmp_path / "high", 2.0)
    assert none == 0.0
    assert 0.0 < low < high


def test_negative_corruption_is_rejected(tmp_path):
    import pytest

    gen = _load("generate_fixture")
    with pytest.raises(ValueError, match="corruption"):
        gen.generate(
            rows=10,
            dupe_rate=0.2,
            out=tmp_path / "o.parquet",
            truth=tmp_path / "t.parquet",
            seed=1,
            batch=1_000_000,
            corruption=-0.1,
        )


def test_cli_accepts_corruption(tmp_path, monkeypatch):
    gen = _load("generate_fixture")
    # Row count is deliberately not asserted here: a pre-existing clip off-by-one
    # in generate()'s batch loop (unrelated to corruption) is tracked separately.
    cli_out, cli_truth = tmp_path / "cli.parquet", tmp_path / "cli.truth.parquet"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_fixture.py",
            "--rows",
            "50",
            "--seed",
            "42",
            "--out",
            str(cli_out),
            "--ground-truth",
            str(cli_truth),
            "--corruption",
            "0",
        ],
    )
    gen.main()
    direct_out, direct_truth = tmp_path / "direct.parquet", tmp_path / "direct.truth.parquet"
    gen.generate(
        rows=50,
        dupe_rate=0.20,
        out=direct_out,
        truth=direct_truth,
        seed=42,
        batch=1_000_000,
        corruption=0.0,
    )
    assert pq.read_table(cli_out).equals(pq.read_table(direct_out))
    assert pq.read_table(cli_truth).equals(pq.read_table(direct_truth))


if __name__ == "__main__" and "--print-digests" in sys.argv:
    import tempfile

    _gen = _load("generate_fixture")
    with tempfile.TemporaryDirectory() as _td:
        for _shape in ("person", "biblio", "product"):
            print(f'    "{_shape}": "{fixture_digest(_gen, _shape, Path(_td) / _shape)}",')
