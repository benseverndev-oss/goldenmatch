"""Path-traversal safety for the bench-gen control plane (CodeQL #290-#293).

``/download``, ``/logs`` and ``/generate`` build a filesystem path from a
caller-supplied name. The original guard only rejected ``/`` and a leading
``.``, which lets a backslash separator (Windows) or a symlink inside
``DATA_DIR`` escape the data directory. ``_safe_child`` resolves the
candidate and verifies it stays strictly under the base directory.
"""

from __future__ import annotations

from pathlib import Path

import bench_data_gen_server as srv
import pytest
from fastapi import HTTPException


def test_safe_child_accepts_simple_name(tmp_path: Path) -> None:
    assert srv._safe_child(tmp_path, "bench_1000.parquet") == (
        tmp_path.resolve() / "bench_1000.parquet"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "../escape",
        "..\\escape",  # backslash separator: the original "/" guard missed this
        "sub/file",
        "/etc/passwd",
        "",
        ".hidden",
    ],
)
def test_safe_child_rejects_traversal(tmp_path: Path, bad: str) -> None:
    with pytest.raises(HTTPException) as exc:
        srv._safe_child(tmp_path, bad)
    assert exc.value.status_code == 400


def test_safe_child_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "data"
    base.mkdir()
    try:
        (base / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported for this platform/user")
    with pytest.raises(HTTPException) as exc:
        srv._safe_child(base, "link")
    assert exc.value.status_code == 400
