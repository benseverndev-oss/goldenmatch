from goldenmatch.core.acronym import derive_initialism


def test_initialism_basic():
    assert derive_initialism("International Business Machines") == "IBM"


def test_initialism_drops_legal_form():
    assert derive_initialism("Acme Industries LLC") == "AI"


def test_initialism_single_token_acronym_is_own_key():
    # Contract change: a 1-token ALL-CAPS acronym (len 2-6, alphabetic) is its
    # OWN block key, uppercased, so "IBM" co-locates with its expansion.
    assert derive_initialism("IBM") == "IBM"
    # A 1-token NON-acronym word ("Apple": not all-caps) is still no-block: one
    # initial letter is too coarse a key and would over-merge.
    assert derive_initialism("Apple") == ""
    # Lowercase 1-token (not all-caps in the original) is NOT acronym-like.
    assert derive_initialism("apple") == ""
    assert derive_initialism("") == ""


def test_initialism_strips_parenthetical_and_legal_noise():
    # Noisy real-world mention: parenthetical + trailing legal form. The
    # cleaned token stream is "International Business Machines" -> "IBM".
    assert (
        derive_initialism("International Business Machines Corporation (Armonk, NY)")
        == "IBM"
    )


def test_initialism_registered_as_plugin():
    # Self-contained: call register_transforms() (idempotent) so the test does not
    # depend on import-time registration surviving another xdist-shard test that may
    # have cleared the PluginRegistry singleton (CLAUDE.md xdist worker-isolation rule).
    from goldenmatch.core.acronym import register_transforms
    from goldenmatch.plugins.registry import PluginRegistry

    register_transforms()
    reg = PluginRegistry.instance()
    assert reg.has_transform("initialism")
    assert reg.get_transform("initialism").transform("International Business Machines") == "IBM"
