"""Tests for the per-source config loader + build_source factory (Task 7 of
the multi-source ER data pipeline). Stdlib + PyYAML only -- box-safe, no
torch/transformers/network."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path

import pytest
from sources.base import PairSource
from sources_config import SourceEntry, build_source, load_sources
from synthetic.generate import SyntheticSource

REAL_YAML = Path(__file__).parent / "sources.yaml"
FIX = Path(__file__).parent / "tests" / "fixtures"
LEIPZIG_FIX = FIX / "leipzig_mini"
FEBRL_FIX = FIX / "febrl_mini"
MAGELLAN_FIX = FIX / "magellan_mini"


# --- load_sources on the real sources.yaml -----------------------------------


def test_real_yaml_loads_expected_names_loaders_mechanisms():
    entries = {e.name: e for e in load_sources(REAL_YAML)}
    assert entries["febrl"].loader == "febrl"
    assert entries["febrl"].mechanism == "bundle"
    assert entries["abt_buy"].loader == "leipzig"
    assert entries["amazon_google"].loader == "leipzig"
    assert entries["dblp_acm"].loader == "leipzig"
    assert entries["dblp_scholar"].loader == "leipzig"
    assert entries["magellan_abt_buy"].loader == "magellan"
    assert entries["magellan_abt_buy"].mechanism == "fetch"
    assert entries["ncvr"].loader == "ncvr"
    assert entries["ncvr"].mechanism == "fetch"
    assert entries["synthetic"].loader == "synthetic"
    assert entries["synthetic"].mechanism == "generate"


def test_real_yaml_every_ccby_entry_has_attribution():
    entries = load_sources(REAL_YAML)
    for e in entries:
        if e.license == "CC-BY":
            assert e.attribution, f"{e.name}: CC-BY entry missing attribution"


def test_real_yaml_eval_only_set_correctly():
    entries = {e.name: e for e in load_sources(REAL_YAML)}
    assert entries["magellan_abt_buy"].eval_only is True
    assert entries["ncvr"].eval_only is True
    assert entries["febrl"].eval_only is False
    assert entries["abt_buy"].eval_only is False


def test_real_yaml_names_match_yaml_keys():
    entries = load_sources(REAL_YAML)
    names = {e.name for e in entries}
    assert names == {
        "febrl",
        "abt_buy",
        "amazon_google",
        "dblp_acm",
        "dblp_scholar",
        "magellan_abt_buy",
        "ncvr",
        "synthetic",
    }


# --- strict validation --------------------------------------------------------


def _write_yaml(tmp_path, text: str) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(text)
    return p


def test_unknown_key_in_entry_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  febrl:
    loader: febrl
    mechanism: bundle
    license: MPL-1.1
    bogus_key: true
""",
    )
    with pytest.raises(ValueError, match="unknown keys"):
        load_sources(p)


def test_bad_mechanism_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  febrl:
    loader: febrl
    mechanism: download
    license: MPL-1.1
""",
    )
    with pytest.raises(ValueError, match="mechanism"):
        load_sources(p)


def test_ccby_without_attribution_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  abt_buy:
    loader: leipzig
    mechanism: bundle
    license: CC-BY
""",
    )
    with pytest.raises(ValueError, match="attribution"):
        load_sources(p)


def test_missing_required_key_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  febrl:
    mechanism: bundle
    license: MPL-1.1
""",
    )
    with pytest.raises(ValueError, match="loader"):
        load_sources(p)


def test_non_dict_kwargs_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  febrl:
    loader: febrl
    mechanism: bundle
    license: MPL-1.1
    kwargs: not_a_dict
""",
    )
    with pytest.raises(ValueError, match="kwargs"):
        load_sources(p)


def test_eval_only_non_bool_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  febrl:
    loader: febrl
    mechanism: bundle
    license: MPL-1.1
    eval_only: flase
""",
    )
    with pytest.raises(ValueError, match="eval_only"):
        load_sources(p)


def test_weight_non_numeric_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  febrl:
    loader: febrl
    mechanism: bundle
    license: MPL-1.1
    weight: heavy
""",
    )
    with pytest.raises(ValueError, match="weight"):
        load_sources(p)


def test_weight_bool_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  febrl:
    loader: febrl
    mechanism: bundle
    license: MPL-1.1
    weight: true
