"""Tests for goldenmatch.core._logging.sanitize_for_log."""

from goldenmatch.core._logging import sanitize_for_log


def test_strips_newlines_and_carriage_returns():
    assert sanitize_for_log("a\nb\rc") == "a b c"


def test_strips_ansi_and_control_chars():
    assert sanitize_for_log("ok\x1b[31mred\x07") == "okred"


def test_truncates_long_values():
    out = sanitize_for_log("x" * 5000)
    assert len(out) <= 1000
    assert out.endswith("...")


def test_non_string_values_coerced():
    assert sanitize_for_log(0.85) == "0.85"
    from pathlib import Path
    assert sanitize_for_log(Path("a/b")) in ("a/b", "a\\b")


def test_plain_string_unchanged():
    assert sanitize_for_log("normal_file.csv") == "normal_file.csv"


def test_strips_osc_sequences():
    assert sanitize_for_log("\x1b]0;evil\x07x") == "]0;evilx"
