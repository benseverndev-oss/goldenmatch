"""The EM-block-sample is DEFAULT ON at 100k rows (validated F1-neutral: 1M
person native peak 11.7->5.7 GB with byte-identical F1; historical_50k 40%
sample identical F1). GOLDENMATCH_FS_EM_SAMPLE_ROWS=0/"" restores full-frame EM.
"""

from __future__ import annotations

import pytest
from goldenmatch.core.pipeline import (
    _FS_EM_SAMPLE_DEFAULT_ROWS,
    _fs_em_sample_rows,
)


def test_default_on_when_unset(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_EM_SAMPLE_ROWS", raising=False)
    assert _fs_em_sample_rows() == _FS_EM_SAMPLE_DEFAULT_ROWS == 100_000


@pytest.mark.parametrize("off", ["0", "", "  ", "-1"])
def test_explicit_off(monkeypatch, off):
    monkeypatch.setenv("GOLDENMATCH_FS_EM_SAMPLE_ROWS", off)
    assert _fs_em_sample_rows() is None  # full-frame EM (byte-identical)


def test_explicit_cap(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_EM_SAMPLE_ROWS", "25000")
    assert _fs_em_sample_rows() == 25_000


def test_unparseable_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_EM_SAMPLE_ROWS", "abc")
    assert _fs_em_sample_rows() == 100_000