""",
    )
    with pytest.raises(ValueError, match="weight"):
        load_sources(p)


def test_missing_sources_key_raises(tmp_path):
    p = _write_yaml(tmp_path, "not_sources: {}\n")
    with pytest.raises(ValueError, match="sources"):
        load_sources(p)


def test_non_mapping_entry_raises(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
sources:
  febrl: "not_a_mapping"
""",
    )
    with pytest.raises(ValueError, match="mapping"):
        load_sources(p)


# --- build_source factory -----------------------------------------------------


def test_build_source_leipzig_against_mini_fixture():
    entry = SourceEntry(
        name="abt_buy_mini",
        loader="leipzig",
        mechanism="bundle",
        license="CC-BY",
        domain="product",
        attribution="Leipzig DB Group; VLDB2010",
        kwargs={"root": LEIPZIG_FIX, "block_fields": ["title"]},
    )
    src = build_source(entry, seed=1)
    assert src.name == "abt_buy_mini"
    rows = [r for split in src.splits().values() for r in split]
    assert rows
    assert {r["label"] for r in rows} == {"match", "no_match"}


def test_build_source_febrl_against_mini_fixture():
    entry = SourceEntry(
        name="febrl_mini",
        loader="febrl",
        mechanism="bundle",
        license="MPL-1.1",
        domain="person",
        kwargs={"root": FEBRL_FIX},
    )
    src = build_source(entry, seed=1)
    assert src.name == "febrl_mini"
    rows = [r for split in src.splits().values() for r in split]
    assert rows


def test_build_source_magellan_against_mini_fixture_no_seed_needed():
    entry = SourceEntry(
        name="magellan_mini",
        loader="magellan",
        mechanism="fetch",
        license="cite-only",
        eval_only=True,
        domain="product",
        kwargs={"root": MAGELLAN_FIX},
    )
    # Magellan's constructor has no `seed` param; build_source must not pass
    # one through to it despite the factory's uniform `seed=` kwarg.
    src = build_source(entry, seed=1)
    assert src.name == "magellan_mini"
    rows = [r for split in src.splits().values() for r in split]
    assert rows
    assert src.eval_only is True


def test_build_source_ncvr_stub_raises_not_implemented():
    entry = SourceEntry(
        name="ncvr",
        loader="ncvr",
        mechanism="fetch",
        license="public-record",
        eval_only=True,
        domain="person",
        kwargs={},
    )
    src = build_source(entry, seed=1)
    assert src.name == "ncvr"
    with pytest.raises(NotImplementedError):
        src.splits()


def test_build_source_synthetic_returns_pairsource_with_rows():
    entry = SourceEntry(
        name="synthetic_mini",
        loader="synthetic",
        mechanism="generate",
        license="CC0-1.0",
        domain="multi",
        kwargs={"n_entities": 30, "profile": "light"},
    )
    src = build_source(entry, seed=1)
    assert isinstance(src, SyntheticSource)
    assert src.name == "synthetic_mini"
    rows = [r for split in src.splits().values() for r in split]
    assert rows


def test_synthetic_source_isinstance_pairsource():
    src = SyntheticSource(name="synthetic", seed=1, n_entities=30)
    assert isinstance(src, PairSource)
    assert src.name == "synthetic"
    assert hasattr(src, "splits")


def test_build_source_unknown_loader_raises():
    entry = SourceEntry(
        name="whatever",
        loader="not_a_real_loader",
        mechanism="bundle",
        license="MPL-1.1",
    )
    with pytest.raises(ValueError, match="unknown loader"):
        build_source(entry, seed=1)


def test_build_source_name_equals_entry_name_for_every_loader():
    cases = [
        SourceEntry(
            name="n1",
            loader="leipzig",
            mechanism="bundle",
            license="CC-BY",
            attribution="x",
            kwargs={"root": LEIPZIG_FIX, "block_fields": ["title"]},
        ),
        SourceEntry(
            name="n2",
            loader="febrl",
            mechanism="bundle",
            license="MPL-1.1",
            kwargs={"root": FEBRL_FIX},
        ),
        SourceEntry(
            name="n3",
            loader="magellan",
            mechanism="fetch",
            license="cite-only",
            kwargs={"root": MAGELLAN_FIX},
        ),
        SourceEntry(name="n4", loader="ncvr", mechanism="fetch", license="public-record"),
    ]
    for entry in cases:
        src = build_source(entry, seed=0)
        assert src.name == entry.name
