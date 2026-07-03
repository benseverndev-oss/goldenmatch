"""Tests for MemoryConfig.table_prefix (Postgres MemoryStore backend, Task 4)."""
import pytest


def test_memory_config_table_prefix():
    from goldenmatch.config.schemas import MemoryConfig

    assert MemoryConfig().table_prefix == ""
    assert MemoryConfig(table_prefix="goldenmatch_").table_prefix == "goldenmatch_"
    with pytest.raises(Exception):
        MemoryConfig(table_prefix="bad-prefix; DROP")
