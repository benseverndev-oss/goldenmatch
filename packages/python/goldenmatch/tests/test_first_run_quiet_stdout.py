"""A zero-config run must not print internal diagnostics to stdout.

The auto-config controller printed a dozen `[controller.run n_rows=...]` stage
timings on every run -- leftovers from hunting a 5M-row hang. That was the
first thing a new user saw from `dedupe_df`, and it landed on stdout, where it
mixes with anything the caller pipes.
"""

import pyarrow as pa


def test_zero_config_dedupe_prints_no_controller_timings(capsys):
    from goldenmatch import dedupe_df

    table = pa.table(
        {
            "name": ["Ann Lee", "Ann Leigh", "Bo Chan", "Bo Chen", "Cy Diaz", "Di Eve"],
            "email": ["ann@x.com", "ann@x.com", "bo@x.com", "bo@x.com", "cy@x.com", "di@x.com"],
            "city": ["Oslo", "Oslo", "Rome", "Rome", "Lima", "Kiev"],
        }
    )
    dedupe_df(table)
    assert "[controller.run" not in capsys.readouterr().out


def test_zero_config_file_dedupe_announces_goldencheck_once_on_stderr(tmp_path, capsys):
    """#2997: the GoldenCheck scan announced once per auto-config sample run.

    One `gm.dedupe("customers.csv")` on a 12-row file printed "GoldenCheck:
    scanning data quality..." six times, on stdout. Sample runs are now silent,
    and the one real-run announcement goes to stderr.
    """
    import csv

    import pytest
    from goldenmatch import dedupe
    from goldenmatch.core.quality import _goldencheck_available

    # The announcement comes from the fix step, which needs polars (a
    # golden-suite install carries goldenmatch[polars]); without either, nothing
    # is announced and this test would pass without testing anything.
    if not _goldencheck_available():
        pytest.skip("goldencheck not installed; nothing is announced")
    pytest.importorskip("polars")

    path = tmp_path / "customers.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "email", "city"])
        w.writerows(
            [
                ("1", "Ann Lee", "ann@x.com", "Oslo"),
                ("2", "Ann Leigh", "ann@x.com", "Oslo"),
                ("3", "Bo Chan", "bo@x.com", "Rome"),
                ("4", "Bo Chen", "bo@x.com", "Rome"),
                ("5", "Cy Diaz", "cy@x.com", "Lima"),
                ("6", "Di Eve", "di@x.com", "Kiev"),
            ]
        )
    dedupe(str(path))
    captured = capsys.readouterr()
    assert "GoldenCheck:" not in captured.out
    assert captured.err.count("GoldenCheck:") <= 1
