"""Stage E smoke: the spill-bench driver runs end-to-end at a trivial
scale (validates the driver before the billable large-new-64GB dispatch).
Runs in the goldenmatch CI lane (datafusion + FFI UDF wheel present)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("datafusion")
pytest.importorskip("goldenmatch_datafusion_udf")

_DRIVER = (
    Path(__file__).resolve().parents[1] / "scripts" / "bench_datafusion_spine_spill.py"
)


def _load_driver():
    spec = importlib.util.spec_from_file_location("spine_spill_bench", _DRIVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["spine_spill_bench"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_spine_spill_driver_runs_tiny():
    drv = _load_driver()

    df = drv.realistic_person_df(400, seed=7)
    cfg = drv._spine_config()
    blocks = drv._build_blocks(df, cfg)

    from goldenmatch.backends.datafusion_spine import run_spine

    # Low pool to exercise the spill runtime construction path on tiny data.
    _golden, assign, raw_pairs = run_spine(blocks, cfg, memory_limit=128 * 1024 * 1024)
    assert isinstance(raw_pairs, list)
    assert assign is not None
    # NOTE: build_blocks drops soundex-singleton surnames (blocks with < 2
    # records), so `assign` covers only the post-height>=2 block universe,
    # NOT all 400 source rows. Assert the path produced a non-empty
    # assignment with at least one cluster -- the end-to-end "it ran" check.
    assert assign.height >= 1
    assert assign["cluster_id"].n_unique() >= 1


def test_spine_spill_config_is_scale_mode():
    # The Stage D gate requires mode='scale'; a regression here would make
    # every spine variant ValueError at bench time.
    drv = _load_driver()
    assert drv._spine_config().mode == "scale"
    assert drv._bucket_config().backend == "bucket"
