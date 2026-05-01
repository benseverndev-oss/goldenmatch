from goldencheck.config.schema import GoldenCheckConfig, ColumnRule, RelationRule, IgnoreEntry, Settings

def test_column_rule_minimal():
    rule = ColumnRule(type="string")
    assert rule.type == "string"
    assert rule.required is None

def test_column_rule_full():
    rule = ColumnRule(type="integer", required=True, range=[0, 120], unique=False)
    assert rule.range == [0, 120]

def test_relation_rule():
    rule = RelationRule(type="temporal_order", columns=["start_date", "end_date"])
    assert len(rule.columns) == 2

def test_ignore_entry():
    entry = IgnoreEntry(column="notes", check="nullability")
    assert entry.column == "notes"

def test_full_config():
    config = GoldenCheckConfig(
        version=1,
        settings=Settings(fail_on="error"),
        columns={"email": ColumnRule(type="string", required=True, format="email")},
        relations=[RelationRule(type="temporal_order", columns=["start", "end"])],
        ignore=[IgnoreEntry(column="notes", check="nullability")],
    )
    assert config.version == 1
    assert "email" in config.columns

def test_default_settings():
    settings = Settings()
    assert settings.sample_size == 100000
    assert settings.fail_on == "error"
