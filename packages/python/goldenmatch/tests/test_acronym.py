from goldenmatch.core.acronym import derive_initialism


def test_initialism_basic():
    assert derive_initialism("International Business Machines") == "IBM"


def test_initialism_drops_legal_form():
    assert derive_initialism("Acme Industries LLC") == "AI"


def test_initialism_single_token_is_empty():
    assert derive_initialism("IBM") == ""      # 1 token -> no block
    assert derive_initialism("") == ""


def test_initialism_registered_as_plugin():
    from goldenmatch.plugins.registry import PluginRegistry
    reg = PluginRegistry.instance()
    assert reg.has_transform("initialism")
    assert reg.get_transform("initialism").transform("International Business Machines") == "IBM"
