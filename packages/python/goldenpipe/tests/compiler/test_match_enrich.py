from goldenpipe.compiler.capture import _normalize_match_config


def test_blocking_keys_union_keys_and_passes():
    cfg = {
        "blocking": {
            "keys": [{"fields": ["last_name"]}],
            "passes": [{"fields": ["last_name"]}, {"fields": ["email"]}],
            "sub_block_keys": None,
        },
        "matchkeys": [{"fields": [{"field": "email"}, {"field": "first_name"}]}],
    }
    out = _normalize_match_config(cfg)
    assert out["keys"] == ["email", "last_name"]  # union, sorted, deduped
    assert out["scorer"] == {"columns": ["email", "first_name"]}  # matchkey field refs, first-seen order


def test_embedding_columns_and_missing_fields_graceful():
    cfg = {"blocking": {"keys": [], "passes": None}, "matchkeys": [{"fields": [{"columns": ["name_a", "name_b"]}]}]}
    out = _normalize_match_config(cfg)
    assert out["keys"] == []
    assert out["scorer"] == {"columns": ["name_a", "name_b"]}


def test_record_embedding_sentinel_is_skipped():
    cfg = {"matchkeys": [{"fields": [{"field": "__record__", "columns": ["full_name"]}]}]}
    out = _normalize_match_config(cfg)
    assert out["scorer"] == {"columns": ["full_name"]}  # __record__ filtered out


def test_empty_config_is_empty():
    assert _normalize_match_config({}) == {"keys": [], "scorer": {"columns": []}}
