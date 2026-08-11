"""Tests for scripts/fetch_benchmark_datasets.py.

Network-free by construction. The env override each dataset already carries
(`GOLDENMATCH_DBLP_ACM_URL` and friends) accepts any URL, so pointing it at a
`file://` zip built in a tmpdir exercises the REAL download + extract + copy
path -- not a mock of it -- while never leaving the box.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import fetch_benchmark_datasets as mod
import pytest
from fetch_benchmark_datasets import (
    FETCHABLE,
    UNFETCHABLE,
    Dataset,
    fetch,
    main,
    present,
    resolve_url,
)


def _zip_of(path: Path, names, *, prefix: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for n in names:
            zf.writestr(f"{prefix}{n}", f"col\n{n}\n")
    return path


@pytest.fixture
def ds(tmp_path, monkeypatch) -> Dataset:
    """A registry-shaped dataset whose download target is redirected to tmp."""
    monkeypatch.setattr(mod, "DATASETS_DIR", tmp_path / "datasets")
    return Dataset(name="Fake-Set", url="https://example.invalid/nope.zip",
                   files=("A.csv", "B.csv"), env="GOLDENMATCH_FAKE_URL")


class TestRegistry:
    def test_names_and_env_vars_are_unique(self):
        assert len({d.name for d in FETCHABLE}) == len(FETCHABLE)
        assert len({d.env for d in FETCHABLE}) == len(FETCHABLE)

    def test_every_dataset_declares_files_and_an_https_url(self):
        for d in FETCHABLE:
            assert d.files, f"{d.name} declares no expected files"
            assert d.url.startswith("https://"), d.url

    def test_fetchable_and_unfetchable_do_not_overlap(self):
        """A name in both lists would make the CLI's error path lie."""
        assert not {d.name for d in FETCHABLE} & set(UNFETCHABLE)

    def test_the_upstream_typo_is_preserved(self):
        """`Amzon_` is the real name inside the published archive, and
        run_amazon_google_bench.py reads that exact string. Normalising it here
        would silently break the benchmark."""
        ag = next(d for d in FETCHABLE if d.name == "Amazon-GoogleProducts")
        assert "Amzon_GoogleProducts_perfectMapping.csv" in ag.files


class TestEnvOverride:
    def test_env_wins_over_the_default_url(self, monkeypatch):
        d = FETCHABLE[0]
        monkeypatch.setenv(d.env, "https://mirror.example/x.zip")
        assert resolve_url(d) == "https://mirror.example/x.zip"

    def test_unset_env_falls_back(self, monkeypatch):
        d = FETCHABLE[0]
        monkeypatch.delenv(d.env, raising=False)
        assert resolve_url(d) == d.url


class TestFetch:
    def test_downloads_and_places_every_expected_file(self, ds, tmp_path, monkeypatch):
        src = _zip_of(tmp_path / "src.zip", ds.files)
        monkeypatch.setenv(ds.env, src.as_uri())

        result = fetch(ds)
        assert result.ok, result.detail
        assert present(ds) == []

    def test_a_nested_archive_still_resolves(self, ds, tmp_path, monkeypatch):
        """Published layouts are flat, but a mirror set via the env override may
        nest the files under a directory -- hence the rglob rather than a
        fixed path."""
        src = _zip_of(tmp_path / "src.zip", ds.files, prefix="Fake-Set/data/")
        monkeypatch.setenv(ds.env, src.as_uri())
        assert fetch(ds).ok
        assert present(ds) == []

    def test_present_dataset_is_not_re_downloaded(self, ds, tmp_path, monkeypatch):
        src = _zip_of(tmp_path / "src.zip", ds.files)
        monkeypatch.setenv(ds.env, src.as_uri())
        assert fetch(ds).ok

        # Any download attempt now is a bug; make one fatal.
        def _boom(*a, **k):
            raise AssertionError("re-downloaded an already-present dataset")

        monkeypatch.setattr(mod, "_download", _boom)
        assert fetch(ds).ok
        assert "already present" in fetch(ds).detail

    def test_force_re_downloads(self, ds, tmp_path, monkeypatch):
        src = _zip_of(tmp_path / "src.zip", ds.files)
        monkeypatch.setenv(ds.env, src.as_uri())
        assert fetch(ds).ok
        calls = []
        real = mod._download
        monkeypatch.setattr(mod, "_download",
                            lambda u, d: (calls.append(u), real(u, d))[1])
        assert fetch(ds, force=True).ok
        assert len(calls) == 1

    def test_an_archive_missing_a_file_fails_loudly_and_names_it(
        self, ds, tmp_path, monkeypatch
    ):
        src = _zip_of(tmp_path / "src.zip", ["A.csv"])  # B.csv absent
        monkeypatch.setenv(ds.env, src.as_uri())
        r = fetch(ds)
        assert not r.ok
        assert "B.csv" in r.detail and r.missing == ["B.csv"]

    def test_a_download_failure_is_reported_not_raised(self, ds, monkeypatch):
        monkeypatch.setenv(ds.env, (Path("/nonexistent/nope.zip")).as_uri())
        r = fetch(ds)
        assert not r.ok
        assert "download failed" in r.detail

    def test_a_non_zip_body_is_reported_not_raised(self, ds, tmp_path, monkeypatch):
        junk = tmp_path / "junk.zip"
        junk.write_text("<html>404</html>")
        monkeypatch.setenv(ds.env, junk.as_uri())
        r = fetch(ds)
        assert not r.ok
        assert "not a zip archive" in r.detail

    def test_a_path_traversing_member_is_refused(self, ds, tmp_path, monkeypatch):
        """This unpacks a remote archive into the source tree, so zip-slip is
        checked before anything is written."""
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../../../../tmp/pwned.csv", "x")
        monkeypatch.setenv(ds.env, evil.as_uri())
        r = fetch(ds)
        assert not r.ok
        assert "escapes" in r.detail
        assert not Path("/tmp/pwned.csv").exists()


class TestCli:
    def test_list_succeeds_with_nothing_on_disk(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(mod, "DATASETS_DIR", tmp_path / "none")
        assert main(["--list"]) == 0
        out = capsys.readouterr().out
        assert "DBLP-ACM" in out and "not auto-fetchable" in out

    def test_an_unknown_name_is_a_usage_error(self, capsys):
        assert main(["No-Such-Set"]) == 2
        assert "not auto-fetchable" in capsys.readouterr().err

    def test_an_unfetchable_name_explains_itself(self, capsys):
        """Asking for NCVR should say WHY it cannot be fetched, not just list
        the valid names."""
        assert main(["NCVR"]) == 2
        assert "NC State Board of Elections" in capsys.readouterr().err

    def test_exit_1_when_a_dataset_cannot_be_had(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(mod, "DATASETS_DIR", tmp_path / "d")
        monkeypatch.setenv("GOLDENMATCH_DBLP_ACM_URL",
                           Path("/nonexistent/x.zip").as_uri())
        assert main(["DBLP-ACM"]) == 1
        assert "will SKIP, not fail" in capsys.readouterr().err
