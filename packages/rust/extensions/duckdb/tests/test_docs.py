"""`goldenmatch_docs()` -- the in-SQL orientation function.

The point of this UDF is that an agent on a bare SQL connection can find the
docs. So the tests assert what an agent actually needs: that it is callable
with no arguments, that the text names the authoritative sources, and that the
packaged file it reads is really shipped alongside the module.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from goldenmatch_duckdb.docs import goldenmatch_docs, register_docs_functions


@pytest.fixture
def con():
    connection = duckdb.connect()
    register_docs_functions(connection)
    yield connection
    connection.close()


def test_callable_with_no_arguments(con):
    (text,) = con.sql("SELECT goldenmatch_docs()").fetchone()
    assert isinstance(text, str)
    assert text.startswith("# goldenmatch-duckdb")


def test_names_the_authoritative_sources(con):
    (text,) = con.sql("SELECT goldenmatch_docs()").fetchone()
    # The three pointers an agent needs: this surface, the suite index, the repo.
    assert "docs.bensevern.dev/extensions/sql" in text
    assert "docs.bensevern.dev/llms.txt" in text
    assert "github.com/benseverndev-oss/goldenmatch" in text


def test_reads_the_packaged_file_not_the_fallback():
    from goldenmatch_duckdb import docs as docs_module

    llms = Path(docs_module.__file__).with_name("llms.txt")
    assert llms.is_file(), "llms.txt must ship next to docs.py"
    assert goldenmatch_docs() == llms.read_text(encoding="utf-8")


def test_fallback_still_answers_the_question(monkeypatch):
    """A missing packaged file degrades to a pointer, never to an exception."""
    monkeypatch.setattr(
        "goldenmatch_duckdb.docs._LLMS_TXT", Path("/nonexistent/llms.txt")
    )
    text = goldenmatch_docs()
    assert "docs.bensevern.dev/llms.txt" in text


def test_registered_by_the_main_register(con):
    """The UDF must come up through the normal entry point, not only directly."""
    fresh = duckdb.connect()
    try:
        from goldenmatch_duckdb.functions import register

        register(fresh)
        (text,) = fresh.sql("SELECT goldenmatch_docs()").fetchone()
        assert "goldenmatch" in text
    finally:
        fresh.close()
