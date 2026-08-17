"""#2457: the DBLP-ACM adapter must read whichever frame type the lane returns.

`match_df` returns a `pyarrow.Table` on the arrow lane and a `pl.DataFrame` on
the classic one. This adapter reads `.height` and `.iter_rows(named=True)`,
which are polars-only, so an arrow result raised

    AttributeError: 'pyarrow.lib.Table' object has no attribute 'height'

and took the whole scheduled `benchmarks` lane red. Nothing gated it: the lane
is weekly, is not in `ci-required`, and no test exercised the adapter with an
arrow frame.

The load-bearing assertion is that both lanes produce the SAME score. A test
that only checked "arrow input does not raise" would pass on an adapter that
silently found zero pairs, which is the failure this benchmark exists to
detect.
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from leipzig_eval import _as_polars, run_dblp_acm_zeroconfig  # noqa: E402


class _Result:
    """Minimal stand-in for what `match_df` returns."""

    def __init__(self, matched):
        self.matched = matched


_PAIRS = {"__target_row_id__": [0, 1], "__ref_row_id__": [2, 3]}


def _corpus(tmp_path: Path) -> Path:
    """The three CSVs the adapter needs, with a mapping it can score against."""
    d = tmp_path / "DBLP-ACM"
    d.mkdir(parents=True)
    pl.DataFrame({"id": ["conf/vldb/a", "conf/vldb/b"],
                  "title": ["alpha", "beta"]}).write_csv(d / "DBLP2.csv")
    pl.DataFrame({"id": ["100", "200"],
                  "title": ["alpha", "beta"]}).write_csv(d / "ACM.csv")
    pl.DataFrame({"idDBLP": ["conf/vldb/a", "conf/vldb/b"],
                  "idACM": ["100", "200"]}).write_csv(
        d / "DBLP-ACM_perfectMapping.csv")
    return tmp_path


# ── the coercion itself ───────────────────────────────────────────────────

def test_arrow_table_becomes_polars():
    out = _as_polars(pa.table(_PAIRS))
    assert isinstance(out, pl.DataFrame)
    assert out.height == 2


def test_polars_passes_through_unchanged():
    df = pl.DataFrame(_PAIRS)
    assert _as_polars(df) is df


def test_none_stays_none():
    assert _as_polars(None) is None


# ── the adapter reads both lanes identically ──────────────────────────────

@pytest.mark.parametrize("wrap", [pl.DataFrame, pa.table], ids=["polars", "arrow"])
def test_adapter_scores_both_frame_types(tmp_path, wrap):
    """Arrow was an AttributeError before; both must now score the same."""
    root = _corpus(tmp_path)
    res = run_dblp_acm_zeroconfig(root, lambda a, b: _Result(wrap(_PAIRS)))
    assert res is not None
    assert res.ground_truth_pairs == 2
    assert res.found_pairs == 2
    assert res.f1 == pytest.approx(1.0)


def test_both_lanes_agree_exactly(tmp_path):
    """The regression this pins: a silently-empty arrow read would still
    return a LeipzigResult, just with f1=0. Compare the two directly."""
    root = _corpus(tmp_path)
    a = run_dblp_acm_zeroconfig(root, lambda x, y: _Result(pl.DataFrame(_PAIRS)))
    b = run_dblp_acm_zeroconfig(root, lambda x, y: _Result(pa.table(_PAIRS)))
    assert a == b


def test_absent_corpus_returns_none(tmp_path):
    """The lane's legitimate skip path must keep working."""
    assert run_dblp_acm_zeroconfig(tmp_path, lambda x, y: _Result(None)) is None
