"""A filtered bless would silently unbless everything it did not run.

`_cmd_bless` REPLACES `baselines/scorecard.json` -- it writes a scorecard built
only from the datasets that ran. So `bless --datasets dblp_acm` does not add
one baseline, it deletes the other six.

That failure is invisible in exactly the way this repo keeps paying for: the
next gate reports the deleted datasets under `skipped`, not `missing`, because
a dataset with no baseline is not something a gate can miss. Six checks would
stop existing and every run would still say PASS.

Pinned here rather than left to reviewer memory, because the flag combination
is one dispatch away at any time -- `bench-suggest-quality.yml` takes both
`mode` and `datasets` as free-text workflow inputs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.suggest_quality.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_bless_with_a_dataset_filter_is_refused():
    r = _run("bless", "--datasets", "dblp_acm")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "refusing" in r.stderr
    # The message has to say WHY, or the next person removes the guard to get
    # their filtered bless and rediscovers this the expensive way.
    assert "unbless" in r.stderr


def test_the_refusal_names_the_datasets_that_would_be_kept():
    """Naming them is what makes the consequence legible at the prompt."""
    r = _run("bless", "--datasets", "dblp_acm,orgs_hard")
    assert r.returncode == 2
    assert "dblp_acm" in r.stderr
    assert "orgs_hard" in r.stderr


def test_a_filter_is_still_fine_for_every_other_mode():
    """The guard must be about bless, not about filtering.

    `report --datasets` is the documented way to look at one dataset, and a
    guard that broke it would push people back toward `bless` for a cheap look.
    """
    r = _run("report", "--datasets", "dblp_acm")
    assert "refusing" not in r.stderr, r.stderr
