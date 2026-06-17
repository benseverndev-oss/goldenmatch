import goldenmatch.core.autoconfig as AC
import polars as pl
from goldenmatch.config.schemas import GoldenMatchConfig, GoldenRulesConfig


def _addr_df():
    return pl.DataFrame({"street": ["a", "b"], "city": ["c", "d"], "state": ["e", "f"],
                         "zip": ["1", "2"], "name": ["x", "y"]})


def test_hook_off_by_default_leaves_config_untouched():
    cfg = GoldenMatchConfig(golden_rules=GoldenRulesConfig(default_strategy="most_complete"))
    AC._maybe_detect_field_groups(_addr_df(), cfg)
    assert cfg.golden_rules.field_groups == []


def test_hook_detects_address_when_enabled_via_env(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP", "1")
    cfg = GoldenMatchConfig(golden_rules=GoldenRulesConfig(default_strategy="most_complete"))
    AC._maybe_detect_field_groups(_addr_df(), cfg)
    assert any(g.category == "address" for g in cfg.golden_rules.field_groups)


def test_hook_detects_when_flag_on_config(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP", raising=False)
    cfg = GoldenMatchConfig(golden_rules=GoldenRulesConfig(default_strategy="most_complete",
                                                           field_group_detection=True))
    AC._maybe_detect_field_groups(_addr_df(), cfg)
    assert any(g.category == "address" for g in cfg.golden_rules.field_groups)


def test_auto_configure_df_detects_address_group_when_env_set(monkeypatch):
    """Integration: auto_configure_df writes field_groups when env flag is on."""
    monkeypatch.setenv("GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP", "1")
    from goldenmatch.core.autoconfig import auto_configure_df
    cfg = auto_configure_df(_addr_df())
    assert cfg.golden_rules is not None
    assert any(g.category == "address" for g in cfg.golden_rules.field_groups)


def test_hook_skips_ray_dataset(monkeypatch):
    # Even with detection enabled, a Ray Dataset df is skipped (no field_groups written).
    monkeypatch.setenv("GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP", "1")
    monkeypatch.setattr("goldenmatch.distributed.is_ray_dataset", lambda d: True)
    cfg = GoldenMatchConfig(golden_rules=GoldenRulesConfig(default_strategy="most_complete"))
    AC._maybe_detect_field_groups(_addr_df(), cfg)   # _addr_df() is a polars frame but is_ray_dataset is forced True
    assert cfg.golden_rules.field_groups == []
