import pytest
from goldenmatch.config.schemas import DistributedRoutingConfig, GoldenMatchConfig
from pydantic import ValidationError


def test_defaults_are_auto_and_no_slow_path():
    cfg = GoldenMatchConfig()
    assert cfg.allow_slow_path is False
    assert cfg.distributed_routing is None


def test_nested_routing_config_parses():
    cfg = GoldenMatchConfig.model_validate({
        "allow_slow_path": True,
        "distributed_routing": {"clustering": "in_memory_scipy", "scoring": "distributed"},
    })
    assert cfg.allow_slow_path is True
    assert cfg.distributed_routing.clustering == "in_memory_scipy"
    assert cfg.distributed_routing.scoring == "distributed"
    assert cfg.distributed_routing.golden == "auto"


def test_rejects_bad_enum():
    with pytest.raises(ValidationError):
        DistributedRoutingConfig(clustering="turbo")
