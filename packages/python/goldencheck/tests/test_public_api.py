"""Test that the public API surface works as documented."""


def test_top_level_imports():
    from goldencheck import (
        scan_file,
        scan_file_with_llm,
        Finding,
        Severity,
        DatasetProfile,
        ColumnProfile,
        ScanResult,
        __version__,
    )
    assert callable(scan_file)
    assert callable(scan_file_with_llm)
    assert __version__


def test_finding_all():
    from goldencheck.models.finding import __all__ as finding_all
    assert "Finding" in finding_all
    assert "Severity" in finding_all


def test_profile_all():
    from goldencheck.models.profile import __all__ as profile_all
    assert "ColumnProfile" in profile_all
    assert "DatasetProfile" in profile_all


def test_scanner_all():
    from goldencheck.engine.scanner import __all__ as scanner_all
    assert "scan_file" in scanner_all
    assert "scan_file_with_llm" in scanner_all


def test_config_all():
    from goldencheck.config.schema import __all__ as config_all
    assert "GoldenCheckConfig" in config_all
    assert "ColumnRule" in config_all
    assert "Settings" in config_all
    assert "RelationRule" in config_all
    assert "IgnoreEntry" in config_all


def test_notebook_all():
    from goldencheck.notebook import __all__ as notebook_all
    assert "ScanResult" in notebook_all
    assert "findings_to_html" in notebook_all
    assert "profile_to_html" in notebook_all
