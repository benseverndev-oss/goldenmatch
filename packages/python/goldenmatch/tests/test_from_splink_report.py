from goldenmatch.config.from_splink import (
    ConversionReport,
)


def test_report_severity_filtering():
    r = ConversionReport()
    r.info("settings.sql_dialect", "ignored (engine infra)", mapped_to=None)
    r.warn("comparisons[0].levels[2]", "unrecognized SQL, level dropped", mapped_to=None)
    assert len(r.findings) == 2
    assert r.has_warnings and not r.has_errors
    assert "warning" in r.summary().lower()


def test_error_findings():
    r = ConversionReport()
    r.error("blocking_rules", "no blocking rule could be converted", mapped_to=None)
    assert r.has_errors
