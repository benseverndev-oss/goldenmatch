"""Tests for the Snowflake Cortex macros (cortex_embed_{768,1024},
cortex_cosine_similarity, cortex_l2_distance, cortex_inner_product,
cortex_complete).

Uses the same stub-Jinja harness as the other dbt-goldensuite tests --
no live dbt-core, no Snowflake account needed at unit-test time. Live
SQL smoke testing happens in the docs/snowflake-setup.md walkthrough.
"""
from __future__ import annotations

from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

_MACROS_DIR = Path(__file__).resolve().parents[1] / "macros"


class _DbtStub:
    @staticmethod
    def string_literal(s):  # noqa: ANN001
        return "'" + str(s).replace("'", "''") + "'"


class _TargetStub:
    def __init__(self, adapter_type: str) -> None:
        self.type = adapter_type


class _AdapterStub:
    def __init__(self, adapter_type, env) -> None:  # noqa: ANN001
        self._adapter_type = adapter_type
        self._env = env

    def dispatch(self, macro_name, namespace):  # noqa: ANN001, ARG002
        for cand in (f"{self._adapter_type}__{macro_name}",
                     f"default__{macro_name}"):
            if cand in self._env.globals:
                return self._env.globals[cand]
        raise RuntimeError(f"no dispatch for {macro_name}")


class _ExceptionsStub:
    @staticmethod
    def raise_compiler_error(msg):  # noqa: ANN001
        raise RuntimeError(msg)


def _build_env(adapter_type: str):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_DIR)),
        autoescape=False,
    )
    env.globals["target"] = _TargetStub(adapter_type)
    env.globals["dbt"] = _DbtStub()
    env.globals["exceptions"] = _ExceptionsStub()
    env.globals["return"] = lambda v: v
    env.globals["adapter"] = _AdapterStub(adapter_type, env)
    template = env.get_template("cortex.sql")
    module = template.module
    for name in dir(module):
        if name.startswith("_"):
            continue
        attr = getattr(module, name)
        if callable(attr):
            env.globals[name] = attr
    return env


# ---------------------------------------------------------------------------
# cortex_embed_768 / cortex_embed_1024 / cortex_embed (dim-dispatched)
# ---------------------------------------------------------------------------


def test_cortex_embed_768_default_model() -> None:
    env = _build_env("snowflake")
    fn = env.globals["cortex_embed_768"]
    sql = fn("name")
    assert "SNOWFLAKE.CORTEX.EMBED_TEXT_768" in sql
    assert "'snowflake-arctic-embed-m-v1.5'" in sql
    assert "name" in sql


def test_cortex_embed_768_custom_model() -> None:
    env = _build_env("snowflake")
    fn = env.globals["cortex_embed_768"]
    sql = fn("name", model="e5-base-v2")
    assert "'e5-base-v2'" in sql
    assert "EMBED_TEXT_768" in sql


def test_cortex_embed_1024_default_model() -> None:
    env = _build_env("snowflake")
    fn = env.globals["cortex_embed_1024"]
    sql = fn("name")
    assert "SNOWFLAKE.CORTEX.EMBED_TEXT_1024" in sql
    assert "'snowflake-arctic-embed-l-v2.0'" in sql


def test_cortex_embed_dispatches_on_dim_768() -> None:
    env = _build_env("snowflake")
    fn = env.globals["cortex_embed"]
    sql = fn("name", model="e5-base-v2", dim=768)
    assert "EMBED_TEXT_768" in sql
    assert "'e5-base-v2'" in sql


def test_cortex_embed_dispatches_on_dim_1024() -> None:
    env = _build_env("snowflake")
    fn = env.globals["cortex_embed"]
    sql = fn("name", model="nv-embed-qa-4", dim=1024)
    assert "EMBED_TEXT_1024" in sql
    assert "'nv-embed-qa-4'" in sql


def test_cortex_embed_invalid_dim_errors() -> None:
    env = _build_env("snowflake")
    fn = env.globals["cortex_embed"]
    with pytest.raises(RuntimeError, match="cortex_embed dim must be 768 or 1024"):
        fn("name", model="some-model", dim=384)


# ---------------------------------------------------------------------------
# Similarity / distance macros
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("macro,sf_fn", [
    ("cortex_cosine_similarity", "VECTOR_COSINE_SIMILARITY"),
    ("cortex_l2_distance",       "VECTOR_L2_DISTANCE"),
    ("cortex_inner_product",     "VECTOR_INNER_PRODUCT"),
])
def test_similarity_macros_snowflake(macro, sf_fn) -> None:
    env = _build_env("snowflake")
    fn = env.globals[macro]
    sql = fn("a.vec", "b.vec")
    assert sf_fn in sql
    assert "a.vec" in sql
    assert "b.vec" in sql


# ---------------------------------------------------------------------------
# cortex_complete (LLM, Snowflake-only)
# ---------------------------------------------------------------------------


def test_cortex_complete_default_model() -> None:
    env = _build_env("snowflake")
    fn = env.globals["cortex_complete"]
    sql = fn("'is this a match?'")
    assert "SNOWFLAKE.CORTEX.COMPLETE" in sql
    assert "'llama3.1-8b'" in sql


def test_cortex_complete_custom_model() -> None:
    env = _build_env("snowflake")
    fn = env.globals["cortex_complete"]
    sql = fn("'is this a match?'", model="claude-3-5-sonnet")
    assert "'claude-3-5-sonnet'" in sql


# ---------------------------------------------------------------------------
# Non-Snowflake adapters compile-error w/ a remediation hint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("macro,args", [
    ("cortex_embed_768",         ("col",)),
    ("cortex_embed_1024",        ("col",)),
    ("cortex_cosine_similarity", ("a", "b")),
    ("cortex_l2_distance",       ("a", "b")),
    ("cortex_inner_product",     ("a", "b")),
    ("cortex_complete",          ("'prompt'",)),
])
@pytest.mark.parametrize("adapter", ["postgres", "duckdb", "bigquery"])
def test_cortex_macros_non_snowflake_errors(macro, args, adapter) -> None:
    env = _build_env(adapter)
    fn = env.globals[macro]
    with pytest.raises(RuntimeError, match=r"(?s)Snowflake-only|Cortex"):
        fn(*args)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
