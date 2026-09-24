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
