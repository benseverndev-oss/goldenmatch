"""Tests for `run_benchmarks.py --download-only` and its presence check.

Network-free: `_ensure_datasets` is stubbed, because what is under test is the
presence/exit-code contract around it, not the HTTP fetch it already owned.

Context: the benchmark datasets are gitignored, and `run_benchmarks.py` has
auto-pulled them since #2386. `--download-only` exists because there was no way
to get the data WITHOUT also sitting through a benchmark run -- which is what a
developer running `pytest -m benchmark` needs. It deliberately reuses
`_ensure_datasets` and the fetchers' own sentinels rather than restating where
each dataset lives; a second copy of that mapping is how you end up writing to
`Amazon-GoogleProducts/` while the runner reads `Amazon-Google/`.
"""
from __future__ import annotations

import sys

import pytest
import run_benchmarks as rb


@pytest.fixture
def run_main(monkeypatch):
    """Invoke `main()` with an argv. It parses `sys.argv` directly rather than
    taking a list, so patch that instead of widening the signature for tests."""
    def _run(*argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["run_benchmarks.py", *argv])
        return rb.main()
    return _run


class TestSentinels:
    def test_every_file_backed_dataset_has_a_sentinel(self):
        assert set(rb._DATASET_SENTINELS) == {
            "dblp-acm", "ncvr", "abt-buy", "amazon-google",
        }

    def test_product_sentinels_track_the_fetcher_spec(self):
        """Derived from `_PRODUCT_SPECS`, so a subdir rename cannot leave the
        presence check pointing at the old directory."""
        for key, spec in rb._PRODUCT_SPECS.items():
            assert rb._DATASET_SENTINELS[key] == f"{spec['subdir']}/{spec['sentinel']}"

    def test_amazon_google_lives_in_the_directory_the_runner_reads(self):
        """`_measure_product` reads `subdir`, and the published zip is named
        Amazon-GoogleProducts while the directory is Amazon-Google. Writing to
        the zip's name instead produces a second copy the runner never sees."""
        assert rb._DATASET_SENTINELS["amazon-google"] == "Amazon-Google/Amazon.csv"


class TestPresence:
    def test_missing_sentinel_is_absent(self, tmp_path):
        assert not rb._dataset_present(tmp_path, "dblp-acm")

    def test_existing_sentinel_is_present(self, tmp_path):
        f = tmp_path / "DBLP-ACM" / "DBLP2.csv"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        assert rb._dataset_present(tmp_path, "dblp-acm")

    def test_non_file_backed_datasets_are_always_present(self, tmp_path):
        """febrl3 comes from `recordlinkage` and dqbench from PyPI -- there is no
        file to be missing, so they must not be reported as unavailable."""
        assert rb._dataset_present(tmp_path, "febrl3")
        assert rb._dataset_present(tmp_path, "dqbench")


class TestDownloadOnly:
    def test_exits_1_and_names_what_is_missing(self, tmp_path, monkeypatch, capsys, run_main):
        monkeypatch.setattr(rb, "_ensure_datasets", lambda d, s: None)
        rc = run_main("--download-only", "--datasets", "dblp-acm",
                      "--datasets-dir", str(tmp_path))
        out = capsys.readouterr().out
        assert rc == 1
        assert "MISSING" in out and "dblp-acm" in out
        assert "SKIP rather than fail" in out

    def test_exits_0_when_everything_landed(self, tmp_path, monkeypatch, capsys, run_main):
        f = tmp_path / "DBLP-ACM" / "DBLP2.csv"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        monkeypatch.setattr(rb, "_ensure_datasets", lambda d, s: None)
        rc = run_main("--download-only", "--datasets", "dblp-acm",
                      "--datasets-dir", str(tmp_path))
        assert rc == 0
        assert "ok" in capsys.readouterr().out

    def test_measures_nothing(self, tmp_path, monkeypatch, run_main):
        """The point of the flag: fetch and stop. If it fell through to the
        measurement path it would cost the benchmark run it exists to avoid."""
        monkeypatch.setattr(rb, "_ensure_datasets", lambda d, s: None)
        called = []
        monkeypatch.setattr(rb, "_measure_dblp_acm",
                            lambda *a, **k: called.append("dblp") or None)
        run_main("--download-only", "--datasets", "dblp-acm",
                 "--datasets-dir", str(tmp_path))
        assert called == []

    def test_still_fetches_even_with_no_download(self, tmp_path, monkeypatch, run_main):
        """`--download-only --no-download` is contradictory; the explicit
        download-only request wins, otherwise the flag would silently no-op."""
        seen = []
        monkeypatch.setattr(rb, "_ensure_datasets", lambda d, s: seen.append(s))
        run_main("--download-only", "--no-download", "--datasets", "dblp-acm",
                 "--datasets-dir", str(tmp_path))
        assert seen == [{"dblp-acm"}]


class TestUrlEnvFallback:
    """`${{ vars.X }}` expands to the EMPTY STRING when the repo variable does
    not exist, so `os.environ.get(name, default)` returns "" and the override
    defeats the default instead of falling back to it.

    That silently skipped DBLP-ACM in every scheduled `benchmarks` run --
    "downloading from  ..." then "unknown url type: ''" -- which is why its
    quality floor is still `None` ("no trustworthy baseline recorded yet").
    Observed in run #94 (Aug 3) and still in #97 (Aug 11).
    """

    def test_unset_falls_back(self, monkeypatch):
        monkeypatch.delenv("GM_TEST_URL", raising=False)
        assert rb._url_env("GM_TEST_URL", "https://default/x.zip") == "https://default/x.zip"

    def test_empty_falls_back(self, monkeypatch):
        """THE regression. `os.environ.get(name, default)` returns "" here."""
        monkeypatch.setenv("GM_TEST_URL", "")
        assert rb._url_env("GM_TEST_URL", "https://default/x.zip") == "https://default/x.zip"

    def test_a_real_override_still_wins(self, monkeypatch):
        monkeypatch.setenv("GM_TEST_URL", "https://mirror/y.zip")
        assert rb._url_env("GM_TEST_URL", "https://default/x.zip") == "https://mirror/y.zip"

    def test_every_dataset_url_has_a_usable_default(self):
        """Module-level constants are resolved at import, so this pins that a
        blank workflow variable can never leave a dataset with no URL."""
        assert rb._DBLP_ACM_URL.startswith("https://")
        for spec in rb._PRODUCT_SPECS.values():
            assert spec["url"].startswith("https://"), spec

    def test_ncvr_keeps_its_empty_default_on_purpose(self):
        """NCVR's 4.3 GB source is not mirrored, so "unset" legitimately means
        "skip" and `_fetch_ncvr_sample` branches on the empty string. Routing it
        through `_url_env` would be a no-op, but pin the intent so nobody
        "fixes" it into a bogus URL."""
        import inspect
        src = inspect.getsource(rb)
        assert '_NCVR_SAMPLE_URL = os.environ.get("GOLDENMATCH_NCVR_SAMPLE_URL", "")' in src
