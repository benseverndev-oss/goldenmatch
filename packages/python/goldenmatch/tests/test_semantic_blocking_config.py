from goldenmatch.config.schemas import GoldenMatchConfig, SemanticBlockingConfig


def test_defaults_all_three_keys():
    c = SemanticBlockingConfig()
    assert set(c.keys) == {"ann", "initialism", "alias"}
    assert c.ann_model == "inhouse"
    assert c.ann_top_k == 20


def test_config_attaches_to_goldenmatch_config():
    gm = GoldenMatchConfig(semantic_blocking=SemanticBlockingConfig())
    assert gm.semantic_blocking is not None
    assert gm.semantic_blocking.keys
