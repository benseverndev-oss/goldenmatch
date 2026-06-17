import yaml
from goldenmatch.config.loader import load_config


def test_loader_keeps_field_groups_out_of_field_rules(tmp_path):
    cfg = tmp_path / "g.yml"
    cfg.write_text(yaml.safe_dump({
        "golden_rules": {
            "default_strategy": "most_complete",
            "field_group_detection": True,
            "field_groups": [{"name": "addr", "columns": ["street", "city"]}],
            "phone": {"strategy": "source_priority", "source_priority": ["crm"]},
        }
    }))
    c = load_config(str(cfg))
    gr = c.golden_rules
    assert gr.field_group_detection is True
    assert gr.field_groups[0].name == "addr"
    assert "phone" in gr.field_rules
    assert "field_groups" not in gr.field_rules
