"""Inline file ingestion for the MCP server.

Turns a caller-supplied (file_path | file_content) pair into a concrete
server-side path. `file_content` is decoded (base64 default, text opt-in),
size-checked, and written under an uploads dir that honors
GOLDENMATCH_ALLOWED_ROOT so the existing `core._paths.safe_path` guard
still accepts the resulting path on network-exposed deployments.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

_MAX_BYTES_ENV = "GOLDENMATCH_MCP_MAX_UPLOAD_BYTES"
_TTL_ENV = "GOLDENMATCH_MCP_UPLOAD_TTL"
_ROOT_ENV = "GOLDENMATCH_ALLOWED_ROOT"
_DEFAULT_MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_TTL = 86_400
_UPLOAD_SUBDIR = "goldenmatch-uploads"
_WS = re.compile(rb"\s+")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _max_bytes() -> int:
    raw = os.environ.get(_MAX_BYTES_ENV)
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_MAX_BYTES


def _ttl() -> int:
    raw = os.environ.get(_TTL_ENV)
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_TTL


def _uploads_dir() -> str:
    root = os.environ.get(_ROOT_ENV)
    base = root if root else tempfile.gettempdir()
    d = Path(base) / _UPLOAD_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _safe_filename(name: str | None) -> str:
    if not name:
        return "upload.csv"
    base = os.path.basename(str(name)).replace("..", "")
    cleaned = _UNSAFE.sub("", base).lstrip(".")
    return cleaned or "upload.csv"


def _reap(uploads_dir: str | os.PathLike, *, ttl: int) -> None:
    cutoff = time.time() - ttl
    try:
        for entry in os.scandir(uploads_dir):
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
            except OSError:
                continue
    except OSError:
        return


def _decode(content: str, encoding: str) -> bytes:
    if encoding == "text":
        return content.encode("utf-8")
    if encoding == "base64":
        stripped = _WS.sub(b"", content.encode("ascii", "ignore"))
        try:
            return base64.b64decode(stripped, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "file_content is not valid base64; set encoding='text' for "
                "raw text"
            ) from exc
    raise ValueError("encoding must be 'base64' or 'text'")


def resolve_input_source(
    *,
    file_path: str | None,
    file_content: str | None,
    filename: str | None = None,
    encoding: str = "base64",
) -> str:
    """Return a concrete server path for (file_path | file_content).

    Exactly one of file_path/file_content must be non-empty.
    """
    if file_content:
        data = _decode(file_content, encoding)
        cap = _max_bytes()
        if len(data) > cap:
            raise ValueError(
                f"file_content ({len(data)} bytes) exceeds the "
                f"{cap}-byte cap ({_MAX_BYTES_ENV}); pass a public http(s) "
                f"URL as file_path for larger datasets"
            )
        uploads = _uploads_dir()
        _reap(uploads, ttl=_ttl())
        dest = Path(uploads) / f"{uuid.uuid4().hex}-{_safe_filename(filename)}"
        dest.write_bytes(data)
        return str(dest)
    if file_path:
        return file_path
    raise ValueError("provide file_path or file_content")
