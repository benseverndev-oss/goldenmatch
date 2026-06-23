from scripts.autoconfig_quality.datasets import Dataset, effective_row_cap


def test_full_scan_defaults_false_and_uses_cli_cap():
    d = Dataset("x", "real", lambda: None)
    assert d.full_scan is False
    assert effective_row_cap(d, 20_000) == 20_000


def test_full_scan_true_disables_cap():
    d = Dataset("big", "real", lambda: None, full_scan=True)
    assert effective_row_cap(d, 20_000) is None
